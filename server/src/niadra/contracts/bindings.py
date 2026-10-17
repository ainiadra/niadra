"""A tool's binding (the object type spec, tool bindings): which argument of the tool carries which field of a
declared type, how to read the objects out of its result, and what the tool can do. The SDK renders the
constraints block through it, reads the results it measures what was honored on, and runs the tool's dry run
in a counterfactual; the server renders the block for `POST /v1/constraints` with a tool."""

from typing import Annotated, Literal, Self

from pydantic import Field, StringConstraints, model_validator

from niadra.contracts.common import Model
from niadra.contracts.signals import AttrPath, Op

ToolName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_][A-Za-z0-9_.:/-]{0,63}$")]
ParamName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z_][A-Za-z0-9_.-]{0,63}$")]
ResultPath = Annotated[
    str, StringConstraints(max_length=256, pattern=r"^\$?(\.?[A-Za-z_][A-Za-z0-9_-]*(\[\*\])?)*$")
]
"""A small path into a result: `$` the result, `a.b` a key, `a[*]` each item of a list."""
ResultKey = Annotated[str, StringConstraints(pattern=r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$")]
TypeName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,39}$")]
FieldName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
Namespace = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")]


class Negation(Model):
    param: ParamName = Field(description="The argument that takes the values the field must not have.")


class BoundArg(Model):
    """One argument of the tool and the field it carries. `in` and `eq` render to `param`, `not_in` and `ne`
    to the negation's, and a comparison or `between` only when `ops` lists it."""

    attr: AttrPath = Field(description="The field, as `type.field`.")
    param: ParamName
    transform: Literal["lower", "upper"] | None = Field(default=None, description="The case text is sent in.")
    negation: Negation | None = None
    ops: list[Op] = Field(default_factory=list, max_length=9)


class ResultObjects(Model):
    """Where a result carries objects of a type, and which key of each item holds which field."""

    path: ResultPath = "$"
    type: TypeName
    namespace: Namespace
    id: ResultKey = Field(default="id", description="The key of the item that holds the object's id.")
    fields: dict[FieldName, ResultKey] = Field(default_factory=dict, max_length=100)


class ToolCapabilities(Model):
    overfetch: bool = Field(
        default=False, description="The tool returns more than asked: the SDK filters the residual from it."
    )
    relax_flag: ResultPath | None = Field(
        default=None, description="Where the result says the tool relaxed what it was asked."
    )
    dry_run_param: ParamName | None = Field(
        default=None, description="The argument that makes a call change nothing, for a counterfactual."
    )
    mask_output: bool = Field(
        default=False, description="The SDK masks the fields the key may not read in the tool's output."
    )


class ToolBinding(Model):
    tool: ToolName
    args: list[BoundArg] = Field(default_factory=list, max_length=50)
    results: list[ResultObjects] = Field(default_factory=list, max_length=10)
    capabilities: ToolCapabilities = Field(default_factory=ToolCapabilities)

    @model_validator(mode="after")
    def _one_meaning_per_param(self) -> Self:
        params = [a.param for a in self.args] + [a.negation.param for a in self.args if a.negation]
        # One field may have several arguments (`max_price`, `min_price`); one argument carries one thing.
        if len(set(params)) < len(params):
            raise ValueError("an argument of the tool is bound twice")
        return self
