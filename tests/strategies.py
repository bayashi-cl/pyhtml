"""Hypothesis strategies for attribute values that conform to the HTML Standard.

The strategy for each attribute follows its annotation in the element's *Attrs
TypedDict: Literal members and bool come from the type itself, while str, int and
token-list members come from the attribute's Value syntax in the WHATWG attribute
index. A syntax without a strategy is an error, so new attributes need a decision
here, as they do in codegen/value_types.toml.

Every strategy produces values that are valid on their own. Rules that involve other
attributes or other elements (attributes that require href, ID references, ...) are
the caller's concern.
"""

import string
from collections.abc import Sequence
from dataclasses import dataclass
from typing import (
    Any,
    Literal,
    TypeAliasType,
    Union,
    get_args,
    get_origin,
    get_type_hints,
)

from hypothesis import strategies as st

from codegen import generate
from codegen.spec import is_global, load_spec
from pyhtml import element
from pyhtml.base import python_to_html_name

ASCII_WHITESPACE = "\t\n\x0c\r "

# --- characters and tokens ----------------------------------------------------

# Surrogates cannot be encoded, unassigned code points include the noncharacters,
# and the Standard forbids control characters other than ASCII whitespace.
_EXCLUDED_CATEGORIES: Any = ("Cs", "Cn", "Cc")

text = st.text(
    st.characters(
        exclude_categories=_EXCLUDED_CATEGORIES, include_characters="\t\n\x0c\r"
    ),
    max_size=10,
)
non_empty_text = text.filter(bool)
# A token is a non-empty string without ASCII whitespace (controls are excluded above).
token = st.text(
    st.characters(exclude_categories=_EXCLUDED_CATEGORIES, exclude_characters=" "),
    min_size=1,
    max_size=10,
)
whitespace = st.text(ASCII_WHITESPACE, max_size=2)
separator = st.text(ASCII_WHITESPACE, min_size=1, max_size=2)


@st.composite
def join_tokens(draw: st.DrawFn, tokens: st.SearchStrategy[list[str]]) -> str:
    """Join tokens with arbitrary ASCII whitespace, as a single attribute string."""
    items = draw(tokens)
    parts = [draw(whitespace)]
    for i, item in enumerate(items):
        if i:
            parts.append(draw(separator))
        parts.append(item)
    parts.append(draw(whitespace))
    return "".join(parts)


def _concat(*parts: st.SearchStrategy[str]) -> st.SearchStrategy[str]:
    return st.tuples(*parts).map("".join)


def _prefixed(prefix: str, strategy: st.SearchStrategy[str]) -> st.SearchStrategy[str]:
    return strategy.map(lambda value: prefix + value)


# --- URLs ---------------------------------------------------------------------

# https://url.spec.whatwg.org/#url-code-points, split into characters that are safe
# in every URL component and percent-encoded bytes.
_URL_ASCII = string.ascii_letters + string.digits + "-._~!$&'()*+,;=:@"
_url_char = st.one_of(
    st.sampled_from(_URL_ASCII),
    st.integers(0, 255).map("%{:02X}".format),
    st.characters(min_codepoint=0xA0, exclude_categories=("Cs", "Cn")),
)
_segment = st.lists(_url_char, max_size=6).map("".join)
_path = st.lists(_segment, max_size=3).map(lambda segments: "/" + "/".join(segments))
_query = st.just("") | _prefixed(
    "?", st.lists(_url_char | st.sampled_from("/?"), max_size=8).map("".join)
)
_fragment = st.just("") | _prefixed(
    "#", st.lists(_url_char | st.sampled_from("/?"), max_size=8).map("".join)
)
# "--" is excluded so that no label starts with "xn--", which must be Punycode.
_label = st.from_regex(r"[a-z0-9]([a-z0-9-]{0,8}[a-z0-9])?", fullmatch=True).filter(
    lambda label: "--" not in label
)
# The last label starts with a letter: a host that ends in a number is an IPv4 address.
_top_label = st.from_regex(r"[a-z]([a-z0-9-]{0,8}[a-z0-9])?", fullmatch=True).filter(
    lambda label: "--" not in label
)
_host = st.one_of(
    st.lists(_label, max_size=2).flatmap(
        lambda labels: _top_label.map(lambda top: ".".join([*labels, top]))
    ),
    st.sampled_from(["127.0.0.1", "[::1]"]),
)
_port = st.just("") | st.integers(0, 65535).map(":{}".format)

