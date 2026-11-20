"""Artifact gate: names, types, sizes, encodings, HTML/CSS allowlists. Pure: no I/O."""

import pytest

from app.deploy import validate
from app.deploy.render import render_site
from app.deploy.site_model import SiteLinks
from app.deploy.validate import ArtifactError, validate_artifacts
from tests.deploy.helpers import fixture_content

LINKS = SiteLinks("https://app.example.in/api/public/waitlist", "abuse@example.in")


def good_files() -> dict[str, bytes]:
    site = render_site(
        fixture_content(),
        business_name="LocalLoop",
        host="localloop.example.in",
        price_guess=None,
        links=LINKS,
    )
    return dict(site.files)


def with_html(old: str, new: str) -> dict[str, bytes]:
    files = good_files()
    page = files["index.html"].decode()
    assert old in page, old
    files["index.html"] = page.replace(old, new, 1).encode()
    return files


def rejects(files: dict, match: str, links: SiteLinks = LINKS) -> None:
    with pytest.raises(ArtifactError, match=match):
        validate_artifacts(files, links)


def test_rendered_site_passes() -> None:
    validate_artifacts(good_files(), LINKS)


@pytest.mark.parametrize(
    "name",
    [
        "../index.html",
        "a/b.html",
        "..",
        ".hidden.html",
        "x.js",
        "x.svg",
        "x.HTML",
        "x.html.",
        "x.php.html2",
        "evil\x00.html",
        "a b.html",
        "x" * 60 + ".html",
        "",
    ],
)
def test_unsafe_file_names_rejected(name: str) -> None:
    files = good_files() | {name: b"{}"}
    rejects(files, "invalid file name")


def test_unsafe_name_is_not_echoed() -> None:
    with pytest.raises(ArtifactError) as ei:
        validate_artifacts(good_files() | {"../../etc/passwd.html": b""}, LINKS)
    assert "passwd" not in str(ei.value)


def test_missing_required_file_rejected() -> None:
    files = good_files()
    del files["styles.css"]
    rejects(files, "missing required files")


def test_too_many_files_rejected() -> None:
    files = good_files() | {f"extra{i}.json": b"{}" for i in range(validate.MAX_FILES)}
    rejects(files, "too many files")


def test_oversized_file_and_total_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    files = good_files()
    files["data.json"] = b"[" + b"0," * validate.MAX_FILE_BYTES + b"0]"
    rejects(files, "data.json: too large")
    files = good_files()  # render before lowering the limit (render validates too)
    monkeypatch.setattr(validate, "MAX_TOTAL_BYTES", 1000)
    rejects(files, "artifact too large")


@pytest.mark.parametrize(
    ("data", "match"),
    [(b"\xff\xfe", "not valid UTF-8"), ("﻿{}".encode(), "BOM"), (b"{}\x00", "NUL")],
)
def test_bad_encodings_rejected(data: bytes, match: str) -> None:
    rejects(good_files() | {"data.json": data}, match)


def test_non_bytes_content_rejected() -> None:
    rejects(good_files() | {"data.json": "{}"}, "content must be bytes")


def test_invalid_json_rejected() -> None:
    rejects(good_files() | {"manifest.json": b"{nope"}, "invalid JSON")


HEAD_END = "</head>"
BODY_END = "</body>"


@pytest.mark.parametrize(
    ("old", "new", "match"),
    [
        (BODY_END, "<script>alert(1)</script></body>", "script"),
        (BODY_END, "<SCRIPT src=x></SCRIPT></body>", "script"),
        (BODY_END, "<iframe src=x></iframe></body>", "tag <iframe>"),
        (BODY_END, "<object data=x></object></body>", "tag <object>"),
        (BODY_END, "<embed src=x></body>", "tag <embed>"),
        (BODY_END, "<svg onload=x></svg></body>", "tag <svg>"),
        (BODY_END, "<img src=x></body>", "tag <img>"),
        (BODY_END, "<style>p{}</style></body>", "tag <style>"),
        (HEAD_END, "<base href=https://evil.example></head>", "tag <base>"),
        (BODY_END, "<!-- hi --></body>", "comment"),
        (BODY_END, '<p onclick="x()">x</p></body>', "attribute 'onclick'"),
        (BODY_END, '<p style="color:red">x</p></body>', "attribute 'style'"),
        (BODY_END, '<a href="javascript:alert(1)">x</a></body>', "javascript"),
        (BODY_END, '<a href="&#106;avascript:alert(1)">x</a></body>', "link target"),
        (BODY_END, '<a href="https://evil.example">x</a></body>', "link target"),
        (BODY_END, '<a href="mailto:other@example.in">x</a></body>', "link target"),
        (BODY_END, '<a href="//evil.example">x</a></body>', "link target"),
        (BODY_END, '<p class="a" class="b">x</p></body>', "duplicate attribute"),
        (BODY_END, '<button formaction="https://evil.example">x</button></body>', "formaction"),
        (BODY_END, "<h1>second</h1></body>", "exactly one <h1>"),
        (HEAD_END, '<meta http-equiv="refresh" content="0;url=x"></head>', "meta element"),
        (HEAD_END, '<link rel="preload" href="x.js"></head>', "only <link"),
        (HEAD_END, '<link rel="stylesheet" href="https://cdn.example/x.css"></head>', "only <link"),
        ('<html lang="en">', "<html>", "lang attribute"),
        ('<meta charset="utf-8">', '<meta charset="latin1">', "charset must be utf-8"),
        ("default-src &#39;none&#39;; ", "", "Content-Security-Policy"),
        ("form-action", "script-src *; form-action", "Content-Security-Policy"),
        ("<!DOCTYPE html>", "", "DOCTYPE"),
        (
            'action="https://app.example.in/api/public/waitlist"',
            'action="https://evil.example/"',
            "form action",
        ),
        ('method="post"', 'method="get"', "method must be post"),
    ],
)
def test_dangerous_html_rejected(old: str, new: str, match: str) -> None:
    rejects(with_html(old, new), match)


def test_form_rejected_when_waitlist_not_configured() -> None:
    rejects(good_files(), "form action", links=SiteLinks(None, "abuse@example.in"))


def test_mailto_rejected_when_abuse_email_not_configured() -> None:
    rejects(good_files(), "link target", links=SiteLinks(LINKS.waitlist_action, None))


@pytest.mark.parametrize(
    "css",
    [
        "@import 'x.css';",
        "body{background:url(https://evil.example/x.png)}",
        "p{width:expression(alert(1))}",
        "p{behavior:url(x.htc)}",
        "p{-moz-binding:x}",
        "p{content:'\\3c script'}",
        "</style><script>",
    ],
)
def test_dangerous_css_rejected(css: str) -> None:
    files = good_files()
    files["styles.css"] = css.encode()
    rejects(files, "styles.css: CSS contains")
