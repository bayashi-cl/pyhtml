"""Render attributes that the types accept and validate the output with vnu.

The tests need a running Nu Html Checker and are skipped unless VNU_URL is set:

    docker run --rm -d -p 127.0.0.1:8888:8888 ghcr.io/validator/validator:latest
    VNU_URL=http://127.0.0.1:8888/ uv run pytest tests/test_vnu.py

Attribute values come from tests/strategies.py and are valid on their own. The
rules in _Case then satisfy constraints that involve other attributes or elements,
which the types cannot express; each rule matches a known gap listed in
tests/type_cases/cases_attributes.py. Any error that remains is either a bug or a
new gap, which needs a rule here and an entry in the cases file.
"""

import html
import json
import os
import re
import urllib.parse
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from codegen import generate
from pyhtml import element
from pyhtml.base import Element, Node
from tests import strategies

type Message = dict[str, Any]


def document(body: str) -> str:
    return (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        f"<title>pyhtml</title></head><body>{body}</body></html>"
    )


class Checker:
    def __init__(self, url: str) -> None:
        self.url = urllib.parse.urljoin(url, "?out=json")

    def messages(self, source: str) -> list[Message]:
        request = urllib.request.Request(
            self.url,
            data=source.encode(),
            headers={"Content-Type": "text/html; charset=utf-8"},
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            messages: list[Message] = json.load(response)["messages"]
        for message in messages:
            if message["type"] == "non-document-error":
                raise RuntimeError(f"vnu could not check the document: {message}")
        return messages


@dataclass(frozen=True, slots=True)
class Disagreement:
    message: re.Pattern[str]
    reason: str


# Errors where vnu disagrees with the HTML Standard. These are ignored; each entry
# must cite the part of the Standard that allows the markup.
VNU_DISAGREEMENTS = (
    Disagreement(
        re.compile(
            r"Attribute “item(?:id|prop|ref|scope|type)” not allowed on element “br”"
            r" at this point\."
        ),
        "https://html.spec.whatwg.org/multipage/microdata.html#attr-itemscope says"
        " every HTML element may have itemscope and itemprop.",
    ),
    Disagreement(
        re.compile(
            r"Bad value “[\n\x0c ]*\t[\t\n\x0c ]*” for attribute “(?:href|itemid)”"
            r" on element “[a-z]+”: Must be non-empty\."
        ),
        "href and itemid are valid URLs potentially surrounded by spaces, and the empty"
        ' string is a valid URL string. vnu accepts "", " " and "\\n", but rejects'
        " whitespace-only values that contain a tab.",
    ),
    Disagreement(
        re.compile(
            r"Bad value “--[^”]*\n[^”]*” for attribute “command” on element “button”\."
        ),
        "https://html.spec.whatwg.org/multipage/form-elements.html#attr-button-command"
        ' says a custom command keyword is a string that starts with "--". vnu accepts'
        " spaces and tabs after it, but rejects newlines.",
    ),
)


# vnu does not split these attributes at U+000C FORM FEED, which is ASCII whitespace
# (https://infra.spec.whatwg.org/#ascii-whitespace); class, rel and accesskey are
# split correctly. The error messages drop the character, so instead of matching
# them, form feeds in these attributes are replaced with spaces before rendering.
VNU_FORM_FEED_ATTRIBUTES = ("itemprop", "itemref", "itemtype", "ping")


def errors(messages: list[Message]) -> list[Message]:
    return [
        m
        for m in messages
        if m["type"] == "error"
        and not any(d.message.fullmatch(m["message"]) for d in VNU_DISAGREEMENTS)
    ]


@pytest.fixture(scope="module")
def checker() -> Checker:
    url = os.environ.get("VNU_URL")
    if not url:
        pytest.skip("set VNU_URL to the URL of a running Nu Html Checker")
    # Once VNU_URL is set, an unreachable checker fails instead of skipping.
    checker = Checker(url)
    checker.messages(document(""))
    return checker


def test_checker_reports_errors(checker: Checker) -> None:
    messages = checker.messages(document("<a download>x</a>"))
    assert [m["message"] for m in errors(messages)] == [
        "Element “a” is missing required attribute “href”."
    ]


# --- cases --------------------------------------------------------------------

# Content that each element is placed in, and the children it gets.
_PARENTS = {
    "a": ("<p>", "</p>"),
    "button": ("<p>", "</p>"),
    "img": ("<p>", "</p>"),
    "li": ("<ol>", "</ol>"),  # li value is only allowed in ol
    "span": ("<p>", "</p>"),
}
_CHILDREN: dict[str, Node] = {"ul": element.li["x"]}

# Attributes of a that require href. itemprop does too: its value is the URL.
_HREF_DEPENDENT = (
    "download",
    "hreflang",
    "itemprop",
    "ping",
    "referrerpolicy",
    "rel",
    "target",
    "type",
)
_FORM_SUBMISSION = (
    "formaction",
    "formenctype",
    "formmethod",
    "formnovalidate",
    "formtarget",
)
_WIDTH_DESCRIPTOR = re.compile(r" \d+w(?:,|$)")


def _present(attrs: dict[str, Any], key: str) -> bool:
    value = attrs.get(key)
    return value is not None and value is not False


def _tokens(value: str | Sequence[str]) -> list[str]:
    if isinstance(value, str):
        return [t for t in re.split(r"[\t\n\x0c\r ]+", value) if t]
    return list(value)


@dataclass(slots=True)
class _Case:
    name: str
    attrs: dict[str, Any]
    data: st.DataObject
    # Markup placed before the element, such as the targets of ID references.
    support: list[str] = field(default_factory=list[str])
    # Markup around the element, innermost first.
    wrappers: list[tuple[str, str]] = field(default_factory=list[tuple[str, str]])
    ids: list[str] = field(default_factory=list[str])

    def present(self, *keys: str) -> bool:
        return any(_present(self.attrs, key) for key in keys)

    def draw_missing(self, key: str, strategy: st.SearchStrategy[Any]) -> None:
        if not self.present(key):
            self.attrs[key] = self.data.draw(strategy, label=f"added {key}")

    def add_target(self, template: str, id_: str) -> None:
        self.support.append(template.format(id=html.escape(id_)))
        self.ids.append(id_)

    def satisfy_rules(self) -> None:
        attrs = self.attrs
        if self.name == "a" and self.present(*_HREF_DEPENDENT):
            self.draw_missing("href", strategies.url_with_spaces)
        if self.name == "button":
            if self.present(*_FORM_SUBMISSION) and attrs.get("type") not in (
                None,
                "submit",
            ):
                del attrs["type"]
            if self.present("form"):
                self.add_target('<form id="{id}"></form>', attrs["form"])
            if self.present("commandfor"):
                self.add_target('<div id="{id}"></div>', attrs["commandfor"])
            if self.present("popovertarget"):
                self.add_target('<div id="{id}" popover></div>', attrs["popovertarget"])
        if self.name == "img":
            self._satisfy_img_rules()
        # Microdata.
        if self.present("itemid"):
            self.draw_missing("itemtype", strategies.absolute_url_set)
        if self.present("itemid", "itemref", "itemtype"):
            attrs["itemscope"] = True
        if self.present("itemref"):
            for id_ in _tokens(attrs["itemref"]):
                self.add_target('<div id="{id}"></div>', id_)
        if self.present("id"):
            self.ids.append(attrs["id"])
        assume(len(set(self.ids)) == len(self.ids))

    def _satisfy_img_rules(self) -> None:
        attrs = self.attrs
        if not self.present("src", "srcset"):
            self.draw_missing("src", strategies.non_empty_url_with_spaces)
        if self.present("sizes"):
            if not (
                self.present("srcset") and _WIDTH_DESCRIPTOR.search(attrs["srcset"])
            ):
                attrs["srcset"] = self.data.draw(
                    strategies.image_candidates(width=True), label="replaced srcset"
                )
        elif self.present("srcset") and _WIDTH_DESCRIPTOR.search(attrs["srcset"]):
            self.draw_missing("sizes", strategies.source_size_list)
        if self.present("controls") and not attrs.get("alt"):
            attrs["alt"] = self.data.draw(strategies.non_empty_text, label="added alt")
        self.draw_missing("alt", strategies.text)
        if self.present("ismap"):
            # The a element must not contain interactive content (an img with usemap)
            # or elements with tabindex.
            attrs.pop("usemap", None)
            attrs.pop("tabindex", None)
            self.wrappers.append(('<a href="/">', "</a>"))
        if self.present("usemap"):
            self.support.append(
                f'<map name="{html.escape(attrs["usemap"][1:])}"></map>'
            )

    def avoid_vnu_disagreements(self) -> None:
        """Change valid markup that vnu rejects with messages that cannot be ignored."""
        for key in VNU_FORM_FEED_ATTRIBUTES:
            if isinstance(value := self.attrs.get(key), str):
                self.attrs[key] = value.replace("\x0c", " ")
        # vnu requires href on an a element with itemscope. The a element definition
        # only requires it for itemprop and the attributes in _HREF_DEPENDENT, and
        # the message is the same as for those, so it cannot be ignored by pattern.
        if self.name == "a" and self.present("itemscope"):
            self.draw_missing("href", strategies.url_with_spaces)

    def render(self) -> str:
        self.satisfy_rules()
        # After the rules, which may add itemscope.
        self.avoid_vnu_disagreements()
        instance: Any = getattr(element, generate.html_to_python_name(self.name))
        node = instance(**self.attrs)
        if isinstance(node, Element):
            node = node[_CHILDREN.get(self.name, "x")]
        markup = str(node)
        wrappers = list(self.wrappers)
        if self.name in _PARENTS:
            wrappers.append(_PARENTS[self.name])
        if self.present("itemprop"):
            wrappers.append(("<div itemscope>", "</div>"))
        for start, end in wrappers:
            markup = start + markup + end
        return document("".join(self.support) + markup)


@pytest.mark.parametrize("name", generate.ELEMENTS)
@settings(deadline=None)
@given(data=st.data())
def test_rendered_attributes_conform(
    checker: Checker, name: str, data: st.DataObject
) -> None:
    attrs = data.draw(strategies.attributes(name), label="attrs")
    source = _Case(name, attrs, data).render()
    found = errors(checker.messages(source))
    assert not found, "\n".join(
        [source, *(f"{m['message']} ({m.get('extract', '')!r})" for m in found)]
    )
