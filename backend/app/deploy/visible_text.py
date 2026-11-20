"""Visible text of a rendered release, in page section order (M5: FR-AR-2).

The persona evaluator reads only what a visitor can read on the page: the rendered HTML of the
exact release, never the WebsiteContent JSON or any assessment data. Stdlib `html.parser` only;
scripts are never executed. Skipped: <head>, script/style/noscript/template, any subtree marked
`hidden` or `aria-hidden="true"` (e.g. the form honeypot), and hidden inputs. Template-added
text (header badge, form labels, footer notes) is included because visitors see it.
Attributes such as a theme class are ignored, so styling changes never alter the text.
"""

from html.parser import HTMLParser

# Template section classes -> evaluator section names (site.html.j2).
SECTION_CLASSES = {
    "hero": "hero",
    "problem": "problem",
    "offer": "offer",
    "steps": "how_it_works",
    "how_it_works": "how_it_works",
    "proof": "proof",
    "pricing": "pricing",
    "faq": "faq",
    "cta": "cta",
}
_SKIP_TAGS = {"head", "script", "style", "noscript", "template", "svg"}
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source",
         "track", "wbr"}  # fmt: skip
_BLOCK = {"p", "h1", "h2", "h3", "h4", "li", "dt", "dd", "label", "button", "a", "div",
          "section", "header", "footer", "summary", "td", "th", "figcaption", "blockquote"}  # fmt: skip


class _Extractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, bool, str | None]] = []  # (tag, hidden, section)
        self.parts: list[tuple[str, str]] = []
        self._buf: list[str] = []

    def _hidden(self) -> bool:
        return any(h for _, h, _ in self.stack)

    def _section(self) -> str:
        for _, _, sec in reversed(self.stack):
            if sec:
                return sec
        return "other"

    def _flush(self) -> None:
        text = " ".join("".join(self._buf).split())
        self._buf = []
        if text:
            self.parts.append((self._section(), text))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag in _VOID:
            return  # inputs carry no visible text (values/placeholders are not page copy)
        if tag in _BLOCK:
            self._flush()
        hidden = tag in _SKIP_TAGS or "hidden" in a or a.get("aria-hidden", "").lower() == "true"
        section = None
        if tag == "header":
            section = "header"
        elif tag == "footer":
            section = "footer"
        elif tag == "section":
            for cls in a.get("class", "").split():
                if cls in SECTION_CLASSES:
                    section = SECTION_CLASSES[cls]
                    break
            else:
                section = "other"
        self.stack.append((tag, hidden, section))

    def handle_endtag(self, tag: str) -> None:
        if tag in _VOID:
            return
        # Pop to the matching tag (tolerates the template's well-formed HTML and stray tags).
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                if tag in _BLOCK or self.stack[i][2]:
                    self._flush()
                del self.stack[i:]
                return

    def handle_data(self, data: str) -> None:
        if not self._hidden():
            self._buf.append(data)

    def close(self) -> None:
        super().close()
        self._flush()


def extract_visible_text(html: str) -> list[tuple[str, str]]:
    """(section, text) pairs in document order; consecutive pairs of a section are merged."""
    parser = _Extractor()
    parser.feed(html)
    parser.close()
    merged: list[tuple[str, list[str]]] = []
    for section, text in parser.parts:
        if merged and merged[-1][0] == section:
            merged[-1][1].append(text)
        else:
            merged.append((section, [text]))
    return [(section, "\n".join(lines)) for section, lines in merged]


def page_text(sections: list[tuple[str, str]]) -> str:
    """Render extracted sections as the evaluator's PAGE block."""
    return "\n\n".join(f"[{section}]\n{text}" for section, text in sections)
