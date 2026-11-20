"""ReleaseStore: atomic immutable publication, activation, rollback and symlink/traversal
attacks (FR-SITE-3, FR-SITE-5, M4B R3/R5). Uses tmp_path only; never touches /srv or Caddy.
"""

import errno
import os
import stat
import threading
import time
from pathlib import Path

import pytest

from app.deploy import storage
from app.deploy.hosts import HostError
from app.deploy.storage import (
    ReleaseConflict,
    ReleaseNotFound,
    ReleaseStore,
    StorageError,
    UnsafePath,
    integrity_problems,
)
from tests.deploy.helpers import BASE, HOST, rendered


@pytest.fixture
def root(tmp_path: Path) -> Path:
    r = tmp_path / "sites"
    r.mkdir(mode=0o755)
    r.chmod(0o755)
    return r


@pytest.fixture
def store(root: Path) -> ReleaseStore:
    return ReleaseStore(root, BASE)


def publish(store: ReleaseStore, version: int = 1, headline: str | None = None):  # noqa: ANN201
    site = rendered(headline)
    return store.publish(HOST, version, site.files, site.artifact_hash), site


def writable(path: Path) -> None:
    """Tests only: make an immutable release dir writable so an attack can be planted."""
    path.chmod(0o755)


# --- publication ------------------------------------------------------------------------


def test_publish_writes_an_immutable_release(store: ReleaseStore, root: Path) -> None:
    ref, site = publish(store)
    assert (ref.version, ref.created, ref.artifact_hash) == (1, True, site.artifact_hash)
    rel = root / HOST / "releases" / "v1"
    assert stat.S_IMODE(rel.stat().st_mode) == 0o555
    for name, data in site.files.items():
        assert (rel / name).read_bytes() == data
        assert stat.S_IMODE((rel / name).stat().st_mode) == 0o444
    assert stat.S_IMODE((root / HOST).stat().st_mode) == 0o755
    assert list((root / HOST / ".staging").iterdir()) == []  # nothing left behind
    assert not (root / HOST / "current").exists()  # publishing never activates


def test_republishing_identical_content_is_a_noop(store: ReleaseStore, root: Path) -> None:
    publish(store)
    before = (root / HOST / "releases" / "v1" / "index.html").stat().st_mtime_ns
    ref, _ = publish(store)
    assert ref.created is False
    assert (root / HOST / "releases" / "v1" / "index.html").stat().st_mtime_ns == before
    assert store.check_existing(HOST, 1, ref.artifact_hash) == ref


def test_different_content_never_overwrites_a_release(store: ReleaseStore, root: Path) -> None:
    _, original = publish(store)
    with pytest.raises(ReleaseConflict):
        publish(store, headline="A different headline")
    assert store.read_release(HOST, 1) == original.files
    other = rendered("A different headline")
    with pytest.raises(ReleaseConflict):
        store.check_existing(HOST, 1, other.artifact_hash)
    assert store.check_existing(HOST, 2, other.artifact_hash) is None


def test_publish_refuses_mismatched_hash_and_bad_file_sets(store: ReleaseStore) -> None:
    site = rendered()
    with pytest.raises(StorageError, match="artifact hash"):
        store.publish(HOST, 1, site.files, "0" * 64)
    files = dict(site.files) | {"../evil.html": b"x"}
    with pytest.raises(UnsafePath):
        store.publish(HOST, 1, files, site.artifact_hash)
    with pytest.raises(ValueError):
        store.publish(HOST, 0, site.files, site.artifact_hash)
    assert store.list_versions(HOST) == []


@pytest.mark.parametrize(
    "host",
    [
        "../etc",
        "..",
        "tiffo.example.in/../../x",
        "TIFFO.example.in",
        "tiffo.example.in.",
        "*.example.in",
        "a.b.example.in",
        "tiffo.other.in",
        "tiffo.example.in:443",
        " tiffo.example.in",
        "example.in",
        "-x.example.in",
    ],
)
def test_bad_hosts_are_refused_before_any_write(store: ReleaseStore, root: Path, host: str) -> None:
    site = rendered()
    with pytest.raises(HostError):
        store.publish(host, 1, site.files, site.artifact_hash)
    assert list(root.iterdir()) == []


