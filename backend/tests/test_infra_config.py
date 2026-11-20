"""Configuration assertions for M4B site-volume isolation and safe defaults.

Static checks of the repository's Compose files and Dockerfile (no Docker needed). They pin:
only the worker mounts the generated sites read-write, Caddy mounts them read-only, the API and
everything else not at all; /srv/sites belongs to the worker uid; deployment is off by default;
the production Caddyfile is not turned into a wildcard/on-demand config by accident.
"""

import os
import re
import stat
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SITES = "/srv/sites"
WORKER_UID = 10001


def compose(name: str) -> dict:
    return yaml.safe_load((ROOT / name).read_text())


def sites_mounts(service: dict) -> list[str]:
    return [v for v in service.get("volumes", []) if str(v).split(":")[1:2] == [SITES]]


@pytest.mark.parametrize("name", ["docker-compose.yml", "docker-compose.override.yml"])
def test_only_the_worker_mounts_sites_read_write(name: str) -> None:
    services = compose(name)["services"]
    worker = sites_mounts(services["worker"])
    assert len(worker) == 1 and not worker[0].endswith(":ro")
    caddy = sites_mounts(services["caddy"])
    assert len(caddy) == 1 and caddy[0].endswith(":ro")
    for other in set(services) - {"worker", "caddy"}:
        assert sites_mounts(services[other]) == [], f"{other} must not mount {SITES}"


def test_local_compose_file_adds_no_sites_mounts() -> None:
    services = compose("docker-compose.local.yml")["services"]
    assert all(sites_mounts(s) == [] for s in services.values())


def test_dockerfile_creates_sites_dir_owned_by_the_worker_uid() -> None:
    text = (ROOT / "backend" / "Dockerfile").read_text()
    assert re.search(rf"useradd .*--uid {WORKER_UID} appuser", text)
    assert f"mkdir -p {SITES}" in text
    assert f"chown {WORKER_UID}:{WORKER_UID} {SITES}" in text
    assert f"chmod 0755 {SITES}" in text  # ReleaseStore refuses group/world-writable roots
    assert "USER appuser" in text  # the services run as uid 10001, not root


@pytest.mark.skipif(not os.path.isdir(SITES), reason="not running in the built backend image")
def test_image_sites_dir_ownership() -> None:  # pragma: no cover - only inside the new image
    st = os.stat(SITES)
    assert st.st_uid == WORKER_UID and stat.S_IMODE(st.st_mode) == 0o755


def env(service: str) -> dict:
    return compose("docker-compose.yml")["services"][service]["environment"]


def test_deployment_is_off_by_default_and_secrets_have_no_defaults() -> None:
    for service in ("api", "worker"):
        e = env(service)
        assert e["DEPLOY_PROVIDER"] == "${DEPLOY_PROVIDER:-}"
        assert e["SITE_DNS_MODE"] == "${SITE_DNS_MODE:-skip}"
        assert e["SITE_HTTP_CHECK"] == "${SITE_HTTP_CHECK:-}"
        assert e["APP_ENV"] == "${APP_ENV:-production}"  # production guards unless overridden
    assert env("api")["INTERNAL_TLS_ASK_TOKEN"] == "${INTERNAL_TLS_ASK_TOKEN:-}"
    assert "INTERNAL_TLS_ASK_TOKEN" not in env("worker")  # only the API checks it
    assert env("worker")["SITES_ROOT"] == SITES
    assert "SITES_ROOT" not in env("api")  # the API never touches site files


def test_production_caddyfile_is_unchanged_by_m4b() -> None:
    text = (ROOT / "infra" / "Caddyfile").read_text()
    assert "on_demand" not in text and "/srv/sites" not in text and "internal" not in text