# Only HTTP(S) schemes, so these also satisfy attributes such as ping.
absolute_url = _concat(
    st.sampled_from(["http://", "https://"]), _host, _port, _path, _query, _fragment
)
relative_url = st.one_of(
    # A path-absolute URL must not start with "//", which would make it scheme-relative.
    _concat(_path.filter(lambda path: not path.startswith("//")), _query, _fragment),
    _concat(_prefixed("./", _segment), _query, _fragment),
    _query,
    _fragment,
)
url = absolute_url | relative_url  # may be empty
non_empty_url = url.filter(bool)
url_with_spaces = _concat(whitespace, url, whitespace)
non_empty_url_with_spaces = _concat(whitespace, non_empty_url, whitespace)
absolute_url_set = st.lists(absolute_url, min_size=1, max_size=3, unique=True)

# --- other microsyntaxes ------------------------------------------------------


def _is_navigable_target_name(name: str) -> bool:
    has_tab_or_newline = any(c in name for c in "\t\n\r")
    return not name.startswith("_") and not (has_tab_or_newline and "<" in name)


# https://html.spec.whatwg.org/multipage/document-sequences.html#valid-navigable-target-name-or-keyword
navigable_target = st.sampled_from(["_blank", "_self", "_parent", "_top"]) | (
    non_empty_text.filter(_is_navigable_target_name)
)

# Validity depends on the IANA subtag registry, so these are picked, not generated.
language_tag = st.sampled_from(
    [
        "en",
        "ja",
        "en-US",
        "en-GB",
        "es-419",
        "zh-Hant",
        "zh-Hant-TW",
        "sr-Latn-RS",
        "de-CH-1996",
        "ja-JP-u-ca-japanese",
        "x-private",
    ]
)

# https://mimesniff.spec.whatwg.org/#valid-mime-type-string
_http_token = st.text(
    string.ascii_letters + string.digits + "!#$%&'*+-.^_`|~", min_size=1, max_size=8
)
_quoted_string = st.lists(
    st.characters(min_codepoint=0x20, max_codepoint=0x7E, exclude_characters='"\\')
    | st.sampled_from(['\\"', "\\\\"]),
    max_size=6,
).map(lambda parts: '"' + "".join(parts) + '"')
_mime_parameter = _concat(
    st.sampled_from([";", "; ", ";\t"]),
    _http_token,
    st.just("="),
    _http_token | _quoted_string,
)
mime_type = _concat(
    _http_token,
    st.just("/"),
    _http_token,
    st.lists(_mime_parameter, max_size=2).map("".join),
)

# https://html.spec.whatwg.org/multipage/custom-elements.html#valid-custom-element-name
_RESERVED_CUSTOM_ELEMENT_NAMES = frozenset(
    [
        "annotation-xml",
        "color-profile",
        "font-face",
        "font-face-src",
        "font-face-uri",
        "font-face-format",
        "font-face-name",
        "missing-glyph",
    ]
)
custom_element_name = st.from_regex(
    r"[a-z][a-z0-9._]{0,5}-[a-z0-9._-]{0,6}", fullmatch=True
).filter(lambda name: name not in _RESERVED_CUSTOM_ELEMENT_NAMES)

_css_length = st.one_of(
    st.integers(0, 2000).map("{}px".format),
    st.integers(1, 100).map("{}vw".format),
    st.integers(1, 100).map("calc(100vw - {}px)".format),
)
# https://html.spec.whatwg.org/multipage/images.html#valid-source-size-list
# "auto" is left out: it is only valid together with loading="lazy".
source_size_list = st.tuples(
    st.lists(
        st.tuples(st.integers(1, 2000), _css_length).map(
            lambda condition: "(max-width: {}px) {}".format(*condition)
        ),
        max_size=2,
    ),
    _css_length,
).map(lambda sizes: ", ".join([*sizes[0], sizes[1]]))

