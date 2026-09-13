"""Generate src/pyhtml/element.py from codegen/data/html-spec.json.

Usage:
    uv run python -m codegen.generate          # rewrite element.py
    uv run python -m codegen.generate --check  # exit 1 if element.py is stale
"""

import argparse
import json
import keyword
import re
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

from codegen.spec import AttributeRow, Spec, is_global, load_spec

ROOT = Path(__file__).resolve().parent.parent
VALUE_TYPES = Path(__file__).parent / "value_types.toml"
OUTPUT = ROOT / "src" / "pyhtml" / "element.py"

# Elements covered by the prototype. Extend this tuple to generate more.
ELEMENTS = ("a", "br", "button", "div", "img", "li", "p", "span", "ul")

GLOBAL_SCOPE = "*"
_KEYWORD_PART = re.compile(r'^"\s*(?P<keyword>[^"]*?)\s*"$')


class GenerationError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class Override:
    type: str
    reason: str


@dataclass(frozen=True, slots=True)
class TypeConfig:
    values: dict[str, str]
    aliases: dict[str, str]
    # (element name or "*", attribute name) -> override
    overrides: dict[tuple[str, str], Override]

    @classmethod
    def load(cls, path: Path = VALUE_TYPES) -> TypeConfig:
        raw = tomllib.loads(path.read_text())
        overrides: dict[tuple[str, str], Override] = {}
        for scope, attrs in raw.get("overrides", {}).items():
            for attr, entry in attrs.items():
                if not entry.get("reason"):
                    raise GenerationError(
                        f"override {scope}.{attr} must state a reason"
                    )
                overrides[scope, attr] = Override(entry["type"], entry["reason"])
        return cls(dict(raw.get("values", {})), dict(raw.get("aliases", {})), overrides)


@dataclass(frozen=True, slots=True)
class Field:
    html_name: str
    type: str
    comment: str


def html_to_python_name(name: str) -> str:
    """Map an HTML name to a keyword argument name (`class` -> `class_`)."""
    ident = name.replace("-", "_")
    if not ident.isidentifier():
        raise GenerationError(f"cannot map {name!r} to a Python identifier")
    return f"{ident}_" if keyword.iskeyword(ident) else ident


def enumerated_type(value: str) -> str | None:
    """Return a Literal type if the Value text is a closed keyword list, else None."""
    members: list[str] = []
    allows_empty = False
    for part in (p.strip() for p in value.split(";")):
        if part == "the empty string":
            allows_empty = True
        elif match := _KEYWORD_PART.match(part):
            members.append(json.dumps(match["keyword"]))
        else:
            return None
    # The empty string is written as True (a bare attribute). False is not offered:
    # omitting the attribute usually selects a different state than any keyword
    # (e.g. spellcheck inherits rather than being "false").
    if allows_empty:
        members.insert(0, "True")
    return f"Literal[{', '.join(members)}]"


