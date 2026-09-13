"""Extract element and attribute data from the WHATWG HTML Standard.

Usage:
    uv run python -m codegen.extract_spec                  # fetch from html.spec.whatwg.org
    uv run python -m codegen.extract_spec --pages-dir DIR  # read previously saved pages

Writes codegen/data/html-spec.json. The generator and tests read only that file,
so updating the snapshot is an explicit, reviewable change.
"""

import argparse
import hashlib
import json
import sys
import urllib.request
from pathlib import Path

from bs4 import BeautifulSoup, Tag
from bs4.element import NavigableString

from codegen.spec import (
    SPEC_JSON,
    AttributeKind,
    AttributeRow,
    ElementRow,
    Page,
    Snapshot,
    Spec,
)

BASE_URL = "https://html.spec.whatwg.org/multipage/"
INDICES = "indices.html"
SYNTAX = "syntax.html"
DOM = "dom.html"


class ExtractionError(Exception):
    pass


def _tag(node: object, what: str) -> Tag:
    if not isinstance(node, Tag):
        raise ExtractionError(f"expected {what}, found {node!r:.80}")
    return node


def _text(node: Tag) -> str:
    return " ".join(node.get_text().split())


def _strip_footnote(value: str) -> str:
    return value.strip().removesuffix("*").strip()


def _table_rows(
    soup: BeautifulSoup, caption_prefix: str, columns: int
) -> list[list[Tag]]:
    for caption in soup.find_all("caption"):
        if not _text(_tag(caption, "caption")).startswith(caption_prefix):
            continue
        rows: list[list[Tag]] = []
        for tr in _tag(caption.find_parent("table"), "table").find_all("tr"):
            tr = _tag(tr, "tr")
            if tr.find_parent("thead") is not None:
                continue
            cells = [_tag(cell, "cell") for cell in tr.find_all(["th", "td"])]
            if len(cells) != columns:
                raise ExtractionError(
                    f"{caption_prefix!r}: expected {columns} cells in {_text(tr)!r}"
                )
            rows.append(cells)
        return rows
    raise ExtractionError(f"table not found: {caption_prefix!r}")


def _list_items(
    cell: Tag, where: str, warnings: list[str]
) -> tuple[list[str], dict[str, str]]:
    """Read a ';'-separated list cell such as "audio; video; source (in picture)".

    Segments are split on ';'. Within a segment, items come from the markup
    (<code> or links), so two tagged names without a ';' between them are reported
    instead of being merged. A parenthesized suffix is returned as a condition on
    the item before it, not as an item.
    """
    segments: list[list[Tag | str]] = [[]]
    for child in cell.children:
        if isinstance(child, Tag):
            segments[-1].append(child)
        elif isinstance(child, NavigableString):
            first, *rest = str(child).split(";")
            segments[-1].append(first)
            segments.extend([piece] for piece in rest)

    items: list[str] = []
    conditions: dict[str, str] = {}
    for segment in segments:
        text = " ".join(
            "".join(n if isinstance(n, str) else n.get_text() for n in segment).split()
        )
        head, has_condition, condition = _strip_footnote(text).partition("(")
        if not head.strip():
            continue
        tagged: list[str] = []
        for node in segment:
            if isinstance(node, str):
                if "(" in node:
                    break
            elif node.name in ("code", "a") and (name := _strip_footnote(_text(node))):
                tagged.append(name)
        names = tagged or [_strip_footnote(head)]
        if len(names) > 1:
            warnings.append(f"{where}: missing ';' between {names}")
        if has_condition:
            conditions[names[-1]] = condition.removesuffix(")").strip()
        items.extend(names)
    return items, conditions


def extract_elements(indices: BeautifulSoup, warnings: list[str]) -> list[ElementRow]:
    rows: list[ElementRow] = []
    for names, description, _, _, children, attributes, _ in _table_rows(
        indices, "List of elements", columns=7
    ):
        # Skips rows such as "autonomous custom elements" and "MathML math".
        element_names = [
            name
            for name in (_strip_footnote(n) for n in _text(names).split(","))
            if " " not in name
        ]
        if not element_names:
            continue
        attrs, _ = _list_items(
            attributes, f"element index: {', '.join(element_names)}", warnings
        )
        for name in element_names:
            rows.append(
                ElementRow(
                    name=name,
                    description=_text(description),
                    children=_text(children),
                    attributes=[a for a in attrs if a != "globals"],
                    globals="globals" in attrs,
                )
            )
    return rows