# A URL in a srcset must not contain whitespace or start or end with a comma.
_srcset_url = non_empty_url.filter(
    lambda u: not u.startswith(",") and not u.endswith(",")
)


@st.composite
def image_candidates(draw: st.DrawFn, *, width: bool | None = None) -> str:
    """https://html.spec.whatwg.org/multipage/images.html#srcset-attributes

    All candidates use width descriptors, or none do, and descriptor values are unique.
    width=True or False forces one kind; None picks either.
    """
    size = draw(st.integers(1, 3))
    urls = draw(st.lists(_srcset_url, min_size=size, max_size=size))
    if width is None:
        width = draw(st.booleans())
    if width:
        values = st.lists(
            st.integers(1, 4000), min_size=size, max_size=size, unique=True
        )
        descriptors = [f" {w}w" for w in draw(values)]
    else:
        # A candidate without a descriptor counts as 1x.
        densities = st.sampled_from(["", " 0.5x", " 1.5x", " 2x", " 3x"])
        descriptors = draw(
            st.lists(densities, min_size=size, max_size=size, unique=True)
        )
    return ", ".join(u + d for u, d in zip(urls, descriptors, strict=True))


css_declarations = st.lists(
    st.sampled_from(
        ["color: red", "margin: 0 auto", "display: none", "font-size: 1.5em"]
    ),
    max_size=3,
).map("; ".join)

# --- attribute syntaxes -------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Syntax:
    """Strategies for each Python type an attribute with this syntax can take."""

    text: st.SearchStrategy[str] | None = None
    integer: st.SearchStrategy[int] | None = None
    tokens: st.SearchStrategy[list[str]] | None = None


def _token_syntax(tokens: st.SearchStrategy[list[str]]) -> Syntax:
    return Syntax(text=join_tokens(tokens), tokens=tokens)


# Keyed by the Value column of the attribute index.
VALUE_SYNTAX: dict[str, Syntax] = {
    "Text": Syntax(text=text),
    "CSS declarations": Syntax(text=css_declarations),
    "Event handler content attribute": Syntax(text=text),
    "ID": Syntax(text=token),
    "Valid URL potentially surrounded by spaces": Syntax(text=url_with_spaces),
    "Valid non-empty URL potentially surrounded by spaces": Syntax(
        text=non_empty_url_with_spaces
    ),
    "Valid navigable target name or keyword": Syntax(text=navigable_target),
    "Valid BCP 47 language tag": Syntax(text=language_tag),
    "Valid BCP 47 language tag or the empty string": Syntax(
        text=st.just("") | language_tag
    ),
    "Valid MIME type string": Syntax(text=mime_type),
    "Valid hash-name reference": Syntax(text=_prefixed("#", token)),
    "Valid custom element name of a defined customized built-in element": Syntax(
        text=custom_element_name
    ),
    "Valid source size list": Syntax(text=source_size_list),
    "Comma-separated list of image candidate strings": Syntax(text=image_candidates()),
    "Valid integer": Syntax(integer=st.integers()),
    "Valid non-negative integer": Syntax(integer=st.integers(min_value=0)),
    "Valid non-negative integer between 0 and 8": Syntax(integer=st.integers(0, 8)),
    "Set of space-separated tokens": _token_syntax(st.lists(token, max_size=4)),
    "Unordered set of unique space-separated tokens consisting of IDs": _token_syntax(
        st.lists(token, min_size=1, max_size=3, unique=True)
    ),
    "Unordered set of unique space-separated tokens consisting of valid absolute URLs": (
        _token_syntax(absolute_url_set)
    ),
    "Unordered set of unique space-separated tokens consisting of valid absolute URLs, defined property names, or text": (
        _token_syntax(
            st.lists(
                # Property names must not contain "." or ":".
                absolute_url | token.filter(lambda t: "." not in t and ":" not in t),
                min_size=1,
                max_size=3,
                unique=True,
            )
        )
    ),
    "Ordered set of unique space-separated tokens, none of which are identical to another, each consisting of one code point in length": (
        _token_syntax(
            st.lists(token.map(lambda t: t[0]), min_size=1, max_size=3, unique=True)
        )
    ),
}

