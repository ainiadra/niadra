"""The `tool-bindings` document: the space's tools, each bound to the fields of its declared types (the object
type spec, tool bindings; 24.2.1). A binding may name the sources whose agents call the tool; the SDK profile
serves each source its own, and the server renders the constraints block through them."""

from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from niadra.config.object_types import ObjectTypeRegistry
from niadra.contracts.bindings import ToolBinding
from niadra.domain.signals.render import Binding, BindingArg

MAX_BINDINGS = 200


class SourceToolBinding(ToolBinding):
    sources: list[UUID] = Field(
        default_factory=list, max_length=50, description="The sources whose agents call it; empty, every one."
    )

    def serves(self, source_id: UUID) -> bool:
        return not self.sources or source_id in self.sources

    def public(self) -> ToolBinding:
        """What the SDK of one source gets: the binding without the sources it is for."""
        return ToolBinding.model_validate(self.model_dump(exclude={"sources"}))

    def binding(self, families: dict[str, str]) -> Binding:
        """The renderer's binding; `families` maps a field (`type.field`) to its attribute family."""
        return Binding(
            self.tool,
            tuple(
                BindingArg(
                    arg.attr,
                    arg.param,
                    arg.transform,
                    arg.negation.param if arg.negation else None,
                    tuple(arg.ops),
                    families.get(arg.attr),
                )
                for arg in self.args
            ),
            self.capabilities.overfetch,
        )


class ToolBindings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, defer_build=True)

    bindings: list[SourceToolBinding] = Field(default_factory=list, max_length=MAX_BINDINGS)

    @model_validator(mode="after")
    def _one_binding_per_tool_and_source(self) -> Self:
        seen: dict[str, list[SourceToolBinding]] = {}
        for binding in self.bindings:
            for other in seen.get(binding.tool, []):
                if not binding.sources or not other.sources or set(binding.sources) & set(other.sources):
                    raise ValueError("a tool is bound twice for one source")
            seen.setdefault(binding.tool, []).append(binding)
        return self

    def for_source(self, source_id: UUID) -> list[SourceToolBinding]:
        return [b for b in self.bindings if b.serves(source_id)]

    def get(self, tool: str, source_id: UUID) -> SourceToolBinding | None:
        return next((b for b in self.for_source(source_id) if b.tool == tool), None)


def families(registry: ObjectTypeRegistry) -> dict[str, str]:
    """The attribute family of each field that has one, keyed `type.field` (`item_variant.size_label`:
    `size`)."""
    return {
        f"{declared.type}.{name}": spec.attribute.family
        for declared in registry.types
        for name, spec in declared.fields.items()
        if spec.attribute is not None
    }