def extract_attributes(
    indices: BeautifulSoup, warnings: list[str]
) -> list[AttributeRow]:
    rows: list[AttributeRow] = []
    tables: tuple[tuple[str, AttributeKind], ...] = (
        ("List of attributes", "content"),
        ("List of event handler content attributes", "event-handler"),
    )
    for caption, kind in tables:
        for name, elements, description, value in _table_rows(
            indices, caption, columns=4
        ):
            attr_name = _strip_footnote(_text(name))
            element_names, conditions = _list_items(
                elements, f"attribute index: {attr_name}", warnings
            )
            value_text = _text(value)
            rows.append(
                AttributeRow(
                    name=attr_name,
                    elements=element_names,
                    conditions=conditions,
                    description=_text(description),
                    value=_strip_footnote(value_text),
                    value_footnote=value_text.endswith("*"),
                    kind=kind,
                )
            )
    return rows


def extract_void_elements(syntax: BeautifulSoup) -> list[str]:
    dfn = _tag(syntax.find("dfn", id="void-elements"), "void elements definition")
    names: list[str] = []
    for node in _tag(dfn.find_next("dd"), "void elements list").descendants:
        if isinstance(node, Tag) and node.name == "dt":
            break
        if isinstance(node, Tag) and node.name == "code":
            names.append(_text(node))
    return names


def extract_global_attributes(dom: BeautifulSoup) -> list[str]:
    listed: list[str] | None = None
    # class, id and slot are introduced by a separate sentence because DOM defines them.
    dom_defined: list[str] | None = None
    for p in dom.find_all("p"):
        p = _tag(p, "paragraph")
        content = _text(p)
        if listed is None and "common to and may be specified on all" in content:
            items = _tag(p.find_next("ul"), "global attribute list").find_all("li")
            listed = [
                _text(_tag(_tag(li, "li").find("code"), "attribute name"))
                for li in items
            ]
        elif dom_defined is None and content.endswith(
            "may be specified on all HTML elements."
        ):
            dom_defined = [
                _text(_tag(code, "attribute name")) for code in p.find_all("code")
            ]
    if listed is None or dom_defined is None:
        raise ExtractionError("global attribute definitions not found")
    return [*listed, *dom_defined]


def _load_page(name: str, pages_dir: Path | None) -> tuple[BeautifulSoup, Page]:
    url = BASE_URL + name
    if pages_dir is not None:
        body = (pages_dir / name).read_bytes()
    else:
        request = urllib.request.Request(url, headers={"User-Agent": "pyhtml-codegen"})
        with urllib.request.urlopen(request, timeout=60) as response:
            body = response.read()
    return BeautifulSoup(body, "lxml"), Page(
        url=url, sha256=hashlib.sha256(body).hexdigest()
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--pages-dir", type=Path, help="read saved pages instead of fetching"
    )
    args = parser.parse_args()

    indices, indices_page = _load_page(INDICES, args.pages_dir)
    syntax, syntax_page = _load_page(SYNTAX, args.pages_dir)
    dom, dom_page = _load_page(DOM, args.pages_dir)
    warnings: list[str] = []
    try:
        last_updated = _text(_tag(indices.find("span", class_="pubdate"), "pubdate"))
        spec = Spec(
            snapshot=Snapshot(
                last_updated=last_updated, pages=[indices_page, syntax_page, dom_page]
            ),
            elements=extract_elements(indices, warnings),
            void_elements=extract_void_elements(syntax),
            global_attributes=extract_global_attributes(dom),
            attributes=extract_attributes(indices, warnings),
            extraction_warnings=warnings,
        )
    except ExtractionError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    SPEC_JSON.parent.mkdir(exist_ok=True)
    SPEC_JSON.write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n")
    print(
        f"wrote {SPEC_JSON.name}: {len(spec['elements'])} elements, "
        f"{len(spec['attributes'])} attribute rows, {len(spec['void_elements'])} void elements, "
        f"{len(spec['global_attributes'])} global attributes (Last Updated {last_updated})"
    )
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