def test_failed_publish_leaves_no_partial_release(
    store: ReleaseStore, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken_rename(*a, **kw):  # noqa: ANN002, ANN003, ANN202
        raise OSError(errno.EIO, "simulated I/O error")

    monkeypatch.setattr(storage, "rename_noreplace", broken_rename)
    with pytest.raises(OSError):
        publish(store)
    monkeypatch.undo()
    assert store.list_versions(HOST) == []
    assert list((root / HOST / ".staging").iterdir()) == []
    ref, _ = publish(store)  # a clean retry works
    assert ref.created


def test_concurrent_identical_publication_creates_exactly_once(store: ReleaseStore) -> None:
    site = rendered()
    barrier = threading.Barrier(8)
    results: list = []

    def worker() -> None:
        barrier.wait()
        try:
            results.append(store.publish(HOST, 1, site.files, site.artifact_hash))
        except Exception as exc:  # pragma: no cover - reported via results
            results.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert all(not isinstance(r, Exception) for r in results), results
    assert sum(r.created for r in results) == 1
    assert store.read_release(HOST, 1) == site.files


def test_concurrent_conflicting_publication_has_one_winner(store: ReleaseStore) -> None:
    sites = [rendered(f"Headline number {i}") for i in range(6)]
    barrier = threading.Barrier(len(sites))
    results: list = []

    def worker(site) -> None:  # noqa: ANN001
        barrier.wait()
        try:
            results.append((site, store.publish(HOST, 1, site.files, site.artifact_hash)))
        except ReleaseConflict as exc:
            results.append((site, exc))

    threads = [threading.Thread(target=worker, args=(s,)) for s in sites]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    winners = [(s, r) for s, r in results if not isinstance(r, Exception)]
    assert len(winners) == 1 and winners[0][1].created
    assert store.read_release(HOST, 1) == winners[0][0].files
    assert len(results) == len(sites)


def test_cleanup_staging_removes_stale_and_planted_entries(
    store: ReleaseStore, root: Path, tmp_path: Path
) -> None:
    publish(store)
    staging = root / HOST / ".staging"
    stale = staging / "v9-deadbeef"
    stale.mkdir()
    (stale / "index.html").write_text("x")
    old = time.time() - 7200
    os.utime(stale, (old, old))
    fresh = staging / "v10-cafe"
    fresh.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep")
    (staging / "link").symlink_to(outside)
    assert store.cleanup_staging(HOST, older_than_s=3600) == 2
    assert sorted(p.name for p in staging.iterdir()) == ["v10-cafe"]
    assert (outside / "keep.txt").read_text() == "keep"  # symlink removed, target untouched


# --- activation and rollback ----------------------------------------------------------------


def test_activation_swaps_a_relative_symlink_and_returns_previous(
    store: ReleaseStore, root: Path
) -> None:
    publish(store, 1)
    publish(store, 2, headline="Second version")
    assert store.active_version(HOST) is None
    assert store.activate(HOST, 1) is None
    current = root / HOST / "current"
    assert current.is_symlink() and os.readlink(current) == "releases/v1"
    assert store.activate(HOST, 2) == 1
    assert os.readlink(current) == "releases/v2" and store.active_version(HOST) == 2
    assert not [p for p in (root / HOST).iterdir() if p.name.startswith(".current-")]


def test_rollback_restores_the_previous_release_quickly(store: ReleaseStore, root: Path) -> None:
    _, v1 = publish(store, 1)
    publish(store, 2, headline="Second version")
    store.activate(HOST, 1)
    previous = store.activate(HOST, 2)
    started = time.monotonic()
    store.activate(HOST, previous)
    assert time.monotonic() - started < 10  # FR-SITE-5
    assert (root / HOST / "current" / "index.html").read_bytes() == v1.files["index.html"]
    assert store.list_versions(HOST) == [1, 2]  # nothing deleted


def test_activating_a_missing_version_fails_and_keeps_current(store: ReleaseStore) -> None:
    with pytest.raises(ReleaseNotFound):
        store.activate(HOST, 1)
    publish(store, 1)
    store.activate(HOST, 1)
    with pytest.raises(ReleaseNotFound):
        store.activate(HOST, 7)
    assert store.active_version(HOST) == 1


# --- symlink and traversal attacks ----------------------------------------------------------


def outside_release(tmp_path: Path) -> Path:
    """A complete, valid-looking release outside SITES_ROOT."""
    site = rendered("Attacker controlled")
    out = tmp_path / "outside" / "v1"
    out.mkdir(parents=True)
    for name, data in site.files.items():
        (out / name).write_bytes(data)
    return out


def test_symlinked_root_is_refused(tmp_path: Path, root: Path) -> None:
    link = tmp_path / "linked-sites"
    link.symlink_to(root)
    with pytest.raises(UnsafePath):
        publish(ReleaseStore(link, BASE))


def test_missing_or_world_writable_root_is_refused(tmp_path: Path, root: Path) -> None:
    with pytest.raises(StorageError, match="does not exist"):
        publish(ReleaseStore(tmp_path / "nope", BASE))
    root.chmod(0o777)
    try:
        with pytest.raises(UnsafePath, match="writable"):
            publish(ReleaseStore(root, BASE))
    finally:
        root.chmod(0o755)


def test_symlinked_host_dir_is_refused(store: ReleaseStore, root: Path, tmp_path: Path) -> None:
    target = tmp_path / "elsewhere"
    target.mkdir()
    (root / HOST).symlink_to(target)
    with pytest.raises(UnsafePath):
        publish(store)
    with pytest.raises(UnsafePath):
        store.active_version(HOST)
    assert list(target.iterdir()) == []  # nothing written through the link


@pytest.mark.parametrize("component", ["releases", ".staging"])
def test_symlinked_internal_dirs_are_refused(
    store: ReleaseStore, root: Path, tmp_path: Path, component: str
) -> None:
    target = tmp_path / "elsewhere"
    target.mkdir()
    (root / HOST).mkdir()
    (root / HOST / component).symlink_to(target)
    with pytest.raises(UnsafePath):
        publish(store)
    assert list(target.iterdir()) == []


def test_symlinked_version_dir_is_never_read_or_activated(
    store: ReleaseStore, root: Path, tmp_path: Path
) -> None:
    publish(store, 1)
    releases = root / HOST / "releases"
    (releases / "v2").symlink_to(outside_release(tmp_path))
    with pytest.raises(UnsafePath):
        store.read_release(HOST, 2)
    with pytest.raises(UnsafePath):
        store.activate(HOST, 2)
    with pytest.raises(UnsafePath):
        publish(store, 2)  # does not treat the planted link as an existing release
    assert store.active_version(HOST) is None


def test_symlinked_file_inside_a_release_is_refused(
    store: ReleaseStore, root: Path, tmp_path: Path
) -> None:
    publish(store, 1)
    rel = root / HOST / "releases" / "v1"
    writable(rel)
    secret = tmp_path / "secret.txt"
    secret.write_text("do not serve")
    (rel / "index.html").unlink()
    (rel / "index.html").symlink_to(secret)
    with pytest.raises(UnsafePath):
        store.read_release(HOST, 1)
    with pytest.raises(UnsafePath):
        store.activate(HOST, 1)


@pytest.mark.parametrize("entry", ["evil.js", ".htaccess", "sub"])
def test_unexpected_entries_inside_a_release_are_refused(
    store: ReleaseStore, root: Path, entry: str
) -> None:
    publish(store, 1)
    rel = root / HOST / "releases" / "v1"
    writable(rel)
    if entry == "sub":
        (rel / entry).mkdir()
    else:
        (rel / entry).write_text("x")
    with pytest.raises(UnsafePath):
        store.read_release(HOST, 1)


def test_damaged_existing_release_is_not_reused(store: ReleaseStore, root: Path) -> None:
    publish(store, 1)
    page = root / HOST / "releases" / "v1" / "index.html"
    writable(page.parent)
    page.chmod(0o644)
    page.write_bytes(page.read_bytes() + b" ")
    with pytest.raises(UnsafePath, match="damaged"):
        publish(store, 1)


def test_current_must_be_a_symlink_into_releases(store: ReleaseStore, root: Path) -> None:
    publish(store, 1)
    current = root / HOST / "current"
    current.symlink_to("../../etc")
    with pytest.raises(UnsafePath, match="outside"):
        store.active_version(HOST)
    current.unlink()
    current.symlink_to("/srv/sites/other.example.in/releases/v1")
    with pytest.raises(UnsafePath):
        store.active_version(HOST)
    current.unlink()
    current.mkdir()
    with pytest.raises(UnsafePath, match="not a symlink"):
        store.active_version(HOST)
    with pytest.raises(UnsafePath):
        store.activate(HOST, 1)


def test_integrity_problems() -> None:
    files = dict(rendered().files)
    assert integrity_problems(files) == []
    assert integrity_problems({k: v for k, v in files.items() if k != "styles.css"}) == [
        "file set differs from manifest"
    ]
    assert integrity_problems(files | {"index.html": b"changed"}) == ["index.html: hash mismatch"]
    assert integrity_problems(files | {"manifest.json": b"{"}) == ["unreadable manifest.json"]
    assert integrity_problems({}) == ["missing manifest.json"]


# --- no-replace rename (planted empty destination) ------------------------------------------


@pytest.fixture(params=["native", "fallback"])
def rename_mode(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    if request.param == "fallback":
        monkeypatch.setattr(storage, "_NATIVE_RENAME", None)
    elif storage._NATIVE_RENAME is None:  # pragma: no cover - platform without the primitive
        pytest.skip("no native no-replace rename on this platform")
    return request.param


def test_native_primitive_is_available_on_supported_platforms() -> None:
    import sys

    expected = {"linux": "renameat2", "darwin": "renameatx_np"}.get(sys.platform)
    if expected is None:  # pragma: no cover
        pytest.skip("unsupported platform")
    assert storage.NATIVE_RENAME_NAME == expected


def test_planted_empty_version_dir_is_never_replaced(
    store: ReleaseStore, root: Path, rename_mode: str
) -> None:
    publish(store, 1)  # creates the host layout
    planted = root / HOST / "releases" / "v2"
    planted.mkdir()
    with pytest.raises(UnsafePath, match="damaged"):
        publish(store, 2, headline="Second version")
    assert planted.is_dir() and list(planted.iterdir()) == []  # untouched, not swapped
    assert list((root / HOST / ".staging").iterdir()) == []  # our staging was cleaned up


def test_rename_noreplace_refuses_every_existing_destination(
    tmp_path: Path, rename_mode: str
) -> None:
    base = tmp_path / "r"
    base.mkdir()
    fd = os.open(base, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for kind in ("empty_dir", "file", "symlink"):
            (base / "src").mkdir()
            dst = base / kind
            if kind == "empty_dir":
                dst.mkdir()
            elif kind == "file":
                dst.write_text("x")
            else:
                dst.symlink_to(tmp_path)
            with pytest.raises(FileExistsError):
                storage.rename_noreplace("src", kind, src_dir_fd=fd, dst_dir_fd=fd)
            assert (base / "src").is_dir()
            (base / "src").rmdir()
        (base / "src").mkdir()
        storage.rename_noreplace("src", "fresh", src_dir_fd=fd, dst_dir_fd=fd)
        assert (base / "fresh").is_dir() and not (base / "src").exists()
    finally:
        os.close(fd)


def test_concurrent_publication_is_safe_on_the_fallback_path(
    store: ReleaseStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(storage, "_NATIVE_RENAME", None)
    test_concurrent_identical_publication_creates_exactly_once(store)
