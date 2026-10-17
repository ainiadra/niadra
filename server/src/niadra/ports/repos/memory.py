"""Facts, mentions, relations, episodes, open items, profile attributes, tail summaries (5.8)."""

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from niadra.domain.extract.result import MemoryRef
from niadra.domain.memory.facts import Relation, RelationOrigin
from niadra.domain.memory.summaries import TailState
from niadra.domain.memory.values import SettledValue, ValueType
from niadra.domain.memory.vectors import ItemVector, PendingVector
from niadra.domain.privacy.erasure import ErasedRow, ErasureScope
from niadra.domain.privacy.purposes import ItemKey, Tagged, Untagged
from niadra.domain.records import Episode, Fact, ObjectKey, OpenItem, ProfileAttr
from niadra.domain.vocabulary import FactStatus, OpenItemStatus


class MemoryRepository(Protocol):
    """Owned by the `memory` module; compile, serve, traits, measure and privacy read through these."""

    async def facts_for(
        self, handle_ids: Sequence[UUID], statuses: Sequence[FactStatus] = (FactStatus.ACTIVE,)
    ) -> list[Fact]: ...

    async def episodes_for(
        self,
        handle_ids: Sequence[UUID],
        *,
        since: datetime | None = None,
        limit: int = 200,
        before: tuple[datetime, UUID] | None = None,
    ) -> list[Episode]:
        """Current versions only, newest first; `before` is the (`ended_at`, id) of the last one read."""
        ...

    async def get_episode(self, episode_id: UUID) -> Episode | None: ...

    async def episode_versions(self, origin_handle_id: UUID, episode_id: UUID) -> list[Episode]:
        """Every version of one episode, oldest first: re-extraction keeps the ones it replaced."""
        ...

    async def expire_facts(self, before: datetime, *, limit: int) -> list[ErasedRow]:
        """Retention: facts last confirmed before `before`, with their mentions and relations."""
        ...

    async def expire_episodes(self, before: datetime, *, limit: int) -> list[ErasedRow]:
        """Retention: every version of the episodes that ended before `before`, and the settled
        values said before it, which are conversation content of the same age."""
        ...

    async def expire_items(self, closed_before: datetime, *, limit: int) -> list[ErasedRow]:
        """Retention: items resolved before `closed_before`; their feedback pairs go with them."""
        ...

    async def open_items_for(
        self, handle_ids: Sequence[UUID], statuses: Sequence[OpenItemStatus] = (OpenItemStatus.OPEN,)
    ) -> list[OpenItem]: ...

    async def open_items_due(self, until: datetime, *, limit: int) -> list[OpenItem]:
        """Open items of the whole space with `due_at <= until`, latest first: the deadline sweep (22.1).
        Nothing closes an item when it passes its deadline, so the oldest come back at every sweep; the
        ones that just fell due must not wait behind them for the limit."""
        ...

    async def object_facts(
        self, keys: Sequence[ObjectKey], handle_ids: Sequence[UUID], *, per_object: int
    ) -> list[Fact]:
        """The active facts bound to one of the objects (`audience` set), under any of the handles, newest
        first, about `per_object` per object: what the participants said about them."""
        ...

    async def object_items(
        self,
        keys: Sequence[ObjectKey],
        handle_ids: Sequence[UUID],
        *,
        per_object: int,
        kept_since: datetime | None = None,
    ) -> list[OpenItem]:
        """The open and overdue items bound to one of the objects (`audience` set), under any of the handles,
        newest first, about `per_object` per object; with `kept_since`, then the ones resolved since it,
        most recently kept first, within the same bound."""
        ...

    async def consolidated_day(self) -> int | None:
        """The local day (ordinal) of the space's last nightly consolidation pass (`space_state`)."""
        ...

    async def set_consolidated_day(self, day: int) -> None: ...

    async def attrs_for(self, handle_ids: Sequence[UUID]) -> list[ProfileAttr]: ...

    async def name_rows(
        self, handle_ids: Sequence[UUID], names: Sequence[str]
    ) -> tuple[list[Fact], list[ProfileAttr]]:
        """The active facts whose predicate is one of `names` and the profile slots of those names, of
        these handles: what may name a customer on a Console screen, two statements for any number of
        customers, and no other row opened. A fact about an object comes too; the caller tells it apart."""
        ...

    async def facts_valued(self, predicates: Sequence[str], dedupe_value: str, *, limit: int) -> list[Fact]:
        """Up to `limit` active facts of the space, of these predicates, whose value has this dedupe key
        (`domain.memory.facts.normalize_value`), found by its keyed digest: a name typed in a search."""
        ...

    async def tail_summary(self, profile_id: UUID, level: str) -> str | None: ...

    async def relations_for(
        self, handle_ids: Sequence[UUID], *, origin: RelationOrigin | None = None
    ) -> list[Relation]:
        """Edges whose first fact belongs to one of the handles; with `origin`, only the edges it drew."""
        ...

    async def episode_vectors(self, episode_ids: Sequence[UUID]) -> list[ItemVector]:
        """The stored vectors of these episodes' current versions, for insights over many customers."""
        ...

    async def vectors_for(self, handle_ids: Sequence[UUID]) -> list[ItemVector]:
        """Stored vectors of the handles' active facts, current episodes and open items, for in-process
        search over one profile (5.14.1, 16.2)."""
        ...

    async def values_for(
        self,
        handle_ids: Sequence[UUID],
        *,
        types: Sequence[ValueType] | None = None,
        since: datetime | None = None,
        limit: int = 500,
    ) -> list[SettledValue]:
        """Settled values of the handles, newest `said_at` first; `domain.memory.values.current` picks the
        one that holds for a type (11, 3.2)."""
        ...

    async def values_by_keys(self, handle_ids: Sequence[UUID], keys: Sequence[str]) -> list[SettledValue]:
        """The handles' values whose `value_key` is one of `keys` (an HMAC lookup, a key is never stored), in
        one statement: the exact channel for the numbers the customer typed. Newest first."""
        ...

    # Writes, used only by the memory module.

    async def insert_values(self, values: Sequence[SettledValue]) -> int:
        """Inserts the values whose id is new and returns how many; ids derive from (session, type, key), so
        a replayed or repeated extraction adds nothing."""
        ...

    async def insert_fact(self, fact: Fact, dedupe_value: str) -> bool:
        """False when (origin handle, predicate, HMAC of `dedupe_value`) already exists; the
        unique constraint decides, never a read before the write (5.8, L11)."""
        ...

    async def find_fact(self, origin_handle_id: UUID, predicate: str, dedupe_value: str) -> Fact | None: ...

    async def update_fact(self, fact: Fact) -> None:
        """Rewrites the mutable columns: times, status, confidence, the two counts, mentions."""
        ...

    async def add_mentions(self, ref: MemoryRef, event_ids: Sequence[UUID], *, feedback: bool = False) -> int:
        """Provenance rows (fact, event); returns how many were new. `feedback` marks the company's own
        feedback events (a retraction, a correction, a restore), which keep the lineage but are never
        evidence (8.4); an event already marked on another row stays marked, so evidence copied from row
        to row keeps its kind."""
        ...

    async def add_relations(self, relations: Sequence[Relation]) -> None: ...

    async def current_episode(self, handle_ids: Sequence[UUID], session_id: UUID) -> Episode | None: ...

    async def add_episode_version(self, episode: Episode, replaces: Episode | None = None) -> None:
        """Inserts `episode` as the current version; `replaces`, the previous current one, stops being
        current (it may live under another origin handle)."""
        ...

    async def insert_open_item(self, item: OpenItem) -> bool:
        """False when the item id exists; ids are derived from the item's natural key."""
        ...

    async def update_open_item(self, item: OpenItem, *, closed_by_event: UUID | None = None) -> None:
        """Rewrites status, `closed_by_action_id` and `closed_at`. `closed_by_event` links the company's
        feedback event that resolved the item, so an erasure of the item finds it and leaves it with ids
        and kinds only (8.4); a link once set stays."""
        ...

    async def note_item_feedback(self, links: Sequence[tuple[UUID, UUID]]) -> int:
        """Each `(item_id, event_id)` records a `feedback.resolve_open_item` event that names the item but
        is not its `closed_by_event_id` (the resolution was refused), so an erasure of the item finds it
        and strips it to ids and kinds (8.4). Ids only, whatever the item's owner or status. Returns how
        many pairs were new; a second call with the same pairs records none."""
        ...

    async def put_attr(self, attr: ProfileAttr) -> None:
        """Upsert of the (origin handle, slot, source) row."""
        ...

    async def tail_state(self, profile_id: UUID, level: str) -> TailState | None: ...

    async def put_tail_summary(self, profile_id: UUID, level: str, state: TailState) -> None: ...

    async def unembedded(
        self, *, model: str | None, handle_ids: Sequence[UUID] | None = None, limit: int
    ) -> list[PendingVector]:
        """Active facts, current episodes and open or overdue items without a vector or, with `model`, with
        a vector of another encoder; facts marked as never encoded are skipped. Only the handles' rows
        with `handle_ids`, the whole space's otherwise (the backfill)."""
        ...

    async def set_vector(self, pending: PendingVector, vector: Sequence[float] | None, model: str) -> bool:
        """Stores the vector sealed, in half precision, with its model and dimension (5.6); `None` marks a
        structured fact as never encoded (15.7). False when the row is gone."""
        ...

    # Purposes (13, 6.6): what each row was collected for, its expiry and the subject's objections.

    async def untagged(self, *, handle_ids: Sequence[UUID] | None, limit: int) -> list[Untagged]:
        """Rows whose `purposes` is NULL, the handles' only or the whole space's, at most `limit` of each
        kind: their attributes, never their content."""
        ...

    async def set_purposes(self, tagged: Sequence[Tagged]) -> None:
        """Writes each row's tags (`{}` for none); an episode's go on every version of it."""
        ...

    async def untag_empty(self, *, limit: int) -> int:
        """Sets `purposes` back to NULL on up to `limit` rows tagged `{}`, so a changed purposes document
        tags them again; returns how many."""
        ...

    async def purpose_expired(self, cutoffs: Mapping[str, datetime], *, limit: int) -> list[ItemKey]:
        """Rows every purpose of which is in `cutoffs` and whose anchor is before the latest of their
        cutoffs: a fact last confirmed, a conversation or a value that ended, a promise closed. An open
        promise never expires by purpose."""
        ...

    async def tagged_with(self, handle_ids: Sequence[UUID], purpose: str) -> list[Tagged]:
        """The handles' rows collected for `purpose`, with their tags."""
        ...

    async def remove_items(self, keys: Sequence[ItemKey]) -> list[ErasedRow]:
        """Deletes the rows, with a fact's mentions and relations and every version of an episode."""
        ...

    async def add_objection(self, handle_ids: Sequence[UUID], purpose: str, at: datetime) -> None: ...

    async def objections(self, handle_ids: Sequence[UUID]) -> dict[UUID, frozenset[str]]:
        """Per handle, the purposes its customer objected to."""
        ...

    async def objection_list(self, handle_ids: Sequence[UUID]) -> list[tuple[UUID, str, datetime]]: ...

    async def objected_handles(self, *, after: tuple[UUID, str] | None, limit: int) -> list[tuple[UUID, str]]:
        """Every (handle, purpose) objection of the space, in key order, for the pass that applies them to
        rows written before a merge or a confirmation brought them under the objection."""
        ...

    async def purpose_state(self, key: str) -> int | None: ...

    async def set_purpose_state(self, key: str, value: int) -> None: ...

    # Privacy, called by the `privacy` module.

    async def erase_for(self, scope: ErasureScope, *, limit: int) -> list[ErasedRow]:
        """`ErasureParticipant` (8.4). With `scope.event_ids`: the mentions of those events, the facts
        left without the customer's evidence (feedback mentions never hold one) with every mention they
        have, the rows only the feedback of those facts holds, and the open items and settled values
        extracted from the events. Otherwise:
        everything anchored on the target's handles (facts, mentions, episodes, open items, settled values,
        attributes, tail summaries), or, for a conversation, its episodes and settled values and the
        profile's tail summaries."""
        ...

    async def export_rows(
        self, table: str, *, after_seq: int, upto_seq: int, limit: int
    ) -> list[dict[str, Any]]:
        """`ExportSource` (22.3) for `episodes`, `facts`, `fact_mentions` and `open_items`: decrypted rows
        by `change_seq`."""
        ...

    # The withholding pass (`app/withhold.py`): every row of the space in key order, with its content, and the
    # rewrite of what the pass scrubbed. A rewritten row's vector is cleared, so it is encoded again.

    async def facts_page(self, after: UUID | None, limit: int) -> list[Fact]:
        """Facts of every status by id, with their evidence."""
        ...

    async def episodes_page(self, after: tuple[UUID, int] | None, limit: int) -> list[Episode]:
        """Every version of every episode, by (episode id, version)."""
        ...

    async def items_page(self, after: UUID | None, limit: int) -> list[OpenItem]: ...

    async def values_page(self, after: UUID | None, limit: int) -> list[SettledValue]: ...

    async def values_by_ids(self, value_ids: Sequence[UUID]) -> list[SettledValue]: ...

    async def rewrite_value(self, value: SettledValue) -> None:
        """A settled value's text in place, and the digest it is found by: an erasure redacts someone named
        in it."""
        ...

    async def attrs_page(self, after: tuple[UUID, str, UUID] | None, limit: int) -> list[ProfileAttr]:
        """By (origin handle, slot, source)."""
        ...

    async def rewrite_fact(self, fact: Fact, dedupe_value: str) -> bool:
        """The fact's predicate, namespace, value, category and sensitivity as given; False, and nothing
        written, when another fact of the handle already holds that predicate and value."""
        ...

    async def rewrite_episode(self, episode: Episode) -> None:
        """Summary, resolution and category of that version, in place."""
        ...

    async def rewrite_item(self, item: OpenItem) -> None:
        """Description and expected operation, in place."""
        ...

    async def restate_item(self, item: OpenItem, *, reworded: bool = False) -> None:
        """Due date, object, expected operation and the organization it is about, in place: what a later
        extraction found about an item it says again (B21). `reworded`: the description too, and the item's
        vector goes, to be made again from it. The status and the closing stay."""
        ...

    async def remove_attr(self, attr: ProfileAttr) -> None: ...

    # An erasure of someone named in the text of others' rows (`domain.privacy.mentions`): the rows the
    # index found, read by id, then rewritten as above.

    async def facts_by_ids(self, fact_ids: Sequence[UUID]) -> list[Fact]:
        """Facts of every status, with their evidence."""
        ...

    async def items_by_ids(self, item_ids: Sequence[UUID]) -> list[OpenItem]: ...

    async def episodes_by_ids(self, episode_ids: Sequence[UUID]) -> list[Episode]:
        """Every version of each episode, by id and version."""
        ...

    async def tails_page(
        self, after: tuple[UUID, str] | None, limit: int
    ) -> list[tuple[UUID, str, TailState]]:
        """Every profile's tail summaries, by profile and level: what the index of mentions reads once."""
        ...
