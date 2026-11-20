"""Strict host validation for generated sites (FR-BR-5, FR-SITE-3). Pure, no I/O.

A site host is exactly one DNS label followed by the configured apex: `<slug>.<BASE_DOMAIN>`.
The check is exact and never normalises: no uppercase, wildcard, trailing dot, port, path,
whitespace or other apex is accepted. The host is used as a directory name under SITES_ROOT,
so this check is also the first path-traversal guard (the store adds fd-level guards).
"""

import re

from app import config

_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_DOMAIN = re.compile(rf"^(?=.{{1,253}}$){_LABEL}(?:\.{_LABEL})+$")


class HostError(ValueError):
    """The host is not a valid `<label>.<BASE_DOMAIN>` site host."""


def validate_base_domain(base_domain: str) -> str:
    if not isinstance(base_domain, str) or not _DOMAIN.fullmatch(base_domain):
        raise HostError("BASE_DOMAIN is not set or not a valid lowercase domain")
    return base_domain


def validate_host(host: object, base_domain: str | None = None) -> str:
    """Return `host` unchanged if it is exactly `<label>.<base_domain>`; else raise HostError."""
    base = validate_base_domain(config.BASE_DOMAIN if base_domain is None else base_domain)
    if not isinstance(host, str) or len(host) > 253:
        raise HostError("invalid site host")
    m = re.fullmatch(rf"({_LABEL})\.{re.escape(base)}", host)
    if m is None or len(m.group(1)) > 63:
        raise HostError("site host must be <label>.<BASE_DOMAIN>")
    return host
