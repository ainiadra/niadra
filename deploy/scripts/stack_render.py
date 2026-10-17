"""Renders aws/cluster.yaml for one cell the way CloudFormation resolves it before creating anything:
parameters, pseudo parameters, conditions, mappings, Fn::If, Fn::Sub, Fn::Join and Fn::Select are
resolved; what only exists once a resource does (Ref and Fn::GetAtt of a resource, Fn::GetAZs) stays
symbolic, as `${Name}` inside strings. Resources whose condition is false are left out.

    python3 scripts/stack_render.py render [Param=value ...]      the rendering, as JSON
    python3 scripts/stack_render.py check                         what scripts/check.sh runs
    python3 scripts/stack_render.py cost [Param=value ...]        the monthly estimate (scripts/cell.sh)

`check` proves the default cell renders exactly as tests/cluster-default.json (refresh it with
`render AlertEmail=alerts@example.com > tests/cluster-default.json` only for a change meant for the
default cell's stack), and that a dedicated cell's names all derive from its own name.
"""

import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

INFRA = Path(__file__).resolve().parents[1]
TEMPLATE = INFRA / "aws" / "cluster.yaml"
GOLDEN = INFRA / "tests" / "cluster-default.json"
PSEUDO = {
    "AWS::AccountId": "111122223333",
    "AWS::Region": "us-east-2",
    "AWS::Partition": "aws",
    "AWS::URLSuffix": "amazonaws.com",
}
NO_VALUE = object()


class _Loader(yaml.SafeLoader):
    pass


def _tag(loader: yaml.Loader, suffix: str, node: yaml.Node) -> dict[str, Any]:
    name = suffix if suffix in ("Ref", "Condition") else f"Fn::{suffix}"
    if isinstance(node, yaml.ScalarNode):
        value: Any = loader.construct_scalar(node)
        if suffix == "GetAtt":
            value = value.split(".", 1)
    elif isinstance(node, yaml.SequenceNode):
        value = loader.construct_sequence(node, deep=True)
    else:
        value = loader.construct_mapping(node, deep=True)
    return {name: value}


_Loader.add_multi_constructor("!", _tag)


def load(path: Path = TEMPLATE) -> dict[str, Any]:
    return yaml.load(path.read_text(), Loader=_Loader)  # noqa: S506 - the loader is SafeLoader's


