"""Schema of codegen/data/html-spec.json, shared by the extractor, generator and tests."""

import json
from pathlib import Path
from typing import Literal, TypedDict, cast

SPEC_JSON = Path(__file__).parent / "data" / "html-spec.json"

# Marker used by the WHATWG attribute index for global attributes.
HTML_ELEMENTS = "HTML elements"


class Page(TypedDict):
    url: str
    sha256: str


class Snapshot(TypedDict):
    last_updated: str
    pages: list[Page]


class ElementRow(TypedDict):
    name: str
    description: str
    children: str
    attributes: list[str]
    globals: bool


type AttributeKind = Literal["content", "event-handler"]


class AttributeRow(TypedDict):
    name: str
    elements: list[str]
    # Context restrictions from the index, e.g. {"source": "in picture"} for width.
    conditions: dict[str, str]
    description: str
    value: str
    # The index marks values whose full constraints are in the element definition.
    value_footnote: bool
    kind: AttributeKind


class Spec(TypedDict):
    snapshot: Snapshot
    # "List of elements" in the index.
    elements: list[ElementRow]
    # "Void elements" in the HTML syntax section.
    void_elements: list[str]
    # The global attribute list in the "Global attributes" section.
    global_attributes: list[str]
    # "List of attributes" and "List of event handler content attributes" in the index.
    attributes: list[AttributeRow]
    # Inconsistencies found in the source markup, kept so snapshot reviews see them.
    extraction_warnings: list[str]


def load_spec(path: Path = SPEC_JSON) -> Spec:
    return cast(Spec, json.loads(path.read_text()))


def is_global(row: AttributeRow) -> bool:
    return HTML_ELEMENTS in row["elements"]
