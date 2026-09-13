"""Type-level conformance cases, checked by both ty and pyright (never executed).

accepted:   valid per the HTML Standard; must type-check cleanly.
rejected:   invalid per the HTML Standard; must be reported. Each line has an
            ignore comment per checker, and both checkers fail on unused ignores,
            so a case that stops being reported fails the check.
known gaps: invalid per the Standard but accepted by the types. Listed so the gap
            stays visible; if a checker starts reporting one, move it to rejected.
"""

# Rejected expressions have no type; that is expected here, not a finding.
# pyright: reportUnknownVariableType=false

from pyhtml.element import a, br, button, div, img, li

# --- accepted -----------------------------------------------------------------

a(href="/docs", target="preview")  # any valid navigable target name, not only keywords
a(href="/docs", target="_blank", rel=["noopener", "noreferrer"])
a(href="/report.pdf", download="")  # the Value is Text, so the empty string is valid
a(href="/report.pdf", download="report-2026.pdf")
a(href="/x", ping=["https://a.example/p", "https://b.example/p"], hreflang="ja")
a(href="/x", referrerpolicy="", type="text/html")
a(href=None)
div(class_="card", id="main", tabindex=-1, is_="fancy-div", title="t")
div(hidden=True)
div(hidden=False)
div(hidden="until-found")
div(spellcheck=True)  # the empty string selects the true state
div(spellcheck="false", translate="no", dir="auto", contenteditable="plaintext-only")
div(onclick="toggle()")
button(type="button", disabled=True, command="--open-menu", commandfor="menu")
img(src="/a.png", alt="", width=640, height=480, loading="lazy", decoding="async")
li(value=-3)
_: object = div({"hx-get": "/items", "data-id": 1})["child"]

# --- rejected -----------------------------------------------------------------

a(referrerpolicy="none")  # ty: ignore[invalid-argument-type]  # pyright: ignore[reportArgumentType]
div(dir="left")  # ty: ignore[invalid-argument-type]  # pyright: ignore[reportArgumentType]
div(spellcheck=False)  # ty: ignore[invalid-argument-type]  # pyright: ignore[reportArgumentType]
div(hidden="yes")  # ty: ignore[invalid-argument-type]  # pyright: ignore[reportArgumentType]
button(type="text")  # ty: ignore[invalid-argument-type]  # pyright: ignore[reportArgumentType]
img(loading="later")  # ty: ignore[invalid-argument-type]  # pyright: ignore[reportArgumentType]
img(width="640")  # ty: ignore[invalid-argument-type]  # pyright: ignore[reportArgumentType]
li(value=1.5)  # ty: ignore[invalid-argument-type]  # pyright: ignore[reportArgumentType]
_: object = img["child"]  # ty: ignore[not-subscriptable]  # pyright: ignore[reportIndexIssue]
_: object = br["child"]  # ty: ignore[not-subscriptable]  # pyright: ignore[reportIndexIssue]

# Unknown or misplaced attribute names. ty 0.0.80 does not report unknown keywords
# passed to **kwargs: Unpack[TypedDict], so these lines only carry a pyright ignore.
# Once ty reports them, its unsuppressed error fails the check: add the ty ignore then.
a(hreff="/x")  # pyright: ignore[reportCallIssue]
div(href="/x")  # pyright: ignore[reportCallIssue]
br(src="/x")  # pyright: ignore[reportCallIssue]

# --- known gaps ---------------------------------------------------------------

img(width=-1)  # must be a valid non-negative integer
div(tabindex=True)  # bool is an int, and renders a bare tabindex
a(href="http://[bad")  # URL syntax is not checked
a(href="/x", target="_top-frame")  # names starting with "_" must be one of the keywords
a(download="x")  # download, target, rel, ... must be omitted when href is absent
_: object = div[b"x"]  # bytes is an Iterable[int]; rejected when the element is built
