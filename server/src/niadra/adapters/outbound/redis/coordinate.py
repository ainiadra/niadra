"""Coordination on the state store (24.3.4): ownership where the most restrictive claim wins, suppressions,
contact and paid budgets reserved atomically, with their funnel slices, and effects exactly once with
`unknown_outcome`.

Each behavior is one Lua script, and the scripts are the specification the check is built on: a decision
never reads a value in one call and writes it in another. A key is `co:<space>:{<tag>}:<what>`: the tag is the
subject's, the object's or the effect's keyed hash, so in cluster mode every script touches one slot, and a
space purge finds every key by its space. Times come from the application clock as arguments (`now_ms`) and
expiries are relative, so the store's own clock never enters a decision.

The store keeps nothing on disk. `coord:marker` says whether it holds everything the durable record does:
`whole`, or `r:<since>` from the moment a caller found it missing. Until the loops process reloads every
space and seals it, a target is loaded from the durable record the first time it is touched, and its
`loaded` key remembers the life it was loaded in.
"""

import json
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

from redis.asyncio import Redis

from niadra.adapters.outbound.redis.errors import translated
from niadra.domain.coordinate.model import (
    Admission,
    Claim,
    ClaimKind,
    ClaimResult,
    ClaimVia,
    Decision,
    EffectAttempt,
    EffectSnapshot,
    EffectState,
    Settler,
    StoreLife,
    SubjectCheck,
)
from niadra.domain.coordinate.window import Window, WindowedSuppression
from niadra.ports.coordination import BudgetSpend, ClaimSlot, CommitmentEntry

MARKER_KEY = "coord:marker"
REBUILD_KEY = "coord:rebuild"
WHOLE = "whole"
LOADED_KEEP_MS = 24 * 3600 * 1000
SPACE_TAG = "space"
"""The tag of what a space's budgets count as a whole: a paid operation with the `space` scope."""

# The claim that holds a target now: among the unexpired ones, the lowest rank (the most restrictive
# level), then the latest, then the slot name, so the answer never depends on the hash's order. A task lock
# (slot `t:<task>`) holds only its task, and the end of an observation (`x`) holds nothing.
_WINNER = """
local function winner(key, now, except_holder)
  local best, best_slot, best_s = nil, nil, -1
  local fields = redis.call('HGETALL', key)
  for i = 1, #fields, 2 do
    local slot = fields[i]
    if slot ~= '#epoch' and string.sub(slot, 1, 2) ~= 't:' then
      local c = cjson.decode(fields[i + 1])
      if c.u > now and c.h ~= except_holder and not c.x then
        local later = c.s > best_s or (c.s == best_s and slot < best_slot)
        if best == nil or c.r < best.r or (c.r == best.r and later) then
          best, best_slot, best_s = c, slot, c.s
        end
      end
    end
  end
  if best then best.slot = best_slot end
  return best
end

local function next_epoch(key, now)
  local epoch = math.max((tonumber(redis.call('HGET', key, '#epoch')) or 0) + 1, now)
  redis.call('HSET', key, '#epoch', string.format('%d', epoch))
  return string.format('%d', epoch)
end

-- Drops the expired claims and keeps the key as long as its longest claim.
local function tidy(key, now)
  local longest = 0
  local fields = redis.call('HGETALL', key)
  for i = 1, #fields, 2 do
    if fields[i] ~= '#epoch' then
      local c = cjson.decode(fields[i + 1])
      if c.u <= now then redis.call('HDEL', key, fields[i]) elseif c.u > longest then longest = c.u end
    end
  end
  if longest > now then redis.call('PEXPIRE', key, longest - now) else redis.call('DEL', key) end
end

local function encoded(c)
  if c == nil then return '' end
  return cjson.encode(c)
end
"""

# A claim from one source on one target. A declaration (ARGV[11] = '1') is refused, and nothing is written,
# while another holder's unexpired claim is at least as restrictive: a softer claim never demotes a stricter
# one. A task lock (ARGV[13] = '1') is refused while another holder has the task. An observation is a fact a
# system reported: always recorded in its source's slot, unless the slot holds something the source saw
# later (ARGV[12] is when it saw this one); the most restrictive valid claim still wins the read.
CLAIM = (
    _WINNER
    + """
local now, since = tonumber(ARGV[1]), tonumber(ARGV[12])
local rank = tonumber(ARGV[3])
local until_ms = tonumber(ARGV[8])
if until_ms <= now then return {'expired', '', ''} end
local held = redis.call('HGET', KEYS[1], ARGV[2])
if ARGV[13] == '1' then
  if held then
    local c = cjson.decode(held)
    if c.u > now and c.h ~= ARGV[5] and not c.x then
      c.slot = ARGV[2]
      return {'held', '', cjson.encode(c)}
    end
  end
elseif ARGV[11] == '1' then
  local other = winner(KEYS[1], now, ARGV[5])
  if other and other.r <= rank then return {'held', '', encoded(other)} end
elseif held and cjson.decode(held).s > since then
  return {'stale', '', encoded(winner(KEYS[1], now, ''))}
end
local epoch = next_epoch(KEYS[1], now)
redis.call('HSET', KEYS[1], ARGV[2], cjson.encode({
  r = rank, l = ARGV[4], h = ARGV[5], k = ARGV[6], v = ARGV[7], s = since, u = until_ms, e = epoch,
  c = ARGV[9], i = cjson.decode(ARGV[10])
}))
tidy(KEYS[1], now)
return {'ok', epoch, encoded(winner(KEYS[1], now, ''))}
"""
)

# What a source's observation ended at ARGV[2]: its slot holds a tombstone until ARGV[4], so an older
# observation that arrives late never brings the claim back; one the source saw later stays.
END_SLOT = (
    _WINNER
    + """
local now, at = tonumber(ARGV[1]), tonumber(ARGV[2])
local raw = redis.call('HGET', KEYS[1], ARGV[3])
local ended = ''
if raw then
  local c = cjson.decode(raw)
  if c.s > at then return {'stale', '', encoded(winner(KEYS[1], now, '')), ''} end
  if not c.x and c.u > now then
    c.slot = ARGV[3]
    ended = cjson.encode(c)
  end
end
local epoch = next_epoch(KEYS[1], now)
redis.call('HSET', KEYS[1], ARGV[3], cjson.encode({s = at, u = tonumber(ARGV[4]), x = 1, e = epoch}))
tidy(KEYS[1], now)
return {'ok', epoch, encoded(winner(KEYS[1], now, '')), ended}
"""
)

# The task locks of an object that have not expired.
LOCKS = """
local now = tonumber(ARGV[1])
local out = {}
local fields = redis.call('HGETALL', KEYS[1])
for i = 1, #fields, 2 do
  if string.sub(fields[i], 1, 2) == 't:' then
    local c = cjson.decode(fields[i + 1])
    if c.u > now and not c.x then
      c.slot = fields[i]
      out[#out + 1] = cjson.encode(c)
    end
  end
end
return out
"""