class Renderer:
    def __init__(self, template: dict[str, Any], overrides: dict[str, str]) -> None:
        self.template = template
        declared = template.get("Parameters", {})
        unknown = set(overrides) - set(declared)
        if unknown:
            raise SystemExit(f"unknown parameters: {', '.join(sorted(unknown))}")
        self.params: dict[str, Any] = {}
        for name, spec in declared.items():
            value = overrides.get(name, spec.get("Default"))
            if value is None:
                raise SystemExit(f"parameter {name} has no default; pass {name}=<value>")
            if spec.get("Type") == "CommaDelimitedList":
                value = [v for v in str(value).split(",") if v]
            pattern = spec.get("AllowedPattern")
            if pattern and not re.fullmatch(pattern, str(value)):
                raise SystemExit(f"{name}={value} does not match {pattern}")
            self.params[name] = value if spec.get("Type") != "Number" else str(value)
        self.resources = set(template.get("Resources", {}))
        self.conditions: dict[str, bool] = {}
        for name in template.get("Conditions", {}):
            self.condition(name)

    def condition(self, name: str) -> bool:
        if name not in self.conditions:
            self.conditions[name] = bool(self.value(self.template["Conditions"][name]))
        return self.conditions[name]

    def value(self, node: Any) -> Any:  # noqa: C901, PLR0911, PLR0912 - one branch per intrinsic
        if isinstance(node, list):
            return [v for v in (self.value(n) for n in node) if v is not NO_VALUE]
        if not isinstance(node, dict):
            return node
        if len(node) == 1:
            (key, arg), = node.items()
            if key == "Ref":
                if arg == "AWS::NoValue":
                    return NO_VALUE
                if arg in self.params:
                    return self.params[arg]
                if arg in PSEUDO:
                    return PSEUDO[arg]
                if arg == "AWS::StackName":
                    return self.params.get("CellName", "niadra")
                return {"Ref": arg}
            if key == "Condition":
                return self.condition(arg)
            if key == "Fn::If":
                name, yes, no = arg
                return self.value(yes if self.condition(name) else no)
            if key == "Fn::Equals":
                left, right = (self.value(a) for a in arg)
                return left == right
            if key == "Fn::Not":
                return not self.value(arg[0])
            if key == "Fn::And":
                return all(self.value(a) for a in arg)
            if key == "Fn::Or":
                return any(self.value(a) for a in arg)
            if key == "Fn::FindInMap":
                mapping, top, second = (self.value(a) for a in arg)
                return self.template["Mappings"][mapping][top][second]
            if key == "Fn::Sub":
                return self.sub(arg)
            if key == "Fn::Join":
                separator, items = arg[0], self.value(arg[1])
                if isinstance(items, list) and all(isinstance(i, str) for i in items):
                    return separator.join(items)
                return {"Fn::Join": [separator, items]}
            if key == "Fn::Select":
                index, items = self.value(arg[0]), self.value(arg[1])
                if isinstance(items, list):
                    return items[int(index)]
                return {"Fn::Select": [index, items]}
            if key in ("Fn::GetAtt", "Fn::GetAZs", "Fn::Base64", "Fn::Split", "Fn::Cidr", "Fn::ImportValue"):
                return {key: self.value(arg)}
        out = {}
        for key, child in node.items():
            resolved = self.value(child)
            if resolved is not NO_VALUE:
                out[key] = resolved
        return out

    def sub(self, arg: Any) -> Any:
        text, variables = (arg, {}) if isinstance(arg, str) else (arg[0], arg[1])
        local = {k: self.value(v) for k, v in variables.items()}

        def replace(match: re.Match[str]) -> str:
            name = match.group(1)
            if name.startswith("!"):
                return "${" + name[1:] + "}"
            if name in local:
                value = local[name]
            elif name in self.params:
                value = self.params[name]
            elif name in PSEUDO:
                value = PSEUDO[name]
            elif name == "AWS::StackName":
                value = self.params.get("CellName", "niadra")
            else:
                return "${" + name + "}"
            if isinstance(value, dict) and set(value) == {"Ref"}:
                return "${" + value["Ref"] + "}"
            if isinstance(value, dict) and set(value) == {"Fn::GetAtt"}:
                return "${" + ".".join(value["Fn::GetAtt"]) + "}"
            if not isinstance(value, str):
                raise SystemExit(f"cannot substitute {name} with {value!r}")
            return value

        return re.sub(r"\$\{([^}]+)\}", replace, text)

    def render(self) -> dict[str, Any]:
        resources = {}
        for name, resource in self.template["Resources"].items():
            if "Condition" in resource and not self.condition(resource["Condition"]):
                continue
            resources[name] = {k: self.value(v) for k, v in resource.items() if k != "Condition"}
        outputs = {}
        for name, output in self.template.get("Outputs", {}).items():
            if "Condition" in output and not self.condition(output["Condition"]):
                continue
            outputs[name] = self.value(output["Value"])
        return {"Resources": resources, "Outputs": outputs}


def render(overrides: dict[str, str]) -> dict[str, Any]:
    return Renderer(load(), overrides).render()


def _args(argv: list[str]) -> dict[str, str]:
    pairs = [a.split("=", 1) for a in argv]
    if any(len(p) != 2 for p in pairs):
        raise SystemExit("parameters are Name=value")
    return dict(pairs)  # type: ignore[arg-type]


# Approximate on-demand list prices in us-east-2 (Linux, US dollars), for the estimate only; the AWS
# Pricing Calculator has the current ones. An instance type missing here gets no estimate, and cell.sh
# refuses to create a cell it cannot price.
HOURS = 730
EC2_HOURLY = {
    "t3.large": 0.0832,
    "t3.xlarge": 0.1664,
    "m7i-flex.large": 0.09576,
    "m7i-flex.xlarge": 0.19152,
    "m7i-flex.2xlarge": 0.38304,
    "m7i.large": 0.1008,
    "m7i.xlarge": 0.2016,
    "m7i.2xlarge": 0.4032,
    "r7i.large": 0.1323,
    "r7i.xlarge": 0.2646,
}
RDS_HOURLY = {
    "db.t4g.micro": 0.016,
    "db.t4g.small": 0.032,
    "db.t4g.medium": 0.065,
    "db.t4g.large": 0.129,
    "db.m7g.large": 0.168,
    "db.m7g.xlarge": 0.336,
    "db.r7g.large": 0.239,
    "db.r7g.xlarge": 0.478,
}
EBS_GP3_GB_MONTH = 0.08
RDS_GP3_GB_MONTH = 0.115
PUBLIC_IPV4_MONTH = 0.005 * HOURS
KMS_KEY_MONTH = 1.0
SECRET_MONTH = 0.40
# The cell's secrets: its generated keys, the AI endpoint's secret, the deploy key and the database
# master secret RDS manages.
CELL_SECRETS = 4
HOSTED_ZONE_MONTH = 0.50


