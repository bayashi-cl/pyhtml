import pytest

from pyhtml import raw, render
from pyhtml.element import a, br, button, div, img, li, ul


def test_escapes_text_and_attribute_values() -> None:
    link = a(href="/s?q=1&r=2")['<b>"hi"</b>']
    assert str(link) == '<a href="/s?q=1&amp;r=2">&lt;b&gt;"hi"&lt;/b&gt;</a>'


def test_keyword_attribute_names_and_token_lists() -> None:
    assert str(div(class_=["card", "wide"])) == '<div class="card wide"></div>'


def test_boolean_attributes_and_none() -> None:
    assert str(button(disabled=True, type="submit", form=None)) == (
        '<button disabled type="submit"></button>'
    )
    assert str(button(disabled=False)) == "<button></button>"


def test_enumerated_attribute_empty_value_state() -> None:
    assert str(div(spellcheck=True, hidden="until-found")) == (
        '<div spellcheck hidden="until-found"></div>'
    )


def test_integer_attributes() -> None:
    assert str(li(value=-3)["x"]) == '<li value="-3">x</li>'


def test_void_elements() -> None:
    assert str(img(src="/a.png", alt="")) == '<img src="/a.png" alt="">'
    assert str(br) == "<br>"


def test_children_forms() -> None:
    items = ["a", "b"]
    assert (
        str(ul[(li[item] for item in items), None, False])
        == "<ul><li>a</li><li>b</li></ul>"
    )


def test_iterator_children_render_repeatedly() -> None:
    node = ul[(li[item] for item in ["a", "b"]), [(li[c] for c in "xy")]]
    expected = "<ul><li>a</li><li>b</li><li>x</li><li>y</li></ul>"
    assert str(node) == expected
    assert str(node) == expected
    assert str(node(id="list")) == expected.replace("<ul>", '<ul id="list">')


def test_children_are_captured_when_building() -> None:
    items = ["a"]
    node = ul[items]
    items.append("b")
    assert str(node) == "<ul>a</ul>"


def test_markup_is_not_escaped() -> None:
    assert render([raw("<hr>"), "<hr>"]) == "<hr>&lt;hr&gt;"


def test_escape_hatch_attributes() -> None:
    assert (
        str(div({"hx-get": "/items", "data-id": 1}))
        == '<div hx-get="/items" data-id="1"></div>'
    )
    with pytest.raises(ValueError, match="invalid attribute name"):
        div({"on click": "x"})


def test_rejects_bytes_child_when_building() -> None:
    with pytest.raises(TypeError, match="not a valid child"):
        _ = div[b"x"]
    with pytest.raises(TypeError, match="not a valid child"):
        _ = div[["ok", bytearray(b"x")]]
    with pytest.raises(TypeError, match="not a valid child"):
        render(memoryview(b"x"))