# Only the holder of the epoch a claim was written with can release it (the fencing check). The answer
# carries the claim it released.
RELEASE = (
    _WINNER
    + """
local now = tonumber(ARGV[1])
local raw = redis.call('HGET', KEYS[1], ARGV[2])
if not raw then return {'missing', '', encoded(winner(KEYS[1], now, '')), ''} end
local c = cjson.decode(raw)
if c.e ~= ARGV[3] then return {'fenced', c.e, encoded(winner(KEYS[1], now, '')), ''} end
redis.call('HDEL', KEYS[1], ARGV[2])
local epoch = next_epoch(KEYS[1], now)
tidy(KEYS[1], now)
c.slot = ARGV[2]
return {'ok', epoch, encoded(winner(KEYS[1], now, '')), cjson.encode(c)}
"""
)

OWNER = (
    _WINNER
    + """
return encoded(winner(KEYS[1], tonumber(ARGV[1]), ''))
"""
)

# The commitments of one subject, one field per commitment: `st` is `active` while it holds and
# `superseded` once another held first. A new one meets those that hold by its type's compatibility: the
# first of a type holds, the best (the higher `s`) of a type wins, an exclusive one holds alone. The same
# commitment declared again answers what it got the first time.
_COMMITMENTS = """
local function keep_commitments(key, now)
  local longest = 0
  local fields = redis.call('HGETALL', key)
  for i = 1, #fields, 2 do
    local u = cjson.decode(fields[i + 1]).u
    if u <= now then redis.call('HDEL', key, fields[i]) elseif u > longest then longest = u end
  end
  if longest > now then redis.call('PEXPIRE', key, longest - now) else redis.call('DEL', key) end
end
"""

COMMIT = (
    _COMMITMENTS
    + """
local now = tonumber(ARGV[1])
local held = redis.call('HGET', KEYS[1], ARGV[2])
if held then return {cjson.decode(held).st, '[]'} end
local new = cjson.decode(ARGV[3])
-- A commitment without a score ranks as 0; JSON's null decodes to cjson.null, not nil.
local function score(x) if x == nil or x == cjson.null then return 0 end return x end
local holds, beaten = true, {}
local fields = redis.call('HGETALL', KEYS[1])
for i = 1, #fields, 2 do
  local c = cjson.decode(fields[i + 1])
  if c.st == 'active' and c.u > now then
    if new.c == 'exclusive' or c.c == 'exclusive' then
      holds = false
    elseif c.t == new.t then
      if new.c ~= 'best_wins' or score(c.s) >= score(new.s) then
        holds = false
      else
        beaten[#beaten + 1] = {fields[i], c}
      end
    end
  end
end
local superseded = {}
if holds then
  for _, pair in ipairs(beaten) do
    pair[2].st = 'superseded'
    redis.call('HSET', KEYS[1], pair[1], cjson.encode(pair[2]))
    superseded[#superseded + 1] = pair[2].i
  end
end
new.st = holds and 'active' or 'superseded'
redis.call('HSET', KEYS[1], ARGV[2], cjson.encode(new))
keep_commitments(KEYS[1], now)
return {new.st, cjson.encode(superseded)}
"""
)

# A commitment that no longer holds (withdrawn, accepted, declined) leaves the subject's list.
UNCOMMIT = (
    _COMMITMENTS
    + """
local held = redis.call('HGET', KEYS[1], ARGV[2])
if not held then return '' end
redis.call('HDEL', KEYS[1], ARGV[2])
keep_commitments(KEYS[1], tonumber(ARGV[1]))
return cjson.decode(held).i
"""
)

COMMITMENTS = """
local now = tonumber(ARGV[1])
local out = {}
local fields = redis.call('HGETALL', KEYS[1])
for i = 1, #fields, 2 do
  local c = cjson.decode(fields[i + 1])
  if c.st == 'active' and c.u > now then out[#out + 1] = fields[i] out[#out + 1] = fields[i + 1] end
end
return out
"""

# A budget is a sliding window: a sorted set of reservation ids by time and a hash of their units. A
# reservation made at `t` counts from `t` until just before `t + window`. A reservation id already present is
# the same call again, answered without counting it twice.
_SPEND = """
local function window_use(zkey, hkey, now, window)
  local old = redis.call('ZRANGEBYSCORE', zkey, '-inf', string.format('%d', now - window))
  if #old > 0 then
    redis.call('ZREM', zkey, unpack(old))
    redis.call('HDEL', hkey, unpack(old))
  end
  local members = redis.call('ZRANGE', zkey, 0, -1, 'WITHSCORES')
  local used = 0
  for i = 1, #members, 2 do used = used + (tonumber(redis.call('HGET', hkey, members[i])) or 0) end
  return used, members
end

-- The earliest time enough of the window's units leave it for `units` more to fit.
local function retry_at(hkey, members, used, units, limit, window)
  local freed = 0
  for i = 1, #members, 2 do
    freed = freed + (tonumber(redis.call('HGET', hkey, members[i])) or 0)
    if used - freed + units <= limit then return tonumber(members[i + 1]) + window end
  end
  return 0
end

local function spend(zkey, hkey, now, id, units, limit, window)
  local used, members = window_use(zkey, hkey, now, window)
  if redis.call('ZSCORE', zkey, id) then return true, math.max(limit - used, 0), 0 end
  if used + units > limit then
    return false, math.max(limit - used, 0), retry_at(hkey, members, used, units, limit, window)
  end
  redis.call('ZADD', zkey, now, id)
  redis.call('HSET', hkey, id, units)
  redis.call('PEXPIRE', zkey, window)
  redis.call('PEXPIRE', hkey, window)
  return true, limit - used - units, 0
end
"""