def cost(overrides: dict[str, str]) -> list[tuple[str, float]]:
    rendered = Renderer(load(), overrides)
    p = rendered.params
    instance, database = p["InstanceType"], p["DatabaseClass"]
    if instance not in EC2_HOURLY or database not in RDS_HOURLY:
        missing = [t for t, table in ((instance, EC2_HOURLY), (database, RDS_HOURLY)) if t not in table]
        raise SystemExit(f"no price for {', '.join(missing)} in scripts/stack_render.py; add it first")
    lines = [
        (f"machine {instance}", EC2_HOURLY[instance] * HOURS),
        (f"machine disk {p['NodeVolumeGiB']} GiB gp3", int(p["NodeVolumeGiB"]) * EBS_GP3_GB_MONTH),
        (f"database {database}", RDS_HOURLY[database] * HOURS),
        (
            f"database storage {p['DatabaseStorageGiB']} GiB gp3",
            int(p["DatabaseStorageGiB"]) * RDS_GP3_GB_MONTH,
        ),
        ("fixed public address", PUBLIC_IPV4_MONTH),
        ("master key in KMS", KMS_KEY_MONTH),
        (f"{CELL_SECRETS} secrets", CELL_SECRETS * SECRET_MONTH),
    ]
    if rendered.condition("IsDefaultCell"):
        lines.append(("DNS zone", HOSTED_ZONE_MONTH))
    return lines


def check() -> None:
    default = render({"AlertEmail": "alerts@example.com"})
    golden = json.loads(GOLDEN.read_text())
    if default != golden:
        changed = sorted(
            name
            for part in ("Resources", "Outputs")
            for name in set(default[part]) | set(golden[part])
            if default[part].get(name) != golden[part].get(name)
        )
        raise SystemExit(f"the default cell renders differently from {GOLDEN.name}: {', '.join(changed)}")
    print(f"    default cell: identical to {GOLDEN.relative_to(INFRA)}")

    cell = "niadra-acme"
    dedicated = render({"CellName": cell, "ZoneId": "Z0000EXAMPLE"})
    text = json.dumps(dedicated)
    problems = []
    for name in ("Zone", "Records", "ServerRepository", "ModelsRepository", "SiteRepository", "SiteApiRepository"):
        if name in dedicated["Resources"]:
            problems.append(f"{name} belongs to the default cell only")
    # Every name the default cell uses is replaced by one derived from the cell's name.
    for taken in ('"niadra"', "niadra-cell-", "niadra-releases-", "niadra-audit-", "alias/niadra-cell",
                  "/niadra/cluster", "secret:niadra/", '"niadra-alerts"', "niadra-server", "niadra-public-a",
                  "rds!db-*"):
        if taken in text:
            problems.append(f"{taken} is the default cell's")
    for derived in (f"{cell}-cell-111122223333", f"{cell}-releases-111122223333", f"{cell}-audit-111122223333",
                    f"alias/{cell}-cell", f"/{cell}/cluster", f"secret:{cell}/*", f'"{cell}-alerts"',
                    f'"{cell}"', f"{cell}.cells.niadra.com", "hostedzone/Z0000EXAMPLE"):
        if derived not in text:
            problems.append(f"{derived} is missing")
    resources = dedicated["Resources"]
    for name in ("DatabaseSecretPolicy", "NodeDatabaseSecret", "CellRecords"):
        if name not in resources:
            problems.append(f"{name} is missing")
    key = {"Ref": "CellKey"}
    if resources["Database"]["Properties"].get("KmsKeyId") != key:
        problems.append("the database is not encrypted with the cell's key")
    if resources["Server"]["Properties"]["BlockDeviceMappings"][0]["Ebs"].get("KmsKeyId") != key:
        problems.append("the machine's disk is not encrypted with the cell's key")
    if problems:
        raise SystemExit("dedicated cell: " + "; ".join(problems))
    print(f"    dedicated cell {cell}: every name derived from it, the zone and images shared")


def main(argv: list[str]) -> None:
    command, rest = (argv[0], argv[1:]) if argv else ("render", [])
    if command == "render":
        json.dump(render(_args(rest)), sys.stdout, indent=1, sort_keys=True)
        sys.stdout.write("\n")
    elif command == "check":
        check()
    elif command == "cost":
        lines = cost(_args(rest))
        for label, amount in lines:
            print(f"    {label:<38} US$ {amount:8.2f}")
        print(f"    {'total per month, running all the time':<38} US$ {sum(a for _, a in lines):8.2f}")
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