class _Builder:
    def __init__(self, spec: Spec, config: TypeConfig) -> None:
        self.spec = spec
        self.config = config
        self.errors: list[str] = []
        self.used_overrides: set[tuple[str, str]] = set()

    def resolve_type(self, scope: str, row: AttributeRow) -> str | None:
        key = (scope, row["name"])
        if (override := self.config.overrides.get(key)) is not None:
            self.used_overrides.add(key)
            return override.type
        if (literal := enumerated_type(row["value"])) is not None:
            return literal
        if (mapped := self.config.values.get(row["value"])) is not None:
            return mapped
        self.errors.append(
            f"no Python type for {scope}.{row['name']} (Value: {row['value']!r}); "
            f"add it to [values] or [overrides] in {VALUE_TYPES.name}"
        )
        return None

    def fields(self, scope: str, rows: list[AttributeRow]) -> dict[str, Field]:
        fields: dict[str, Field] = {}
        for row in sorted(rows, key=lambda r: r["name"]):
            type_expr = self.resolve_type(scope, row)
            if type_expr is None:
                continue
            footnote = "; see element definition" if row["value_footnote"] else ""
            field = Field(
                row["name"],
                type_expr,
                f"{row['description']} [{row['value']}{footnote}]",
            )
            existing = fields.setdefault(row["name"], field)
            if existing.type != field.type:
                self.errors.append(
                    f"{scope}.{row['name']} has conflicting rows: {existing.type} vs {field.type}"
                )
        return fields

    def build(self) -> str:
        attributes = self.spec["attributes"]
        element_rows = {row["name"]: row for row in self.spec["elements"]}
        global_fields = self.fields(
            GLOBAL_SCOPE, [r for r in attributes if is_global(r)]
        )

        elements: list[tuple[str, dict[str, Field]]] = []
        for name in ELEMENTS:
            if name not in element_rows:
                self.errors.append(f"element {name!r} is not in the element index")
                continue
            fields = self.fields(name, [r for r in attributes if name in r["elements"]])
            for attr in [a for a in fields if a in global_fields]:
                if fields[attr].type != global_fields[attr].type:
                    self.errors.append(
                        f"{name}.{attr} ({fields[attr].type}) conflicts with the global "
                        f"attribute ({global_fields[attr].type})"
                    )
                del fields[attr]
            elements.append((name, fields))

        for scope, attr in sorted(set(self.config.overrides) - self.used_overrides):
            self.errors.append(
                f"override {scope}.{attr} does not match any generated attribute"
            )
        if self.errors:
            raise GenerationError("\n".join(self.errors))
        return self.render(global_fields, elements)

    def render(
        self,
        global_fields: dict[str, Field],
        elements: list[tuple[str, dict[str, Field]]],
    ) -> str:
        element_rows = {row["name"]: row for row in self.spec["elements"]}
        void = set(self.spec["void_elements"])
        all_fields = [
            *global_fields.values(),
            *(f for _, fs in elements for f in fs.values()),
        ]
        aliases = {
            name: expr
            for name, expr in self.config.aliases.items()
            if any(re.search(rf"\b{name}\b", f.type) for f in all_fields)
        }
        type_text = " ".join([*(f.type for f in all_fields), *aliases.values()])

        abc_imports = ["Mapping", *(["Sequence"] if "Sequence[" in type_text else [])]
        typing_imports = ["Self", "TypedDict", "Unpack"]
        if "Literal[" in type_text:
            typing_imports.insert(0, "Literal")
        base_imports = ["AttrValue", "Element"]
        if any(name in void for name, _ in elements):
            base_imports.append("VoidElement")

        snapshot = self.spec["snapshot"]
        lines = [
            "# Generated by codegen/generate.py from codegen/data/html-spec.json. Do not edit.",
            f"# Source: WHATWG HTML Living Standard, Last Updated {snapshot['last_updated']}.",
            '"""HTML elements with attribute types derived from the WHATWG HTML Standard."""',
            "",
            f"from collections.abc import {', '.join(abc_imports)}",
            f"from typing import {', '.join(typing_imports)}",
            "",
            f"from pyhtml.base import {', '.join(base_imports)}",
            "",
        ]
        exports = [*aliases, "GlobalAttrs"]
        lines += [f"type {name} = {expr}" for name, expr in aliases.items()]
        lines += [
            "",
            "class GlobalAttrs(TypedDict, total=False):",
            *_field_lines(global_fields),
        ]

        for name, fields in elements:
            row = element_rows[name]
            cls = name.capitalize()
            instance = html_to_python_name(name)
            attrs_base = "GlobalAttrs" if row["globals"] else "TypedDict"
            description = row["description"].replace('"', "'")
            lines += [
                "",
                f"class {cls}Attrs({attrs_base}, total=False):",
                *(_field_lines(fields) or ["    pass"]),
                "",
                f"class {cls}({'VoidElement' if name in void else 'Element'}):",
                f'    """The <{name}> element: {description}."""',
                "",
                "    __slots__ = ()",
                f'    tag = "{name}"',
                "",
                "    def __call__(",
                "        self,",
                "        attrs: Mapping[str, AttrValue] | None = None,",
                "        /,",
                f"        **kwargs: Unpack[{cls}Attrs],",
                "    ) -> Self:",
                "        return self._with_attrs(attrs, kwargs)",
                "",
                f"{instance} = {cls}()",
            ]
            exports += [f"{cls}Attrs", cls, instance]

        lines += [
            "",
            f"__all__ = [{', '.join(json.dumps(e) for e in sorted(exports))}]",
        ]
        return _format("\n".join(lines) + "\n")


def _field_lines(fields: dict[str, Field]) -> list[str]:
    lines: list[str] = []
    for field in fields.values():
        lines.append(f"    # {field.comment}")
        lines.append(f"    {html_to_python_name(field.html_name)}: {field.type} | None")
    return lines


def _format(source: str) -> str:
    ruff = Path(sys.executable).with_name("ruff")
    ruff_path = str(ruff) if ruff.exists() else shutil.which("ruff")
    if ruff_path is None:
        raise GenerationError("ruff is required to format the generated module")
    result = subprocess.run(
        [ruff_path, "format", "--stdin-filename", str(OUTPUT), "-"],
        input=source,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise GenerationError(f"ruff format failed:\n{result.stderr}")
    return result.stdout


def generate_source(spec: Spec | None = None, config: TypeConfig | None = None) -> str:
    return _Builder(spec or load_spec(), config or TypeConfig.load()).build()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--check", action="store_true", help="exit 1 if element.py is stale"
    )
    args = parser.parse_args()
    try:
        source = generate_source()
    except GenerationError as e:
        print(f"error: generation failed\n{e}", file=sys.stderr)
        return 2
    relative = OUTPUT.relative_to(ROOT)
    if args.check:
        if not OUTPUT.exists() or OUTPUT.read_text() != source:
            print(
                f"{relative} is stale; run: uv run python -m codegen.generate",
                file=sys.stderr,
            )
            return 1
        return 0
    OUTPUT.write_text(source)
    print(f"wrote {relative}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