# A subject's side of a check, in the order the coordination spec (3.6) takes: a suppression of the purpose
# on the channel denies; another holder's claim that does not allow the intent defers, and so does what
# blocks the object (ARGV[12] to ARGV[14]); an exhausted budget, or the intent's slice of it, denies (never a
# deferral); a spread slice defers until its next unit frees; the quiet hours defer; and only then the budget
# and the slice are spent. A limit below zero means the purpose has no budget, and a slice limit below zero no
# slice. An inbound message is never denied: it hands off to another holder. A suppression field is
# `<purpose>|<channel or *>|<source>` and its value `<until_ms or 0>|<id>`, or `<until_ms or 0>|<id>|<window>`
# for one that holds only in its local hours: those the caller weighs, in its time zones, and passes as the
# end of the window `now` falls in (ARGV[17]), which defers. A suppression of purpose `any` holds for every
# purpose.
CHECK = (
    _WINNER
    + _SPEND
    + """
local now = tonumber(ARGV[1])
local id, units, limit, window = ARGV[2], tonumber(ARGV[3]), tonumber(ARGV[4]), tonumber(ARGV[5])
local purpose, channel, agent, intent = ARGV[6], ARGV[7], ARGV[8], ARGV[9]
local quiet_until = tonumber(ARGV[10])
local blocked_by, blocked_until, blocked_holder = ARGV[12], tonumber(ARGV[13]), ARGV[14]
local slice_limit, spacing = tonumber(ARGV[15]), tonumber(ARGV[16])
local window_until = tonumber(ARGV[17])
local out = {decision = 'allow', reason = '', remaining = -1, retry = 0, suppressed = {}}
local suppressed = false
local sup = redis.call('HGETALL', KEYS[3])
for i = 1, #sup, 2 do
  local p, ch = string.match(sup[i], '^([^|]+)|([^|]+)|')
  local until_ms = tonumber(string.match(sup[i + 1], '^(%d+)'))
  local windowed = string.match(sup[i + 1], '^%d+|[^|]+|.') ~= nil
  local live = until_ms == 0 or until_ms > now
  if p and not windowed and live and (ch == '*' or channel == '' or ch == channel) then
    out.suppressed[#out.suppressed + 1] = p
    if p == purpose or p == 'any' then suppressed = true end
  end
end
local owner = winner(KEYS[4], now, '')
if owner then out.owner = owner end
local blocks = false
if owner and owner.h ~= agent then
  blocks = true
  for _, name in ipairs(owner.i) do if name == intent then blocks = false end end
end
if ARGV[11] == 'inbound' then
  if blocks then
    out.decision, out.reason, out.holder = 'handoff_to', 'owner_active', owner.h
  elseif blocked_by ~= '' then
    out.decision, out.reason, out.holder = 'handoff_to', blocked_by, blocked_holder
  end
  return cjson.encode(out)
end
local used, members = 0, {}
if limit >= 0 then
  used, members = window_use(KEYS[1], KEYS[2], now, window)
  out.remaining = math.max(limit - used, 0)
end
local slice_used, slice_members, last = 0, {}, 0
if slice_limit >= 0 then
  slice_used, slice_members = window_use(KEYS[5], KEYS[6], now, window)
  if #slice_members > 0 then last = tonumber(slice_members[#slice_members]) end
  out.remaining = math.min(out.remaining, math.max(slice_limit - slice_used, 0))
end
local again = redis.call('ZSCORE', KEYS[1], id)
if suppressed then
  out.decision, out.reason = 'deny', 'suppressed'
elseif blocks then
  out.decision, out.reason, out.retry = 'defer', 'owner_active', owner.u
elseif blocked_by ~= '' then
  out.decision, out.reason, out.retry = 'defer', blocked_by, blocked_until
elseif limit >= 0 and not again and used + units > limit then
  out.decision, out.reason = 'deny', 'budget_exhausted'
  out.retry = retry_at(KEYS[2], members, used, units, limit, window)
elseif slice_limit >= 0 and not again and slice_used + units > slice_limit then
  out.decision, out.reason = 'deny', 'budget_exhausted'
  out.retry = retry_at(KEYS[6], slice_members, slice_used, units, slice_limit, window)
elseif spacing > 0 and not again and last > 0 and last + spacing > now then
  out.decision, out.reason, out.retry = 'defer', 'budget_paced', last + spacing
elseif window_until > now then
  out.decision, out.reason, out.retry = 'defer', 'contact_window', window_until
elseif quiet_until > now then
  out.decision, out.reason, out.retry = 'defer', 'quiet_hours', quiet_until
elseif limit >= 0 then
  local _, remaining = spend(KEYS[1], KEYS[2], now, id, units, limit, window)
  out.remaining = remaining
  if slice_limit >= 0 then
    local _, left = spend(KEYS[5], KEYS[6], now, id, units, slice_limit, window)
    out.remaining = math.min(remaining, left)
  end
end
return cjson.encode(out)
"""
)

# A paid call or search against its budget, with no suppression or owner to consult.
SPEND = (
    _SPEND
    + """
local ok, remaining, retry = spend(KEYS[1], KEYS[2], tonumber(ARGV[1]), ARGV[2], tonumber(ARGV[3]),
  tonumber(ARGV[4]), tonumber(ARGV[5]))
if ok then return {'allow', '', string.format('%d', remaining), '0'} end
return {'deny', 'budget_exhausted', string.format('%d', remaining), string.format('%d', retry)}
"""
)

# What each of a subject's budget windows holds now, for a read that only advises: KEYS are a window's sorted
# set and units hash per budget, ARGV its length after `now`.
BUDGETS_USED = (
    _SPEND
    + """
local now = tonumber(ARGV[1])
local out = {}
for i = 1, #KEYS, 2 do
  local used = window_use(KEYS[i], KEYS[i + 1], now, tonumber(ARGV[(i + 1) / 2 + 1]))
  out[#out + 1] = used
end
return out
"""
)

# Settles a reservation's units once the real cost is known (a paid call that failed keeps what it
# reserved), or cancels it (ARGV[2] below zero) when the reservation was never confirmed.
SETTLE_UNITS = """
if not redis.call('ZSCORE', KEYS[1], ARGV[1]) then return 0 end
if tonumber(ARGV[2]) < 0 then
  redis.call('ZREM', KEYS[1], ARGV[1])
  redis.call('HDEL', KEYS[2], ARGV[1])
else
  redis.call('HSET', KEYS[2], ARGV[1], ARGV[2])
end
return 1
"""

# The highest cost measured for a paid operation, which an attempt of unknown cost reserves.
RAISE_MAX = """
if (tonumber(redis.call('HGET', KEYS[1], ARGV[1])) or 0) < tonumber(ARGV[2]) then
  redis.call('HSET', KEYS[1], ARGV[1], ARGV[2])
end
return 1
"""

# Suppressions of one subject, one field per purpose, channel and source. The same source suppressing again
# keeps the entry's id; the key lives as long as its longest entry, or for good.
_SUPPRESSIONS_TTL = """
local function keep(key, now)
  local longest = 0
  local fields = redis.call('HGETALL', key)
  for i = 2, #fields, 2 do
    local until_ms = tonumber(string.match(fields[i], '^(%d+)'))
    if until_ms == 0 then longest = -1 break end
    if until_ms > longest then longest = until_ms end
  end
  if longest < 0 then redis.call('PERSIST', key)
  elseif longest > now then redis.call('PEXPIRE', key, longest - now)
  else redis.call('DEL', key) end
end
"""

SUPPRESS = (
    _SUPPRESSIONS_TTL
    + """
local current = redis.call('HGET', KEYS[1], ARGV[1])
local id = ARGV[3]
if current then id = string.match(current, '^%d+|([^|]+)') end
local value = ARGV[2] .. '|' .. id
if ARGV[5] ~= '' then value = value .. '|' .. ARGV[5] end
redis.call('HSET', KEYS[1], ARGV[1], value)
keep(KEYS[1], tonumber(ARGV[4]))
return id
"""
)

LIFT = (
    _SUPPRESSIONS_TTL
    + """
local current = redis.call('HGET', KEYS[1], ARGV[1])
if not current then return '' end
redis.call('HDEL', KEYS[1], ARGV[1])
keep(KEYS[1], tonumber(ARGV[2]))
return string.match(current, '^%d+|([^|]+)')
"""
)

