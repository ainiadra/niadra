"""The rows a history read loads, kept by the read process while the customer's memory holds (16).

A search reads the customer's whole memory (about twenty statements) to build its rows, and the next search
of the same customer, seconds later in the same call, would read them again. Here the rows are recorded as
the repositories returned them, call by call, and replayed through the same code (`load_snapshot` and the
rest), so the rows a replay builds are the ones a database read would: what depends on the moment of the
read (a fact's validity, a promise kept in the last days) is still computed at the read.

An entry serves while the profile's generation in the cache is the one it was recorded under: every change
of memory is compiled into a new generation, the moment ingest status calls "readable" (7.1). It also needs
the target's privacy version (the change feed drops a target whose version moved) and is kept at most
`ROWS_FOR_S`, the bound on what moves without a new generation (a pattern's expiry, the window of system
events); an entry past `RENEW_AFTER_S` is read again in the background while it still serves. A replay
that asks for a call nobody recorded goes to the database instead.

A search `about` an organization keeps the organization's rows as an entry of their own, under the
organization's generation, replayed for every contact that searches about it beside the contact's own.
"""

import time
from collections import OrderedDict
from collections.abc import Callable, Hashable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

ROWS_FOR_S = 60.0
RENEW_AFTER_S = 40.0
"""Past this age a replay still serves, and the rows are read again in the background for the next ones:
a customer searched all through a call never waits for the database once its rows are held."""
MAX_ENTRIES = 512


class Missed(Exception):
    """A replay asked for a call the recording does not hold."""


@dataclass(slots=True)
class Recorded:
    generation: int
    privacy_version: int
    window_start: datetime
    """The start of the system events' window the recording read; replays read the same window."""
    loaded: float
    calls: dict[Hashable, Any] = field(default_factory=dict)
    read_at: datetime | None = None
    """When the recording read, for the other windows its calls take (`load_snapshot`'s `read_at`)."""


class HistoryRows:
    def __init__(self, *, monotonic: Callable[[], float] = time.monotonic) -> None:
        self._now = monotonic
        self._entries: OrderedDict[Hashable, Recorded] = OrderedDict()

    def get(self, key: Hashable, generation: int | None, privacy_version: int) -> Recorded | None:
        found = self._entries.get(key)
        if found is None:
            return None
        if (
            generation is None
            or found.generation != generation
            or found.privacy_version != privacy_version
            or self._now() - found.loaded > ROWS_FOR_S
        ):
            del self._entries[key]
            return None
        self._entries.move_to_end(key)
        return found

    def due(self, recorded: Recorded) -> bool:
        """Old enough to be read again in the background (`RENEW_AFTER_S`)."""
        return self._now() - recorded.loaded > RENEW_AFTER_S

    def start(
        self, generation: int, privacy_version: int, window_start: datetime, read_at: datetime | None = None
    ) -> Recorded:
        return Recorded(generation, privacy_version, window_start, self._now(), read_at=read_at)

    def put(self, key: Hashable, recorded: Recorded) -> None:
        """Keeps `recorded` for `key`. A renewal of the same generation keeps what the entry it replaces held
        and it did not read again (an episode's versions, read on the first open of that episode)."""
        previous = self._entries.get(key)
        if (
            previous is not None
            and previous is not recorded
            and (previous.generation, previous.privacy_version)
            == (recorded.generation, recorded.privacy_version)
        ):
            for call, result in previous.calls.items():
                recorded.calls.setdefault(call, result)
        self._entries[key] = recorded
        self._entries.move_to_end(key)
        while len(self._entries) > MAX_ENTRIES:
            self._entries.popitem(last=False)

    def __len__(self) -> int:
        return len(self._entries)


class Recording:
    """Stands in for a unit of work: every repository call goes to `uow` and is kept in `recorded`."""

    def __init__(self, uow: Any, recorded: Recorded) -> None:
        self._uow = uow
        self._recorded = recorded

    def __getattr__(self, repo: str) -> "_RecordingRepo":
        return _RecordingRepo(getattr(self._uow, repo), repo, self._recorded.calls)


class Replay:
    """Stands in for a unit of work from a recording; a call it does not hold raises `Missed`."""

    def __init__(self, recorded: Recorded) -> None:
        self._calls = recorded.calls

    def __getattr__(self, repo: str) -> "_ReplayRepo":
        return _ReplayRepo(repo, self._calls)


class _RecordingRepo:
    def __init__(self, repo: Any, name: str, calls: dict[Hashable, Any]) -> None:
        self._repo, self._name, self._calls = repo, name, calls

    def __getattr__(self, method: str) -> Callable[..., Any]:
        real = getattr(self._repo, method)

        async def call(*args: Any, **kwargs: Any) -> Any:
            result = await real(*args, **kwargs)
            self._calls[_key(self._name, method, args, kwargs)] = result
            return _copy(result)

        return call


class _ReplayRepo:
    def __init__(self, name: str, calls: dict[Hashable, Any]) -> None:
        self._name, self._calls = name, calls

    def __getattr__(self, method: str) -> Callable[..., Any]:
        async def call(*args: Any, **kwargs: Any) -> Any:
            key = _key(self._name, method, args, kwargs)
            if key not in self._calls:
                raise Missed(f"{self._name}.{method}")
            return _copy(self._calls[key])

        return call


def _key(repo: str, method: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Hashable:
    return (repo, method, _frozen(args), _frozen(tuple(sorted(kwargs.items()))))


def _frozen(value: Any) -> Hashable:
    if isinstance(value, list | tuple):
        return tuple(_frozen(v) for v in value)
    if isinstance(value, set | frozenset):
        return frozenset(_frozen(v) for v in value)
    if isinstance(value, dict):
        return tuple(sorted((k, _frozen(v)) for k, v in value.items()))
    return value  # type: ignore[no-any-return]


def _copy(result: Any) -> Any:
    """Each caller gets its own list, as a database read would give it; the rows themselves are frozen."""
    return list(result) if isinstance(result, list) else result
