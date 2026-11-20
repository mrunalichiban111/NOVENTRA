"""Static site renderer (FR-SITE-2, FR-SITE-3, FR-SITE-6). Pure: returns bytes, writes nothing.

The template and stylesheet are repository files. Jinja runs sandboxed with autoescaping and
StrictUndefined, and the context is plain JSON-like data (see site_model.build_site_model), so
model text can only ever appear as escaped text. Output is deterministic: the same content,
facts and template version always produce byte-identical files, which keeps deploys idempotent.
Every rendered set passes validate.validate_artifacts before it is returned.
"""

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any

from jinja2 import FileSystemLoader, StrictUndefined
from jinja2.sandbox import SandboxedEnvironment

from app.deploy.site_model import TEMPLATE_VERSION, SiteLinks, build_site_model, content_hash
from app.deploy.validate import validate_artifacts
from app.llm.schemas import WebsiteContent

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
HTML_TEMPLATE = "site.html.j2"
CSS_FILE = "site.css"


@dataclass(frozen=True)
class RenderedSite:
    files: dict[str, bytes]  # name -> bytes, including manifest.json
    manifest: dict[str, Any]
    content_hash: str
    artifact_hash: str  # sha256 of manifest.json bytes


@lru_cache(maxsize=1)
def _env() -> SandboxedEnvironment:
    return SandboxedEnvironment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=True,
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
        auto_reload=False,
    )


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def render_html(context: dict[str, Any]) -> str:
    return _env().get_template(HTML_TEMPLATE).render(**context)


def build_manifest(files: dict[str, bytes], chash: str) -> dict[str, Any]:
    return {
        "template_version": TEMPLATE_VERSION,
        "content_hash": chash,
        "files": {
            name: {"sha256": _sha(data), "bytes": len(data)} for name, data in sorted(files.items())
        },
    }


def render_site(
    content: WebsiteContent,
    *,
    business_name: str,
    host: str,
    price_guess: Decimal | None,
    links: SiteLinks,
) -> RenderedSite:
    """Render, hash and validate a site. Raises ArtifactError if the output is unsafe."""
    context = build_site_model(
        content, business_name=business_name, host=host, price_guess=price_guess, links=links
    )
    chash = content_hash(content)
    files = {
        "index.html": render_html(context).encode("utf-8"),
        "styles.css": (TEMPLATES_DIR / CSS_FILE).read_bytes(),
    }
    manifest = build_manifest(files, chash)
    manifest_bytes = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode("utf-8")
    files["manifest.json"] = manifest_bytes
    validate_artifacts(files, links)
    return RenderedSite(
        files=files, manifest=manifest, content_hash=chash, artifact_hash=_sha(manifest_bytes)
    )