# An effect's key, reserved once per attempt. A reservation whose lease lapsed without a settlement is
# ambiguous (the effect may have gone out) and becomes `unknown_outcome`, which no reservation ever turns
# back into an attempt: only a settlement to `failed`, by an observation or a person, opens a new one.
# Every script answers with the effect's fields after the call, which the durable record stores.
_LAPSE = """
local function lapse(key, now)
  local state = redis.call('HGET', key, 'state')
  if state == 'reserved' then
    local lease = redis.call('HGET', key, 'lease_until')
    if tonumber(lease) <= now then
      redis.call('HSET', key, 'state', 'unknown_outcome', 'settled_at', lease)
      return 'unknown_outcome', 1
    end
  end
  return state, 0
end

local function keep(key, ttl)
  if tonumber(ttl) > 0 then redis.call('PEXPIRE', key, ttl) else redis.call('PERSIST', key) end
end
"""

PEEK_EFFECT = (
    _LAPSE
    + """
local state, lapsed = lapse(KEYS[1], tonumber(ARGV[1]))
return {lapsed, redis.call('HGETALL', KEYS[1])}
"""
)

# ARGV: now, lease_ms, ttl_ms (0 for as long as the fact exists), kind, source, agent, cost units, fact.
RESERVE_EFFECT = (
    _LAPSE
    + """
local now = tonumber(ARGV[1])
local state, lapsed = lapse(KEYS[1], now)
if state == 'reserved' then return {'in_flight', lapsed, redis.call('HGETALL', KEYS[1])} end
if state == 'done' or state == 'unknown_outcome' then
  return {state, lapsed, redis.call('HGETALL', KEYS[1])}
end
local attempt = (tonumber(redis.call('HGET', KEYS[1], 'attempt')) or 0) + 1
if state == 'failed' then
  -- The failed attempt, kept so a reservation the durable record never took can be undone to it.
  local was = redis.call('HMGET', KEYS[1], 'reserved_at', 'lease_until', 'settled_at', 'settled_by', 'source',
    'agent')
  redis.call('HSET', KEYS[1], 'prev', cjson.encode(was))
end
redis.call('HDEL', KEYS[1], 'settled_at', 'settled_by', 'done_at')
redis.call('HSET', KEYS[1], 'state', 'reserved', 'attempt', attempt, 'reserved_at', ARGV[1],
  'lease_until', string.format('%d', now + tonumber(ARGV[2])), 'kind', ARGV[4], 'source', ARGV[5],
  'agent', ARGV[6], 'cost', ARGV[7], 'fact', ARGV[8], 'ttl', ARGV[3])
keep(KEYS[1], ARGV[3])
return {'reserved', lapsed, redis.call('HGETALL', KEYS[1])}
"""
)

# Undoes the reservation of attempt ARGV[1] when the durable record never took it: the key goes back to
# nothing, or to the failed attempt before it.
CANCEL_EFFECT = """
local state, attempt = unpack(redis.call('HMGET', KEYS[1], 'state', 'attempt'))
if state ~= 'reserved' or attempt ~= ARGV[1] then return 0 end
local prev = redis.call('HGET', KEYS[1], 'prev')
if not prev then redis.call('DEL', KEYS[1]) return 1 end
local was = cjson.decode(prev)
redis.call('HDEL', KEYS[1], 'prev', 'settled_at', 'settled_by')
redis.call('HSET', KEYS[1], 'state', 'failed', 'attempt', tonumber(ARGV[1]) - 1, 'reserved_at', was[1],
  'lease_until', was[2], 'source', was[5], 'agent', was[6])
if was[3] then redis.call('HSET', KEYS[1], 'settled_at', was[3]) end
if was[4] then redis.call('HSET', KEYS[1], 'settled_by', was[4]) end
return 1
"""

# The transitions of an effect (the coordination spec, 7.2): the reserver settles its own attempt; an
# observation settles an attempt in flight, an ambiguous one, or confirms a failed one late; a person
# decides an ambiguous one. `done` is final.
SETTLE_EFFECT = (
    _LAPSE
    + """
local now, target, attempt, by = tonumber(ARGV[1]), ARGV[2], ARGV[3], ARGV[4]
local state, lapsed = lapse(KEYS[1], now)
if not state then return {'missing', lapsed, {}} end
local current = redis.call('HGET', KEYS[1], 'attempt')
if by == 'reserver' and attempt ~= current then return {'fenced', lapsed, redis.call('HGETALL', KEYS[1])} end
if state == target and (state == 'done' or state == 'unknown_outcome') then
  return {'ok', lapsed, redis.call('HGETALL', KEYS[1])}
end
local allowed = false
if state == 'reserved' then
  local settled = target == 'done' or target == 'failed'
  allowed = (by == 'reserver' and target ~= 'reserved') or (by == 'observation' and settled)
elseif state == 'unknown_outcome' then
  allowed = target == 'done' or target == 'failed'
elseif state == 'failed' then
  allowed = by == 'observation' and target == 'done'
end
if not allowed then return {'conflict', lapsed, redis.call('HGETALL', KEYS[1])} end
redis.call('HSET', KEYS[1], 'state', target, 'settled_at', ARGV[1], 'settled_by', by)
if target == 'done' then redis.call('HSET', KEYS[1], 'done_at', ARGV[1]) end
redis.call('HDEL', KEYS[1], 'prev')
return {'ok', lapsed, redis.call('HGETALL', KEYS[1])}
"""
)

# What a turn record reports of an effect after the fact, from the agent that acted: `done` is taken from any
# state short of done, since the effect went out; `unknown_outcome` only turns an attempt in flight
# ambiguous. A key Niadra never saw (the check had no answer) starts at the reported state, attempt 1.
# ARGV: now, state, ttl_ms, kind, source, agent.
RECORD_EFFECT = (
    _LAPSE
    + """
local now, target = tonumber(ARGV[1]), ARGV[2]
local state, lapsed = lapse(KEYS[1], now)
local moved = 0
if not state then
  redis.call('HSET', KEYS[1], 'state', target, 'attempt', 1, 'reserved_at', ARGV[1], 'lease_until', ARGV[1],
    'settled_at', ARGV[1], 'settled_by', 'reserver', 'kind', ARGV[4], 'source', ARGV[5], 'agent', ARGV[6],
    'cost', '0', 'fact', '', 'ttl', ARGV[3])
  if target == 'done' then redis.call('HSET', KEYS[1], 'done_at', ARGV[1]) end
  keep(KEYS[1], ARGV[3])
  moved = 1
elseif (target == 'done' and state ~= 'done') or (target == 'unknown_outcome' and state == 'reserved') then
  redis.call('HSET', KEYS[1], 'state', target, 'settled_at', ARGV[1], 'settled_by', 'reserver')
  if target == 'done' then redis.call('HSET', KEYS[1], 'done_at', ARGV[1]) end
  redis.call('HDEL', KEYS[1], 'prev')
  moved = 1
end
return {moved + lapsed, redis.call('HGETALL', KEYS[1])}
"""
)

