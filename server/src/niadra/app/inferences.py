"""The reviewable profile (24.5.3; LGPD, art. 20): every inference the signals hold about a subject can be
seen, with its origin, evidence, confidence, use and date; corrected, which makes the value the subject gives
a stated preference or attribute (`source: correction`); and deleted, which leaves a tombstone so the same
evidence never infers it again.

An inference is named by a keyed hash of whose body it is in and what it infers, so its value never travels
in a URL. These are governance reads and writes, off the hot path.
"""

from typing import Any
from uuid import UUID

from pydantic import ConfigDict, TypeAdapter, ValidationError

from niadra.app.signals import SignalsService, interaction_json
from niadra.contracts.signals import Inference, InferenceCorrection, InferencePage, Interaction
from niadra.domain.common.errors import InvalidInput, NotFound
from niadra.domain.common.space import Principal
from niadra.domain.signals.inferences import Inferred
from niadra.domain.signals.records import PURPOSE
from niadra.ports.uow import UnitOfWorkFactory

KEY_BYTES = 16
_INTERACTION: TypeAdapter[Any] = TypeAdapter(Interaction, config=ConfigDict(defer_build=True))


class InferencesService:
    def __init__(self, uow: UnitOfWorkFactory, signals: SignalsService) -> None:
        self._uow = uow
        self._signals = signals

    async def page(
        self, principal: Principal, profile_id: UUID, *, cursor: str | None, limit: int
    ) -> InferencePage:
        """`GET /v1/profiles/{id}/inferences`, by key; the cursor is the last key of the page."""
        space_id = principal.space.space_id
        found = await self._found(space_id, await self._handles(space_id, profile_id))
        after = [f for f in found if cursor is None or f[0] > cursor]
        page = after[:limit]
        more = len(after) > limit
        return InferencePage(
            items=[_out(key, for_, inference) for key, _, for_, inference in page],
            next_cursor=page[-1][0] if more and page else None,
        )

    async def correct(
        self, principal: Principal, profile_id: UUID, key: str, body: InferenceCorrection
    ) -> Inference:
        """What the subject says instead becomes a stated preference (or, for an inferred size, a stated
        attribute) with `source: correction`, pending like any said interaction; the inference it corrects
        is deleted. Returns the inference as it was."""
        space_id = principal.space.space_id
        handles = await self._handles(space_id, profile_id)
        name, beneficiary, for_, inference = await self._one(space_id, handles, key)
        if inference.kind in ("interest", "instruction"):
            raise InvalidInput(f"an {inference.kind} is deleted, not corrected")
        said: dict[str, Any] = {"for": for_, "source": "correction", "value": body.value}
        if inference.kind == "attribute":
            said |= {"kind": "attribute", "name": inference.name}
        else:
            said = {
                "kind": "preference", "for": for_, "attr": inference.attr, "op": "in", "values": [body.value],
                "strength": "prefer", "scope": "persistent", "source": "correction",
            }  # fmt: skip
        try:
            interaction = _INTERACTION.validate_python(said)
        except ValidationError as exc:
            raise InvalidInput("the value does not fit what the inference is about") from exc
        await self._signals.said(space_id, handles[0], interaction_json(interaction))
        await self._signals.suppress(space_id, handles, beneficiary, inference.token, inference.kind)
        return _out(name, for_, inference)

    async def delete(self, principal: Principal, profile_id: UUID, key: str) -> None:
        """A tombstone in every body of the subject: evidence up to now never infers it again."""
        space_id = principal.space.space_id
        handles = await self._handles(space_id, profile_id)
        _, beneficiary, _, inference = await self._one(space_id, handles, key)
        await self._signals.suppress(space_id, handles, beneficiary, inference.token, inference.kind)

    async def _handles(self, space_id: UUID, profile_id: UUID) -> list[UUID]:
        async with self._uow(space_id) as uow:
            profile = await uow.identity.get_profile(profile_id)
        if profile is None or not profile.handle_ids:
            raise NotFound("unknown profile")
        return list(profile.handle_ids)

    async def _found(self, space_id: UUID, handles: list[UUID]) -> list[tuple[str, UUID, str, Inferred]]:
        out = []
        for beneficiary, for_, inference in await self._signals.inferences(space_id, handles):
            key = await self._signals.inference_key(space_id, beneficiary, inference.token)
            out.append((key[:KEY_BYTES].hex(), beneficiary, for_, inference))
        return sorted(out, key=lambda f: f[0])

    async def _one(self, space_id: UUID, handles: list[UUID], key: str) -> tuple[str, UUID, str, Inferred]:
        found = next((f for f in await self._found(space_id, handles) if f[0] == key), None)
        if found is None:
            raise NotFound("unknown inference")
        return found


def _out(key: str, for_: str, inference: Inferred) -> Inference:
    return Inference.model_validate(
        {
            "key": key,
            "kind": inference.kind,
            "for": for_,
            "attr": inference.attr,
            "value": inference.value,
            "polarity": inference.polarity,
            "ref": inference.ref,
            "origin": inference.origin,
            "evidence": inference.evidence,
            "confidence": inference.confidence,
            "used_for": [PURPOSE],
            "at": inference.at,
        }
    )
