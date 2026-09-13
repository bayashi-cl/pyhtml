"""Checks that the generated element module agrees with the extracted HTML Standard data."""

import keyword
from typing import Any

import pytest

from codegen import generate
from codegen.spec import is_global, load_spec
from pyhtml import element
from pyhtml.base import VoidElement, python_to_html_name

SPEC = load_spec()
ELEMENT_ROWS = {row["name"]: row for row in SPEC["elements"]}


def _instance(name: str) -> Any:
    return getattr(element, generate.html_to_python_name(name))


def _typed_attribute_names(name: str) -> set[str]:
    attrs: Any = getattr(element, f"{type(_instance(name)).__name__}Attrs")
    keys: frozenset[str] = attrs.__required_keys__ | attrs.__optional_keys__
    return {python_to_html_name(key) for key in keys}


def test_generated_module_is_up_to_date() -> None:
    assert generate.OUTPUT.read_text() == generate.generate_source(), (
        "element.py is stale; run: uv run python -m codegen.generate"
    )


@pytest.mark.parametrize("name", generate.ELEMENTS)
def test_attributes_match_element_index(name: str) -> None:
    # The generator reads the attribute index; this compares against two other
    # places in the Standard: the element index and the global attributes section.
    row = ELEMENT_ROWS[name]
    expected = set(row["attributes"])
    if row["globals"]:
        expected |= set(SPEC["global_attributes"])
        expected |= {
            r["name"]
            for r in SPEC["attributes"]
            if r["kind"] == "event-handler" and is_global(r)
        }
    assert _typed_attribute_names(name) == expected


@pytest.mark.parametrize("name", generate.ELEMENTS)
def test_void_elements_match_syntax_section(name: str) -> None:
    assert isinstance(_instance(name), VoidElement) == (name in SPEC["void_elements"])


def test_attribute_names_round_trip() -> None:
    for name in sorted({row["name"] for row in SPEC["attributes"]}):
        python_name = generate.html_to_python_name(name)
        assert not keyword.iskeyword(python_name), name
        assert python_to_html_name(python_name) == name