# Loads one effect from the durable record, only when the store does not hold the key: what the store holds
# is never older than the record.
LOAD_EFFECT = """
if redis.call('EXISTS', KEYS[1]) == 1 then return 0 end
redis.call('HSET', KEYS[1], unpack(cjson.decode(ARGV[1])))
if tonumber(ARGV[2]) > 0 then redis.call('PEXPIRE', KEYS[1], ARGV[2]) end
return 1
"""

# Loads a target's claims, suppressions, commitments and budget spends from the durable record once per life
# of the store: a field or a reservation the store already holds was written after the restart and stays.
# KEYS: loaded, claims, suppressions, commitments, then a window's sorted set and units hash for each budget;
# a spend is `[<index of its window's sorted set in KEYS>, <reservation id>, <units>, <spent at>, <until>]`.
LOAD_TARGET = (
    _WINNER
    + _SUPPRESSIONS_TTL
    + _COMMITMENTS
    + """
if redis.call('GET', KEYS[1]) == ARGV[1] then return 0 end
local now = tonumber(ARGV[2])
local claims = cjson.decode(ARGV[3])
for i = 1, #claims, 2 do redis.call('HSETNX', KEYS[2], claims[i], claims[i + 1]) end
if #claims > 0 then
  local top = tonumber(ARGV[5])
  if top > (tonumber(redis.call('HGET', KEYS[2], '#epoch')) or 0) then
    redis.call('HSET', KEYS[2], '#epoch', ARGV[5])
  end
  tidy(KEYS[2], now)
end
local sups = cjson.decode(ARGV[4])
for i = 1, #sups, 2 do redis.call('HSETNX', KEYS[3], sups[i], sups[i + 1]) end
if #sups > 0 then keep(KEYS[3], now) end
local commitments = cjson.decode(ARGV[8])
for i = 1, #commitments, 2 do redis.call('HSETNX', KEYS[4], commitments[i], commitments[i + 1]) end
if #commitments > 0 then keep_commitments(KEYS[4], now) end
for _, s in ipairs(cjson.decode(ARGV[7])) do
  local zkey, hkey, left = KEYS[s[1]], KEYS[s[1] + 1], s[5] - now
  if left > 0 then
    redis.call('ZADD', zkey, 'NX', s[4], s[2])
    redis.call('HSETNX', hkey, s[2], s[3])
    for _, k in ipairs({zkey, hkey}) do
      if redis.call('PTTL', k) < left then redis.call('PEXPIRE', k, left) end
    end
  end
end
redis.call('SET', KEYS[1], ARGV[1], 'PX', ARGV[6])
return 1
"""
)

LIFE = """
local life = redis.call('GET', KEYS[1])
if not life then
  life = 'r:' .. ARGV[1]
  redis.call('SET', KEYS[1], life)
end
return life
"""

SEAL = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
redis.call('SET', KEYS[1], 'whole')
return 1
"""

LEASE_IF_MINE = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
redis.call('PEXPIRE', KEYS[1], ARGV[2])
return 1
"""


def key(space_id: UUID, tag: str, what: str) -> str:
    return f"co:{space_id}:{{{tag}}}:{what}"


def claims_key(space_id: UUID, tag: str) -> str:
    return key(space_id, tag, "claims")


def suppressions_key(space_id: UUID, tag: str) -> str:
    return key(space_id, tag, "sup")


def budget_keys(space_id: UUID, tag: str, budget: str) -> tuple[str, str]:
    return key(space_id, tag, f"b:{budget}"), key(space_id, tag, f"bu:{budget}")


def commitments_key(space_id: UUID, tag: str) -> str:
    return key(space_id, tag, "cm")


def paid_max_key(space_id: UUID) -> str:
    return key(space_id, SPACE_TAG, "paidmax")


def loaded_key(space_id: UUID, tag: str) -> str:
    return key(space_id, tag, "loaded")


def effect_key(space_id: UUID, key_hash: str) -> str:
    return key(space_id, key_hash, "effect")


def claim_index_key(space_id: UUID, claim_id: UUID) -> str:
    return f"co:{space_id}:claim:{claim_id}"


def idempotency_key(space_id: UUID, digest: str) -> str:
    return f"co:{space_id}:idem:{digest}"