# Attributes whose valid values are narrower than their Value syntax, keyed by
# (element name or "*" for global attributes, attribute name). The types accept the
# values these leave out; each is listed as a known gap in
# tests/type_cases/cases_attributes.py.
ATTRIBUTE_SYNTAX: dict[tuple[str, str], Syntax] = {
    # The index says Text; the element definition requires a non-empty value without
    # ASCII whitespace.
    ("*", "id"): Syntax(text=token),
    # https://html.spec.whatwg.org/multipage/links.html#linkTypes
    ("a", "rel"): _token_syntax(
        st.lists(
            st.sampled_from(
                [
                    "alternate",
                    "author",
                    "bookmark",
                    "external",
                    "help",
                    "license",
                    "next",
                    "nofollow",
                    "noopener",
                    "noreferrer",
                    "opener",
                    "prev",
                    "privacy-policy",
                    "search",
                    "tag",
                    "terms-of-service",
                ]
            ),
            max_size=3,
            unique=True,
        )
    ),
    # Each token must be a valid non-empty URL whose scheme is an HTTP(S) scheme.
    ("a", "ping"): _token_syntax(st.lists(absolute_url, max_size=3)),
    # The keyword list is open: custom command keywords start with "--".
    ("button", "command"): Syntax(
        text=st.sampled_from(
            [
                "toggle-popover",
                "show-popover",
                "hide-popover",
                "close",
                "request-close",
                "show-modal",
            ]
        )
        | _prefixed("--", text)
    ),
    # The name of a form control must not be the empty string.
    ("button", "name"): Syntax(text=non_empty_text),
}

# --- attributes of an element -------------------------------------------------

_SPEC = load_spec()


def _value_syntaxes(name: str) -> dict[str, str]:
    """Map each HTML attribute name of an element to its Value text in the index."""
    rows = [r for r in _SPEC["attributes"] if is_global(r) or name in r["elements"]]
    # Element-specific rows come last so they win over global rows of the same name.
    rows.sort(key=is_global, reverse=True)
    return {r["name"]: r["value"] for r in rows}


def _union_members(annotation: object) -> list[object]:
    if isinstance(annotation, TypeAliasType):
        return _union_members(annotation.__value__)
    if get_origin(annotation) is Union:
        return [m for arg in get_args(annotation) for m in _union_members(arg)]
    return [annotation]


def _member_strategy(
    member: object, syntax: Syntax | None, where: str
) -> st.SearchStrategy[Any] | None:
    if member is type(None):
        return None  # omission is covered by leaving the key out
    if get_origin(member) is Literal:
        return st.sampled_from(get_args(member))
    if member is bool:
        return st.booleans()
    if syntax is None:
        raise LookupError(f"{where}: no strategy for this Value syntax")
    if member is int:
        strategy = syntax.integer
    elif member is str:
        strategy = syntax.text
    elif get_origin(member) is Sequence and get_args(member) == (str,):
        strategy = syntax.tokens
    else:
        raise LookupError(f"{where}: unsupported annotation member {member!r}")
    if strategy is None:
        raise LookupError(f"{where}: no strategy for {member!r} values")
    return strategy


def attrs_class(name: str) -> type:
    instance = getattr(element, generate.html_to_python_name(name))
    return getattr(element, f"{type(instance).__name__}Attrs")


def attributes(name: str) -> st.SearchStrategy[dict[str, Any]]:
    """Keyword arguments for the element, each key optional."""
    values = _value_syntaxes(name)
    optional: dict[str, st.SearchStrategy[Any]] = {}
    for key, annotation in get_type_hints(attrs_class(name)).items():
        html_name = python_to_html_name(key)
        value_text = values[html_name]
        syntax = (
            ATTRIBUTE_SYNTAX.get((name, html_name))
            or ATTRIBUTE_SYNTAX.get((generate.GLOBAL_SCOPE, html_name))
            or VALUE_SYNTAX.get(value_text)
        )
        where = f"{name}.{html_name} ({value_text!r})"
        members = [
            s
            for m in _union_members(annotation)
            if (s := _member_strategy(m, syntax, where)) is not None
        ]
        optional[key] = st.one_of(members)
    return st.fixed_dictionaries({}, optional=optional)
