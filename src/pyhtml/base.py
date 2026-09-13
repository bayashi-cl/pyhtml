"""Core node model and HTML serialization."""

import html
import keyword
import re
from collections.abc import Iterable, Iterator, Mapping, Sequence
from typing import ClassVar, Protocol, Self, cast, runtime_checkable


@runtime_checkable
class HasHtml(Protocol):
    def __html__(self) -> str: ...


class Markup(str):
    """A string that is already safe HTML and is emitted without escaping."""

    __slots__ = ()

    def __html__(self) -> str:
        return self


def raw(value: str) -> Markup:
    return Markup(value)


type AttrValue = str | int | bool | Sequence[str] | None
type Node = BaseElement | HasHtml | str | int | bool | None | Iterable[Node]

# https://html.spec.whatwg.org/multipage/syntax.html#attributes-2
_INVALID_ATTR_NAME_CHARS = re.compile(r"[\s\"'>/=\x00-\x1f\x7f-\x9f]")


def python_to_html_name(name: str) -> str:
    """Map a keyword argument name to its HTML attribute name (`class_` -> `class`)."""
    if name.endswith("_") and keyword.iskeyword(name[:-1]):
        name = name[:-1]
    return name.replace("_", "-")


def _validate_attr_name(name: str) -> None:
    if not name or _INVALID_ATTR_NAME_CHARS.search(name):
        raise ValueError(f"invalid attribute name: {name!r}")


def _attr_text(name: str, value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, int):
        return str(value)
    if isinstance(value, Sequence):
        items = cast("Sequence[object]", value)
        if all(isinstance(item, str) for item in items):
            return " ".join(cast("Sequence[str]", items))
    raise TypeError(f"unsupported value for attribute {name!r}: {value!r}")


def _render_attrs(attrs: Mapping[str, object]) -> str:
    parts: list[str] = []
    for name, value in attrs.items():
        if value is None or value is False:
            continue
        if value is True:
            parts.append(f" {name}")
        else:
            parts.append(f' {name}="{html.escape(_attr_text(name, value))}"')
    return "".join(parts)


def iter_node(node: Node) -> Iterator[str]:
    match node:
        case None | bool():
            return
        case BaseElement():
            yield from node.iter_chunks()
        case HasHtml():
            yield node.__html__()
        case str():
            yield html.escape(node, quote=False)
        case int():
            yield str(node)
        case bytes() | bytearray() | memoryview():
            raise TypeError(f"{node!r} is not a valid child node")
        case _:
            for child in node:
                yield from iter_node(child)


def render(node: Node) -> str:
    return "".join(iter_node(node))


class BaseElement:
    __slots__ = ("_attrs", "_children")

    tag: ClassVar[str]

    def __init__(
        self, attrs: Mapping[str, object] | None = None, children: Node = None
    ) -> None:
        self._attrs: dict[str, object] = dict(attrs or {})
        self._children: Node = children

    def _with_attrs(
        self, attrs: Mapping[str, AttrValue] | None, kwargs: Mapping[str, object]
    ) -> Self:
        merged = dict(self._attrs)
        for name, value in (attrs or {}).items():
            _validate_attr_name(name)
            merged[name] = value
        for name, value in kwargs.items():
            merged[python_to_html_name(name)] = value
        return type(self)(merged, self._children)

    def iter_chunks(self) -> Iterator[str]:
        yield f"<{self.tag}{_render_attrs(self._attrs)}>"

    def __html__(self) -> str:
        return "".join(self.iter_chunks())

    def __str__(self) -> str:
        return self.__html__()


class Element(BaseElement):
    """An element that can have children."""

    __slots__ = ()

    def __getitem__(self, children: Node) -> Self:
        return type(self)(self._attrs, children)

    def iter_chunks(self) -> Iterator[str]:
        yield from super().iter_chunks()
        yield from iter_node(self._children)
        yield f"</{self.tag}>"


class VoidElement(BaseElement):
    """An element that never has children or an end tag."""

    __slots__ = ()