class RedisCoordination:
    def __init__(self, redis: Redis, *, store: str = "state") -> None:
        self._redis = redis
        self._store = store
        self._claim = redis.register_script(CLAIM)
        self._release = redis.register_script(RELEASE)
        self._owner = redis.register_script(OWNER)
        self._check = redis.register_script(CHECK)
        self._spend = redis.register_script(SPEND)
        self._settle_units = redis.register_script(SETTLE_UNITS)
        self._raise_max = redis.register_script(RAISE_MAX)
        self._suppress = redis.register_script(SUPPRESS)
        self._lift = redis.register_script(LIFT)
        self._peek_effect = redis.register_script(PEEK_EFFECT)
        self._reserve_effect = redis.register_script(RESERVE_EFFECT)
        self._cancel_effect = redis.register_script(CANCEL_EFFECT)
        self._settle_effect = redis.register_script(SETTLE_EFFECT)
        self._record_effect = redis.register_script(RECORD_EFFECT)
        self._load_effect = redis.register_script(LOAD_EFFECT)
        self._load_target = redis.register_script(LOAD_TARGET)
        self._life = redis.register_script(LIFE)
        self._seal = redis.register_script(SEAL)
        self._lease_if_mine = redis.register_script(LEASE_IF_MINE)
        self._end_slot = redis.register_script(END_SLOT)
        self._locks = redis.register_script(LOCKS)
        self._commit = redis.register_script(COMMIT)
        self._uncommit = redis.register_script(UNCOMMIT)
        self._commitments = redis.register_script(COMMITMENTS)
        self._budgets_used = redis.register_script(BUDGETS_USED)

    def claims_key(self, space_id: UUID, tag: str) -> str:
        return claims_key(space_id, tag)

    def effect_key(self, space_id: UUID, key_hash: str) -> str:
        return effect_key(space_id, key_hash)

    def idempotency_key(self, space_id: UUID, digest: str) -> str:
        return idempotency_key(space_id, digest)

    async def life(self, *, now_ms: int) -> StoreLife:
        """Whether the store holds everything, and since when it does not."""
        async with translated(self._store, "coordination_life"):
            return _life(await self._life(keys=[MARKER_KEY], args=[now_ms]))

    async def claim_rebuild(self, token: str, lease_ms: int) -> bool:
        async with translated(self._store, "coordination_claim_rebuild"):
            return bool(await self._redis.set(REBUILD_KEY, token, px=lease_ms, nx=True))

    async def extend_rebuild(self, token: str, lease_ms: int) -> bool:
        async with translated(self._store, "coordination_extend_rebuild"):
            return bool(await self._lease_if_mine(keys=[REBUILD_KEY], args=[token, lease_ms]))

    async def seal(self, life: StoreLife) -> bool:
        """Marks the store whole, only if it is still in the life the rebuild began in."""
        async with translated(self._store, "coordination_seal"):
            return bool(await self._seal(keys=[MARKER_KEY], args=[f"r:{life.since_ms}"]))

    async def claim(
        self,
        key: str,
        slot: str,
        *,
        rank: int,
        level: str,
        holder: str,
        kind: ClaimKind,
        via: ClaimVia,
        until_ms: int,
        claim_id: str,
        intents: Sequence[str] = (),
        since_ms: int | None = None,
        now_ms: int,
    ) -> ClaimResult:
        """`since_ms` is when an observation's source saw it; `now_ms` otherwise. `ClaimResult.accepted` is
        false for a declaration or a task lock another holder beats, and for an observation older than what
        the source said last."""
        declare = "1" if via is ClaimVia.DECLARATION else "0"
        lock = "1" if kind is ClaimKind.TASK_LOCK else "0"
        args: list[str | int] = [now_ms, slot, rank, level, holder, kind.value, via.value, until_ms, claim_id]
        args += [json.dumps(list(intents)), declare, since_ms if since_ms is not None else now_ms, lock]
        async with translated(self._store, "coordination_claim"):
            status, epoch, owner = await self._claim(keys=[key], args=args)
        return ClaimResult(_text(status) == "ok", int(epoch or 0), _claim(owner))

    async def end_observation(
        self, key: str, slot: str, *, at_ms: int, keep_until_ms: int, now_ms: int
    ) -> ClaimResult:
        """The source's claim ends at `at_ms`, unless the source said something later; `released` is the
        claim it ended, if one held."""
        async with translated(self._store, "coordination_end_observation"):
            status, epoch, owner, ended = await self._end_slot(
                keys=[key], args=[now_ms, at_ms, slot, keep_until_ms]
            )
        return ClaimResult(_text(status) == "ok", int(epoch or 0), _claim(owner), _claim(ended))

    async def locks(self, key: str, *, now_ms: int) -> list[Claim]:
        async with translated(self._store, "coordination_locks"):
            found = await self._locks(keys=[key], args=[now_ms])
        return [claim for claim in (_claim(item) for item in found) if claim is not None]

    async def release(self, key: str, slot: str, epoch: int, *, now_ms: int) -> ClaimResult:
        async with translated(self._store, "coordination_release"):
            status, current, owner, released = await self._release(
                keys=[key], args=[now_ms, slot, str(epoch)]
            )
        return ClaimResult(_text(status) == "ok", int(current or 0), _claim(owner), _claim(released))

    async def owner(self, key: str, *, now_ms: int) -> Claim | None:
        async with translated(self._store, "coordination_owner"):
            return _claim(await self._owner(keys=[key], args=[now_ms]))

    async def index_claim(self, space_id: UUID, claim_id: UUID, where: ClaimSlot, ttl_ms: int) -> None:
        async with translated(self._store, "coordination_index_claim"):
            await self._redis.set(
                claim_index_key(space_id, claim_id), f"{where.key}\n{where.slot}", px=ttl_ms
            )

    async def find_claim(self, space_id: UUID, claim_id: UUID) -> ClaimSlot | None:
        async with translated(self._store, "coordination_find_claim"):
            raw = await self._redis.get(claim_index_key(space_id, claim_id))
        if raw is None:
            return None
        where, _, slot = _text(raw).partition("\n")
        return ClaimSlot(where, slot)

    async def check_subject(
        self,
        space_id: UUID,
        tag: str,
        reservation_id: str,
        *,
        direction: str,
        purpose: str,
        channel: str | None,
        agent: str,
        intent: str,
        units: int,
        budget: str,
        limit: int | None,
        window_ms: int,
        quiet_until_ms: int | None,
        slice_budget: str | None = None,
        slice_limit: int | None = None,
        spacing_ms: int = 0,
        blocked_by: str | None = None,
        blocked_until_ms: int | None = None,
        blocked_holder: str | None = None,
        window_until_ms: int | None = None,
        now_ms: int,
    ) -> SubjectCheck:
        """`budget` names the purpose's window and `slice_budget` the intent's slice of it."""
        spent, units_key = budget_keys(space_id, tag, budget)
        sliced, slice_units = budget_keys(space_id, tag, slice_budget or f"{budget}:-")
        keys = [
            spent,
            units_key,
            suppressions_key(space_id, tag),
            claims_key(space_id, tag),
            sliced,
            slice_units,
        ]
        args: list[str | int] = [now_ms, reservation_id, units, -1 if limit is None else limit, window_ms]
        args += [purpose, channel or "", agent, intent, quiet_until_ms or 0, direction]
        args += [blocked_by or "", blocked_until_ms or 0, blocked_holder or ""]
        args += [-1 if slice_limit is None else slice_limit, spacing_ms, window_until_ms or 0]
        async with translated(self._store, "coordination_check"):
            raw = json.loads(_text(await self._check(keys=keys, args=args)))
        suppressed = raw["suppressed"] if isinstance(raw["suppressed"], list) else []
        return SubjectCheck(
            admission=Admission(
                Decision(raw["decision"]),
                raw["reason"] or None,
                int(raw["remaining"]),
                int(raw["retry"]) or None,
                _claim(json.dumps(raw["owner"])) if "owner" in raw else None,
            ),
            holder=raw.get("holder"),
            suppressed=tuple(sorted(set(suppressed))),
        )

    async def spend(
        self,
        space_id: UUID,
        tag: str,
        budget: str,
        reservation_id: str,
        *,
        units: int,
        limit: int,
        window_ms: int,
        now_ms: int,
    ) -> Admission:
        async with translated(self._store, "coordination_spend"):
            decision, reason, remaining, retry = await self._spend(
                keys=list(budget_keys(space_id, tag, budget)),
                args=[now_ms, reservation_id, units, limit, window_ms],
            )
        return Admission(Decision(_text(decision)), _text(reason) or None, int(remaining), int(retry) or None)

    async def settle_units(
        self, space_id: UUID, tag: str, budget: str, reservation_id: str, units: int | None
    ) -> bool:
        """`units` is the real cost; None cancels a reservation that was never confirmed."""
        async with translated(self._store, "coordination_settle_units"):
            return bool(
                await self._settle_units(
                    keys=list(budget_keys(space_id, tag, budget)),
                    args=[reservation_id, -1 if units is None else units],
                )
            )

    async def paid_max(self, space_id: UUID, operation: str) -> int | None:
        """The highest cost measured for the operation since the store came up, if any."""
        async with translated(self._store, "coordination_paid_max"):
            raw = await self._redis.hget(paid_max_key(space_id), operation)
        return int(raw) if raw is not None else None

    async def raise_paid_max(self, space_id: UUID, operation: str, units: int) -> None:
        async with translated(self._store, "coordination_raise_paid_max"):
            await self._raise_max(keys=[paid_max_key(space_id)], args=[operation, units])

    async def suppress(
        self,
        space_id: UUID,
        tag: str,
        field: str,
        *,
        until_ms: int | None,
        suppression_id: UUID,
        now_ms: int,
        window: Window | None = None,
    ) -> UUID:
        """The entry's id: the one the field already had, or `suppression_id` for a new one."""
        async with translated(self._store, "coordination_suppress"):
            found = await self._suppress(
                keys=[suppressions_key(space_id, tag)],
                args=[field, until_ms or 0, str(suppression_id), now_ms, window.encode() if window else ""],
            )
        return UUID(_text(found))

    async def lift(self, space_id: UUID, tag: str, field: str, *, now_ms: int) -> UUID | None:
        async with translated(self._store, "coordination_lift"):
            found = _text(await self._lift(keys=[suppressions_key(space_id, tag)], args=[field, now_ms]))
        return UUID(found) if found else None

    async def peek_effect(self, key: str, *, now_ms: int) -> tuple[EffectSnapshot | None, bool]:
        """The effect as the store holds it, and whether this call found its lease lapsed."""
        async with translated(self._store, "coordination_peek_effect"):
            lapsed, fields = await self._peek_effect(keys=[key], args=[now_ms])
        return _snapshot(fields), bool(int(lapsed))

    async def reserve_effect(
        self,
        key: str,
        *,
        kind: str,
        source: str,
        agent: str = "",
        cost_units: int = 0,
        fact: str = "",
        lease_ms: int,
        ttl_ms: int | None,
        now_ms: int,
    ) -> EffectAttempt:
        """`ttl_ms` None keeps the key for as long as the fact exists."""
        args: list[str | int] = [now_ms, lease_ms, ttl_ms or 0, kind, source, agent, cost_units, fact]
        async with translated(self._store, "coordination_reserve_effect"):
            outcome, lapsed, fields = await self._reserve_effect(keys=[key], args=args)
        snapshot = _snapshot(fields)
        assert snapshot is not None
        return EffectAttempt(_text(outcome), snapshot.attempt, snapshot, bool(int(lapsed)))

    async def cancel_effect(self, key: str, attempt: int) -> bool:
        async with translated(self._store, "coordination_cancel_effect"):
            return bool(await self._cancel_effect(keys=[key], args=[attempt]))

    async def settle_effect(
        self, key: str, state: EffectState, *, attempt: int | None, by: Settler, now_ms: int
    ) -> tuple[str, EffectSnapshot | None, bool]:
        """`ok`, `missing`, `fenced` or `conflict`; the effect after the call; whether its lease lapsed."""
        async with translated(self._store, "coordination_settle_effect"):
            status, lapsed, fields = await self._settle_effect(
                keys=[key], args=[now_ms, state.value, "" if attempt is None else attempt, by.value]
            )
        return _text(status), _snapshot(fields), bool(int(lapsed))

    async def record_effect(
        self,
        key: str,
        state: EffectState,
        *,
        kind: str,
        source: str,
        agent: str,
        ttl_ms: int | None,
        now_ms: int,
    ) -> tuple[EffectSnapshot, bool]:
        """The effect after a turn's report, and whether the report or a lapse moved it."""
        async with translated(self._store, "coordination_record_effect"):
            moved, fields = await self._record_effect(
                keys=[key], args=[now_ms, state.value, ttl_ms or 0, kind, source, agent]
            )
        snapshot = _snapshot(fields)
        assert snapshot is not None
        return snapshot, bool(int(moved))

    async def load_effect(self, key: str, snapshot: EffectSnapshot, ttl_ms: int | None) -> bool:
        flat = [item for pair in _fields(snapshot).items() for item in pair]
        async with translated(self._store, "coordination_load_effect"):
            return bool(await self._load_effect(keys=[key], args=[json.dumps(flat), ttl_ms or 0]))

    async def load_target(
        self,
        space_id: UUID,
        tag: str,
        life: StoreLife,
        *,
        claims: Mapping[str, Claim],
        suppressions: Mapping[str, str],
        spends: Sequence[BudgetSpend] = (),
        commitments: Mapping[str, CommitmentEntry] | None = None,
        now_ms: int,
    ) -> bool:
        """Claims by slot, suppressions by field, budget spends and commitments by id, once per life of the
        store."""
        flat_claims = [item for slot, claim in claims.items() for item in (slot, _encode_claim(claim))]
        flat_sups = [item for pair in suppressions.items() for item in pair]
        flat_commitments = [
            item
            for external_id, entry in (commitments or {}).items()
            for item in (external_id, json.dumps(_encode_entry(entry)))
        ]
        top = max((claim.epoch for claim in claims.values()), default=0)
        keys = [
            loaded_key(space_id, tag),
            claims_key(space_id, tag),
            suppressions_key(space_id, tag),
            commitments_key(space_id, tag),
        ]
        windows: dict[str, int] = {}
        loaded: list[list[str | int]] = []
        for spend in spends:
            for budget in (spend.budget, spend.slice):
                if budget is None:
                    continue
                if budget not in windows:
                    windows[budget] = len(keys) + 1
                    keys += budget_keys(space_id, tag, budget)
                loaded.append(
                    [windows[budget], spend.reservation_id, spend.units, spend.at_ms, spend.until_ms]
                )
        args: list[str | int] = [f"r:{life.since_ms}", now_ms, json.dumps(flat_claims), json.dumps(flat_sups)]
        args += [top, LOADED_KEEP_MS, json.dumps(loaded), json.dumps(flat_commitments)]
        async with translated(self._store, "coordination_load_target"):
            return bool(await self._load_target(keys=keys, args=args))

    async def commit(
        self,
        space_id: UUID,
        tag: str,
        external_id: str,
        commitment: CommitmentEntry,
        *,
        now_ms: int,
    ) -> tuple[bool, list[str]]:
        """Whether the new commitment holds, and the ids of those it superseded."""
        entry = {
            "t": commitment.type,
            "c": commitment.compatibility,
            "u": commitment.until_ms,
            "s": commitment.score,
            "a": commitment.agent,
            "i": commitment.commitment_id,
        }
        async with translated(self._store, "coordination_commit"):
            status, superseded = await self._commit(
                keys=[commitments_key(space_id, tag)], args=[now_ms, external_id, json.dumps(entry)]
            )
        found = json.loads(_text(superseded))
        return _text(status) == "active", found if isinstance(found, list) else []

    async def uncommit(self, space_id: UUID, tag: str, external_id: str, *, now_ms: int) -> str | None:
        """The id of the commitment that left the subject's list, if it was there."""
        async with translated(self._store, "coordination_uncommit"):
            found = _text(
                await self._uncommit(keys=[commitments_key(space_id, tag)], args=[now_ms, external_id])
            )
        return found or None

    async def commitments(
        self, space_id: UUID, tag: str, *, now_ms: int
    ) -> list[tuple[str, CommitmentEntry]]:
        async with translated(self._store, "coordination_commitments"):
            raw = [
                _text(item)
                for item in await self._commitments(keys=[commitments_key(space_id, tag)], args=[now_ms])
            ]
        return [
            (external_id, _entry(json.loads(data)))
            for external_id, data in zip(raw[::2], raw[1::2], strict=True)
        ]

    async def budgets_used(
        self, space_id: UUID, tag: str, windows: Sequence[tuple[str, int]], *, now_ms: int
    ) -> list[int]:
        """What each budget's window holds of the subject now, by budget and window length."""
        if not windows:
            return []
        keys = [item for budget, _ in windows for item in budget_keys(space_id, tag, budget)]
        async with translated(self._store, "coordination_budgets_used"):
            used = await self._budgets_used(keys=keys, args=[now_ms, *(window for _, window in windows)])
        return [int(u) for u in used]

    async def suppressed(self, space_id: UUID, tag: str, *, now_ms: int) -> list[str]:
        """The purposes suppressed at every hour; one that holds only in its window is in `windows`."""
        async with translated(self._store, "coordination_suppressed"):
            fields = await self._redis.hgetall(suppressions_key(space_id, tag))
        purposes = set()
        for field, value in fields.items():
            until_ms, _, rest = _text(value).partition("|")
            if "|" in rest:
                continue
            if int(until_ms) == 0 or int(until_ms) > now_ms:
                purposes.add(_text(field).split("|", 1)[0])
        return sorted(purposes)

    async def windows(self, space_id: UUID, tag: str, *, now_ms: int) -> list[WindowedSuppression]:
        """The subject's suppressions that hold only in their local hours, in force at `now_ms`."""
        async with translated(self._store, "coordination_windows"):
            fields = await self._redis.hgetall(suppressions_key(space_id, tag))
        found: list[WindowedSuppression] = []
        for field, value in fields.items():
            until_ms, _, rest = _text(value).partition("|")
            _, _, encoded = rest.partition("|")
            if not encoded or (int(until_ms) != 0 and int(until_ms) <= now_ms):
                continue
            purpose, channel, _ = _text(field).split("|", 2)
            found.append(
                WindowedSuppression(purpose, None if channel == "*" else channel, Window.decode(encoded))
            )
        return found

    async def purge(self, space_id: UUID, tag: str) -> int:
        """Everything the store keeps of a target but its suppressions, which outlive an erasure. The
        erasure's own fence lives apart, with the other modules' (`adapters.outbound.redis.erasure`)."""
        keep = {suppressions_key(space_id, tag)}
        async with translated(self._store, "coordination_purge"):
            # A tag is base64url and a space a UUID: the pattern holds no glob character but its own `*`.
            found = [_text(k) async for k in self._redis.scan_iter(match=key(space_id, tag, "*"))]
            doomed = [k for k in found if k not in keep]
            if doomed:
                await self._redis.delete(*doomed)
        return len(doomed)

    async def remember(self, key: str, value: bytes, ttl_ms: int) -> bool:
        """Sets `key` only when it is not set: what an idempotency key answered the first time."""
        async with translated(self._store, "coordination_remember"):
            return bool(await self._redis.set(key, value, px=ttl_ms, nx=True))

    async def overwrite(self, key: str, value: bytes, ttl_ms: int) -> None:
        async with translated(self._store, "coordination_overwrite"):
            await self._redis.set(key, value, px=ttl_ms)

    async def recall(self, key: str) -> bytes | None:
        async with translated(self._store, "coordination_recall"):
            raw = await self._redis.get(key)
        if raw is None:
            return None
        return raw if isinstance(raw, bytes) else raw.encode()

    async def forget(self, key: str) -> None:
        async with translated(self._store, "coordination_forget"):
            await self._redis.delete(key)


