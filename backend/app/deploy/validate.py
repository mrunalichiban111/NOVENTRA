"""Artifact gate for generated sites (FR-SITE-6, M4B security boundary). Pure, no I/O.

Runs on the rendered file set before anything is written to disk. It does not trust the
template or the renderer: names, types, sizes, encodings, HTML structure, attributes, link
targets and CSS are all checked against allowlists. Nothing here executes or loads the files.
"""

import json
import re
from collections.abc import Mapping
from html.parser import HTMLParser

from app.deploy.site_model import SiteLinks

MAX_FILES = 8
MAX_FILE_BYTES = 256 * 1024
MAX_TOTAL_BYTES = 1024 * 1024
REQUIRED = frozenset({"index.html", "styles.css", "manifest.json"})

# One plain name per file: no directories, no leading dot, exactly one allowed extension.
_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,58}\.(?:html|css|json)$")

_TAGS = frozenset(
    "html head meta title link body header main section footer div span p a ul ol li "
    "h1 h2 h3 strong em small br details summary form label input button".split()
)
_ATTRS = frozenset(
    "class id href lang charset name content http-equiv rel type for required autocomplete "
    "tabindex action method value maxlength aria-labelledby aria-hidden".split()
)
_META_NAMES = frozenset({"viewport", "referrer", "description"})
_ANCHOR = re.compile(r"^#[a-z][a-z0-9-]{0,40}$")
_RAW_DANGER = re.compile(r"<\s*script|javascript\s*:|vbscript\s*:|<!--", re.IGNORECASE)
_CSS_DANGER = re.compile(
    r"@import|url\s*\(|expression\s*\(|javascript\s*:|behavior\s*:|-moz-binding|</|<!--|\\",
    re.IGNORECASE,
)
_CSP_REQUIRED = ("default-src 'none'", "base-uri 'none'")


class ArtifactError(ValueError):
    """A generated artifact failed validation. Never publish it."""


class _Inspector(HTMLParser):
    def __init__(self, links: SiteLinks) -> None:
        super().__init__(convert_charrefs=True)
        self.links = links
        self.errors: list[str] = []
        self.h1 = 0
        self.lang = False
        self.charset = False
        self.csp: str | None = None
        self.decls: list[str] = []

    def handle_decl(self, decl: str) -> None:
        self.decls.append(decl)
        if decl.strip().lower() != "doctype html":
            self.errors.append("unexpected declaration")

    def handle_pi(self, data: str) -> None:
        self.errors.append("processing instruction not allowed")

    def handle_comment(self, data: str) -> None:
        self.errors.append("HTML comments not allowed")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in _TAGS:
            self.errors.append(f"tag <{tag}> not allowed")
            return
        a: dict[str, str] = {}
        for name, value in attrs:
            if name not in _ATTRS:
                self.errors.append(f"attribute {name!r} on <{tag}> not allowed")
            if name in a:
                self.errors.append(f"duplicate attribute {name!r} on <{tag}>")
            a[name] = value or ""
        if tag == "h1":
            self.h1 += 1
        elif tag == "html":
            self.lang = bool(a.get("lang"))
        elif tag == "meta":
            self._meta(a)
        elif tag == "link":
            if a.get("rel") != "stylesheet" or a.get("href") != "styles.css" or len(a) != 2:
                self.errors.append("only <link rel=stylesheet href=styles.css> is allowed")
        elif tag == "form":
            if not self.links.waitlist_action or a.get("action") != self.links.waitlist_action:
                self.errors.append("form action is not the configured waitlist URL")
            if a.get("method") != "post":
                self.errors.append("form method must be post")
        if "href" in a and tag != "link":
            self._href(a["href"])
        if tag != "form" and "action" in a:
            self.errors.append(f"action attribute on <{tag}> not allowed")

    def _meta(self, a: dict[str, str]) -> None:
        if set(a) == {"charset"}:
            self.charset = a["charset"].lower() == "utf-8"
            if not self.charset:
                self.errors.append("charset must be utf-8")
        elif set(a) == {"http-equiv", "content"} and a["http-equiv"] == "Content-Security-Policy":
            if self.csp is not None:
                self.errors.append("more than one CSP meta")
            self.csp = a["content"]
        elif set(a) == {"name", "content"} and a["name"] in _META_NAMES:
            pass
        else:
            self.errors.append("meta element not allowed")

    def _href(self, href: str) -> None:
        if _ANCHOR.fullmatch(href):
            return
        if self.links.abuse_email and href == f"mailto:{self.links.abuse_email}":
            return
        self.errors.append("link target not allowed")


def _html(text: str, links: SiteLinks) -> list[str]:
    errors = []
    if _RAW_DANGER.search(text):
        errors.append("script, javascript: URL or comment found")
    inspector = _Inspector(links)
    inspector.feed(text)
    inspector.close()
    errors += inspector.errors
    if inspector.decls != ["DOCTYPE html"]:
        errors.append("must start with exactly one <!DOCTYPE html>")
    if inspector.h1 != 1:
        errors.append("must contain exactly one <h1>")
    if not inspector.lang:
        errors.append("<html> needs a lang attribute")
    if not inspector.charset:
        errors.append("missing <meta charset=utf-8>")
    csp = inspector.csp or ""
    if not all(part in csp for part in _CSP_REQUIRED) or "script-src" in csp:
        errors.append("missing or weak Content-Security-Policy")
    return errors


def _css(text: str) -> list[str]:
    return (
        ["CSS contains imports, URLs, expressions or escapes"] if _CSS_DANGER.search(text) else []
    )


def _json(text: str) -> list[str]:
    try:
        json.loads(text)
    except json.JSONDecodeError:
        return ["invalid JSON"]
    return []


def validate_artifacts(files: Mapping[str, bytes], links: SiteLinks) -> None:
    """Raise ArtifactError listing every problem; return None if the set is safe to publish."""
    errors: list[str] = []
    if len(files) > MAX_FILES:
        errors.append(f"too many files ({len(files)} > {MAX_FILES})")
    missing = REQUIRED - set(files)
    if missing:
        errors.append(f"missing required files: {sorted(missing)}")
    total = 0
    for name, data in files.items():
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            errors.append("invalid file name")  # never echo a hostile name
            continue
        if not isinstance(data, bytes):
            errors.append(f"{name}: content must be bytes")
            continue
        total += len(data)
        if len(data) > MAX_FILE_BYTES:
            errors.append(f"{name}: too large ({len(data)} bytes)")
            continue
        try:
            text = data.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            errors.append(f"{name}: not valid UTF-8")
            continue
        if "\x00" in text or text.startswith("﻿"):
            errors.append(f"{name}: NUL byte or BOM")
            continue
        ext = name.rsplit(".", 1)[1]
        checker = {"html": lambda t: _html(t, links), "css": _css, "json": _json}[ext]
        errors += [f"{name}: {e}" for e in checker(text)]
    if total > MAX_TOTAL_BYTES:
        errors.append(f"artifact too large ({total} bytes)")
    if errors:
        raise ArtifactError("; ".join(errors))
