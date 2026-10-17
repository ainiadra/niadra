"""Whose memory a reader of a profile may see while the profile holds unconfirmed links (5.3).

Every memory row is anchored on the handle it came through (L6). A handle a claim joined (`declared`,
`explicit_identify`) is provisional until a system of record, a login or an operator confirms it, and
until then its rows and the profile's other rows stay apart:

- a reader through a confirmed handle, or a read addressed by profile or by object, sees the rows of the
  confirmed handles only;
- a reader through a provisional handle sees its own handle's rows and the confirmed ones, and its level
  stays capped at V1 (`PROVISIONAL_READ_CAP`), so the confirmed rows reach it only as far as V1 does;
- a person whose role reviews identity (`UNCONFIRMED_ROLES`) sees everything, each row marked.

What the profile keeps as a whole is derived from the confirmed rows alone (`derivation_view`): the
patterns, the warnings, the stored tail summaries and the analytical reads; so is an agent note distilled
from a conversation that spoke only through unconfirmed links (`only_unconfirmed`). A reader through a
provisional handle gets its own view where one exists (its pack, its summaries rebuilt on read).

A confirmation makes the handle an ordinary member and everything is shared again; a split takes the
handle away with its rows, which follow their anchor. A profile without provisional handles is its own
view.
"""

from collections.abc import Collection, Mapping
from dataclasses import replace
from typing import Any
from uuid import UUID

from niadra.domain.common.space import PersonPrincipal, Principal
from niadra.domain.records import ProfileRecord

UNCONFIRMED_ROLES = frozenset({"admin", "security"})
"""Console roles that may read the rows of unconfirmed links: the people who confirm or split them (the
same roles that see contacts in the clear). No source key does, whatever its scopes."""


def reader_view(profile: ProfileRecord, readers: Collection[UUID] = ()) -> ProfileRecord:
    """The profile as a reader through `readers` (its handles in this read or session; none for a read by
    profile or object) may see it: `handle_ids` narrowed to what it may read, `hidden_handle_ids` the rest.
    Every read path enumerates memory by `handle_ids`, so the view scopes all of it at once."""
    if profile.hidden_handle_ids or not profile.provisional_handle_ids:
        return profile  # already a view, or nothing unconfirmed to keep apart
    own = set(readers)
    hidden = tuple(h for h in profile.provisional_handle_ids if h not in own)
    if not hidden:
        return profile
    kept = set(hidden)
    return replace(
        profile,
        handle_ids=tuple(h for h in profile.handle_ids if h not in kept),
        provisional_handle_ids=tuple(h for h in profile.provisional_handle_ids if h not in kept),
        hidden_handle_ids=tuple(sorted(hidden, key=str)),
    )


def session_view(profile: ProfileRecord, session_handles: Collection[UUID]) -> ProfileRecord:
    """The profile as one conversation's memory writes see it (reconciliation, supersession, closing,
    the extraction's known facts). A conversation that speaks only through unconfirmed links sees their
    rows alone: what arrives through a claimed e-mail never retracts, supersedes or closes the confirmed
    rows, nor learns from them. Any other conversation sees what a reader through its handles sees."""
    named = {h for h in session_handles if h in profile.handle_ids}
    provisional = set(profile.provisional_handle_ids)
    if profile.hidden_handle_ids or not named or not named <= provisional:
        return reader_view(profile, named)
    kept = set(named)
    return replace(
        profile,
        handle_ids=tuple(h for h in profile.handle_ids if h in kept),
        provisional_handle_ids=tuple(h for h in profile.provisional_handle_ids if h in kept),
        hidden_handle_ids=tuple(sorted((h for h in profile.handle_ids if h not in kept), key=str)),
    )


def derivation_view(profile: ProfileRecord) -> ProfileRecord:
    """The profile as what it keeps as a whole is derived from it: the confirmed handles only, the view
    of a read by profile id. A pattern, a warning, a stored summary or an analytical read never counts a
    row that came through an unconfirmed link; once the link is confirmed the row counts, and a split
    takes it away with its handle."""
    return reader_view(profile)


def only_unconfirmed(named: Collection[UUID], provisional: Collection[UUID]) -> bool:
    """Whether a conversation (the handles its events name) spoke only through unconfirmed links: none of
    its rows counts anywhere but in that link's own view until the link is confirmed."""
    handles = set(named)
    return bool(handles) and handles <= set(provisional)


def provisional_reader(profile: ProfileRecord, handle_id: UUID | None) -> UUID | None:
    """The handle a read goes through when it is one of the profile's provisional ones: that reader gets
    packs of its own, never the confirmed readers' (the variant key carries it)."""
    return handle_id if handle_id is not None and handle_id in profile.provisional_handle_ids else None


def hidden_marker(profile: ProfileRecord) -> list[str]:
    """What a pack compiled from this view records in its manifest; a pack is served to a reader only when
    the reader's view hides exactly the same handles, so a join, a confirmation or a split recompiles."""
    return [str(h) for h in profile.hidden_handle_ids]


def compiled_for(manifest: Mapping[str, Any], profile: ProfileRecord) -> bool:
    """Whether a pack with this manifest was compiled for this view: it hid exactly the handles the view
    hides."""
    provenance = manifest.get("provenance") or {}
    return list(provenance.get("hidden_handles") or []) == hidden_marker(profile)


def sees_unconfirmed(principal: Principal) -> bool:
    """A person in one of `UNCONFIRMED_ROLES`; never a source key, whatever its scopes."""
    return isinstance(principal, PersonPrincipal) and bool(principal.roles & UNCONFIRMED_ROLES)


def governance_view(principal: Principal, profile: ProfileRecord) -> ProfileRecord:
    """What a Console or governance read of a profile shows: all of it to the roles that confirm and split
    links, the confirmed view to everyone else, as an agent's read by profile id would."""
    return profile if sees_unconfirmed(principal) else reader_view(profile)