def _text(value: Any) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def _life(raw: Any) -> StoreLife:
    text = _text(raw)
    if text == WHOLE:
        return StoreLife(whole=True, since_ms=0)
    return StoreLife(whole=False, since_ms=int(text.removeprefix("r:")))


def _claim(raw: Any) -> Claim | None:
    text = _text(raw) if raw is not None else ""
    if not text or text == "null":
        return None
    c = json.loads(text)
    return Claim(
        slot=c["slot"],
        rank=int(c["r"]),
        level=c["l"],
        holder=c["h"],
        kind=ClaimKind(c["k"]),
        via=ClaimVia(c["v"]),
        since_ms=int(c["s"]),
        until_ms=int(c["u"]),
        epoch=int(c["e"]),
        claim_id=c["c"],
        intents=tuple(c["i"]) if isinstance(c["i"], list) else (),
    )


def _encode_claim(claim: Claim) -> str:
    return json.dumps(
        {
            "r": claim.rank,
            "l": claim.level,
            "h": claim.holder,
            "k": claim.kind.value,
            "v": claim.via.value,
            "s": claim.since_ms,
            "u": claim.until_ms,
            "e": str(claim.epoch),
            "c": claim.claim_id,
            "i": list(claim.intents),
        }
    )


def _snapshot(fields: Any) -> EffectSnapshot | None:
    if not fields:
        return None
    raw = [_text(item) for item in fields]
    data = dict(zip(raw[::2], raw[1::2], strict=True))

    def ms(name: str) -> int | None:
        return int(data[name]) if data.get(name) else None

    return EffectSnapshot(
        state=EffectState(data["state"]),
        attempt=int(data["attempt"]),
        kind=data.get("kind", ""),
        source=data.get("source", ""),
        agent=data.get("agent") or None,
        reserved_at_ms=int(data["reserved_at"]),
        lease_until_ms=ms("lease_until"),
        settled_at_ms=ms("settled_at"),
        settled_by=Settler(data["settled_by"]) if data.get("settled_by") else None,
        done_at_ms=ms("done_at"),
        cost_units=int(data.get("cost") or 0),
        fact=data.get("fact") or None,
        ttl_ms=int(data.get("ttl") or 0) or None,
    )


def _fields(snapshot: EffectSnapshot) -> dict[str, str]:
    fields = {
        "state": snapshot.state.value,
        "attempt": str(snapshot.attempt),
        "kind": snapshot.kind,
        "source": snapshot.source,
        "agent": snapshot.agent or "",
        "reserved_at": str(snapshot.reserved_at_ms),
        "cost": str(snapshot.cost_units),
        "fact": snapshot.fact or "",
        "ttl": str(snapshot.ttl_ms or 0),
    }
    for name, value in (
        ("lease_until", snapshot.lease_until_ms),
        ("settled_at", snapshot.settled_at_ms),
        ("done_at", snapshot.done_at_ms),
    ):
        if value is not None:
            fields[name] = str(value)
    if snapshot.settled_by is not None:
        fields["settled_by"] = snapshot.settled_by.value
    return fields


def _entry(data: dict[str, Any]) -> CommitmentEntry:
    return CommitmentEntry(
        commitment_id=data["i"],
        type=data["t"],
        compatibility=data["c"],
        until_ms=int(data["u"]),
        score=data.get("s"),
        agent=data.get("a") or None,
        active=data.get("st", "active") == "active",
    )


def _encode_entry(entry: CommitmentEntry) -> dict[str, Any]:
    return {
        "i": entry.commitment_id,
        "t": entry.type,
        "c": entry.compatibility,
        "u": entry.until_ms,
        "s": entry.score,
        "a": entry.agent,
        "st": "active" if entry.active else "superseded",
    }
