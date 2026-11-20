"""Hardened, versioned artifact storage for generated sites (FR-SITE-3, FR-SITE-5, M4B R5).

Layout under SITES_ROOT (Caddy later serves `<host>/current`, read-only):

    <host>/releases/v1/{index.html, styles.css, manifest.json}   immutable, 0555 dir / 0444 files
    <host>/current -> releases/vN                                 relative symlink, swapped atomically
    <host>/.staging/vN-<random>/                                  in-progress builds (never served)

Guarantees:
- No path is ever resolved through a symlink. Every directory is opened with
  O_DIRECTORY|O_NOFOLLOW relative to its parent's fd (openat-style), every file with O_NOFOLLOW,
  so a planted symlink anywhere below SITES_ROOT makes the operation fail instead of escaping.
- SITES_ROOT must already exist, be a real directory and not be group/world-writable.
- Publication is atomic: files are written to a private staging dir with O_EXCL, fsynced,
  made read-only, then the whole dir is renamed to releases/vN in one step. A reader sees either
  no vN or a complete vN.
- Releases are immutable: publishing an existing vN is a no-op when its manifest hash matches
  and a ReleaseConflict otherwise. Nothing ever overwrites or edits a release.
- Activation swaps the `current` symlink with an atomic rename and returns the previous version,
  which is how a failed deploy restores the last good release (and how M6 rolls back).

Nothing here interprets, imports or executes the stored files.
"""

import contextlib
import ctypes
import errno
import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

from app.deploy.hosts import validate_host
from app.deploy.validate import MAX_FILE_BYTES, MAX_FILES

RELEASES = "releases"
STAGING = ".staging"
CURRENT = "current"
MANIFEST = "manifest.json"
DIR_MODE = 0o755
RELEASE_DIR_MODE = 0o555
FILE_MODE = 0o444

_FILE_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,58}\.(?:html|css|json)$")
_VERSION_DIR = re.compile(r"^v([1-9][0-9]{0,8})$")
_CURRENT_TARGET = re.compile(r"^releases/v([1-9][0-9]{0,8})$")
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)


# --- no-replace rename ------------------------------------------------------------------
# A plain rename(2) silently replaces an *empty* destination directory, so a planted empty
# releases/vN could be swapped for our build. Use the kernel's atomic no-replace rename:
#   Linux (glibc >= 2.28): renameat2(..., RENAME_NOREPLACE)
#   macOS 10.12+:          renameatx_np(..., RENAME_EXCL)
# If neither exists, or the filesystem rejects the flag (EINVAL/ENOSYS/ENOTSUP), fall back to
# "refuse if the destination exists, then rename". The fallback has a tiny check-then-act window;
# it is only reachable on platforms without the primitive, and the database version lock
# (one open version per business) already prevents concurrent builds of the same version.
_RENAME_NOREPLACE = 1  # linux/fs.h
_RENAME_EXCL = 0x4  # macOS sys/stdio.h


def _load_native_rename() -> tuple[str, object] | None:
    try:
        libc = ctypes.CDLL(None, use_errno=True)
    except OSError:  # pragma: no cover - no libc handle
        return None
    for name, flag in (("renameat2", _RENAME_NOREPLACE), ("renameatx_np", _RENAME_EXCL)):
        fn = getattr(libc, name, None)
        if fn is not None:
            fn.argtypes = [
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_uint,
            ]
            fn.restype = ctypes.c_int
            return name, (fn, flag)
    return None


_NATIVE_RENAME = _load_native_rename()
NATIVE_RENAME_NAME = _NATIVE_RENAME[0] if _NATIVE_RENAME else None
_UNSUPPORTED = {errno.EINVAL, errno.ENOSYS, getattr(errno, "ENOTSUP", -1), errno.EOPNOTSUPP}


def rename_noreplace(src: str, dst: str, *, src_dir_fd: int, dst_dir_fd: int) -> None:
    """Atomically rename src -> dst (both relative to dir fds); FileExistsError if dst exists."""
    if _NATIVE_RENAME is not None:
        fn, flag = _NATIVE_RENAME[1]  # type: ignore[misc]
        if fn(src_dir_fd, os.fsencode(src), dst_dir_fd, os.fsencode(dst), flag) == 0:
            return
        err = ctypes.get_errno()
        if err == errno.EEXIST or err == errno.ENOTEMPTY:
            raise FileExistsError(err, "destination exists", dst)
        if err not in _UNSUPPORTED:
            raise OSError(err, os.strerror(err), dst)
    # Fallback: refuse any existing destination (file, dir, empty dir or symlink).
    try:
        os.stat(dst, dir_fd=dst_dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        pass
    else:
        raise FileExistsError(errno.EEXIST, "destination exists", dst)
    try:
        os.rename(src, dst, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)
    except OSError as exc:
        # Someone created dst inside the window (errno differs by OS: ENOTEMPTY, EEXIST, or
        # EACCES on macOS for a read-only dir). Report it as "exists", never retry the rename.
        try:
            os.stat(dst, dir_fd=dst_dir_fd, follow_symlinks=False)
        except FileNotFoundError:
            raise exc from None
        raise FileExistsError(errno.EEXIST, "destination exists", dst) from exc


class StorageError(Exception):
    """Base class for release-store failures."""


class UnsafePath(StorageError):
    """A symlink, non-regular file or unexpected entry was found where none may exist."""


class ReleaseConflict(StorageError):
    """vN already exists with different content. Releases are never overwritten."""


class ReleaseNotFound(StorageError):
    pass


@dataclass(frozen=True)
class ReleaseRef:
    host: str
    version: int
    artifact_hash: str
    created: bool  # False when an identical release already existed (idempotent publish)


def _check_version(version: int) -> int:
    if not isinstance(version, int) or isinstance(version, bool) or not 1 <= version < 10**9:
        raise ValueError("version must be a positive integer")
    return version


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _same_or_conflict(existing: ReleaseRef, artifact_hash: str) -> ReleaseRef:
    if existing.artifact_hash != artifact_hash:
        raise ReleaseConflict(f"v{existing.version} already exists with different content")
    return existing


def integrity_problems(files: Mapping[str, bytes]) -> list[str]:
    """Compare a release's files with its own manifest.json (exact file set, sizes, hashes)."""
    raw = files.get(MANIFEST)
    if raw is None:
        return ["missing manifest.json"]
    try:
        listed = json.loads(raw)["files"]
        assert isinstance(listed, dict)
    except (ValueError, KeyError, TypeError, AssertionError):
        return ["unreadable manifest.json"]
    problems = []
    actual = {n for n in files if n != MANIFEST}
    if actual != set(listed):
        problems.append("file set differs from manifest")
    for name in sorted(actual & set(listed)):
        entry = listed[name]
        if not isinstance(entry, dict) or entry.get("sha256") != _sha(files[name]):
            problems.append(f"{name}: hash mismatch")
        elif entry.get("bytes") != len(files[name]):
            problems.append(f"{name}: size mismatch")
    return problems


@contextlib.contextmanager
def _fd(fd: int) -> Iterator[int]:
    try:
        yield fd
    finally:
        os.close(fd)


class ReleaseStore:
    def __init__(self, root: str | os.PathLike[str], base_domain: str) -> None:
        self.root = Path(root)
        self.base_domain = base_domain

    # --- fd helpers ------------------------------------------------------------------

    def _open_root(self) -> int:
        try:
            st = os.lstat(self.root)
        except FileNotFoundError:
            raise StorageError("SITES_ROOT does not exist") from None
        if not stat.S_ISDIR(st.st_mode):  # lstat: a symlinked root is refused here
            raise UnsafePath("SITES_ROOT is not a real directory")
        if st.st_mode & 0o022:
            raise UnsafePath("SITES_ROOT must not be group- or world-writable")
        try:
            return os.open(self.root, _DIR_FLAGS)
        except OSError as exc:
            raise UnsafePath(
                f"cannot open SITES_ROOT safely ({errno.errorcode.get(exc.errno)})"
            ) from None

    @staticmethod
    def _open_dir(parent: int, name: str, *, create: bool = False) -> int:
        """Open `name` (one component) under `parent` as a directory, never via a symlink."""
        if "/" in name or name in ("", ".", ".."):
            raise UnsafePath("invalid path component")
        if create:
            try:
                os.mkdir(name, DIR_MODE, dir_fd=parent)
            except FileExistsError:
                pass
            else:
                with _fd(os.open(name, _DIR_FLAGS, dir_fd=parent)) as fd:
                    os.fchmod(fd, DIR_MODE)  # defeat a restrictive or permissive umask
        try:
            return os.open(name, _DIR_FLAGS, dir_fd=parent)
        except FileNotFoundError:
            raise
        except OSError as exc:  # ELOOP / ENOTDIR: a symlink or file where a dir must be
            raise UnsafePath(f"{name!s} is not a real directory") from exc

    def _host_dir(self, root: int, host: str, *, create: bool) -> int:
        return self._open_dir(root, validate_host(host, self.base_domain), create=create)

    @staticmethod
    def _read_files(release: int) -> dict[str, bytes]:
        """Read every entry of an open release dir; only small regular files are allowed."""
        names = os.listdir(release)
        if len(names) > MAX_FILES:
            raise UnsafePath("release has too many entries")
        files: dict[str, bytes] = {}
        for name in names:
            if not _FILE_NAME.fullmatch(name):
                raise UnsafePath("unexpected entry in release")
            st = os.stat(name, dir_fd=release, follow_symlinks=False)
            if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_FILE_BYTES:
                raise UnsafePath("release entry is not a small regular file")
            try:
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=release)
            except OSError as exc:
                raise UnsafePath("release entry cannot be opened safely") from exc
            with os.fdopen(fd, "rb") as fh:
                data = fh.read(MAX_FILE_BYTES + 1)
            if len(data) > MAX_FILE_BYTES:
                raise UnsafePath("release entry grew while reading")
            files[name] = data
        return files

    # --- publish -----------------------------------------------------------------------

    def publish(
        self, host: str, version: int, files: Mapping[str, bytes], artifact_hash: str
    ) -> ReleaseRef:
        """Write releases/vN atomically. Idempotent for identical content; never overwrites."""
        _check_version(version)
        if MANIFEST not in files or _sha(files[MANIFEST]) != artifact_hash:
            raise StorageError("artifact hash does not match manifest.json")
        if len(files) > MAX_FILES or not all(
            isinstance(n, str) and _FILE_NAME.fullmatch(n) and isinstance(d, bytes)
            for n, d in files.items()
        ):
            raise UnsafePath("invalid artifact file set")
        vname = f"v{version}"
        with (
            _fd(self._open_root()) as root,
            _fd(self._host_dir(root, host, create=True)) as hostd,
            _fd(self._open_dir(hostd, RELEASES, create=True)) as releases,
            _fd(self._open_dir(hostd, STAGING, create=True)) as staging,
        ):
            existing = self._existing(releases, host, version)
            if existing is not None:
                return _same_or_conflict(existing, artifact_hash)
            tmp = f"{vname}-{secrets.token_hex(8)}"
            os.mkdir(tmp, 0o700, dir_fd=staging)
            renamed = False
            try:
                with _fd(self._open_dir(staging, tmp)) as tmpd:
                    for name, data in sorted(files.items()):
                        fd = os.open(
                            name,
                            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                            0o600,
                            dir_fd=tmpd,
                        )
                        with os.fdopen(fd, "wb") as fh:
                            fh.write(data)
                            fh.flush()
                            os.fsync(fh.fileno())
                            os.fchmod(fh.fileno(), FILE_MODE)
                    os.fchmod(tmpd, DIR_MODE)
                    os.fsync(tmpd)
                    try:
                        rename_noreplace(tmp, vname, src_dir_fd=staging, dst_dir_fd=releases)
                    except FileExistsError:
                        # Lost a race with a concurrent publisher, or something was planted at
                        # releases/vN. Never replace it: accept only an identical, intact release
                        # (an empty or damaged dir raises UnsafePath).
                        existing = self._existing(releases, host, version)
                        if existing is None:
                            raise
                        return _same_or_conflict(existing, artifact_hash)
                    renamed = True
                    os.fchmod(tmpd, RELEASE_DIR_MODE)  # same dir, now releases/vN
                os.fsync(releases)
            finally:
                if not renamed:
                    self._remove_staging(staging, tmp)
            return ReleaseRef(host, version, artifact_hash, created=True)

    def _existing(self, releases: int, host: str, version: int) -> ReleaseRef | None:
        """The already-published vN, if any. Raises ReleaseConflict on different content."""
        try:
            vfd = self._open_dir(releases, f"v{version}")
        except FileNotFoundError:
            return None
        with _fd(vfd):
            stored = self._read_files(vfd)
        problems = integrity_problems(stored)
        if problems:
            raise UnsafePath("existing release is damaged: " + "; ".join(problems))
        return ReleaseRef(host, version, _sha(stored[MANIFEST]), created=False)

    def _remove_staging(self, staging: int, name: str) -> None:
        with contextlib.suppress(FileNotFoundError):
            with _fd(self._open_dir(staging, name)) as d:
                os.fchmod(d, 0o700)
            shutil.rmtree(name, dir_fd=staging)

    def check_existing(self, host: str, version: int, artifact_hash: str) -> ReleaseRef | None:
        """Existing vN with the expected hash, None if absent, ReleaseConflict if different."""
        _check_version(version)
        with _fd(self._open_root()) as root:
            try:
                hostd = self._host_dir(root, host, create=False)
            except FileNotFoundError:
                return None
            with _fd(hostd) as hostd:
                try:
                    releases = self._open_dir(hostd, RELEASES)
                except FileNotFoundError:
                    return None
                with _fd(releases):
                    ref = self._existing(releases, host, version)
        return None if ref is None else _same_or_conflict(ref, artifact_hash)

    def cleanup_staging(self, host: str, older_than_s: float = 3600) -> int:
        """Remove abandoned staging dirs (crashed builds). Returns how many were removed."""
        removed = 0
        with _fd(self._open_root()) as root:
            try:
                hostd = self._host_dir(root, host, create=False)
            except FileNotFoundError:
                return 0
            with _fd(hostd) as hostd:
                try:
                    staging = self._open_dir(hostd, STAGING)
                except FileNotFoundError:
                    return 0
                with _fd(staging):
                    cutoff = time.time() - older_than_s
                    for name in os.listdir(staging):
                        st = os.stat(name, dir_fd=staging, follow_symlinks=False)
                        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
                            os.unlink(name, dir_fd=staging)  # never follow, just drop it
                            removed += 1
                        elif st.st_mtime < cutoff:
                            self._remove_staging(staging, name)
                            removed += 1
        return removed

    # --- read / activate ------------------------------------------------------------------

    def read_release(self, host: str, version: int) -> dict[str, bytes]:
        _check_version(version)
        with _fd(self._open_root()) as root:
            try:
                hostd = self._host_dir(root, host, create=False)
                with _fd(hostd):
                    with _fd(self._open_dir(hostd, RELEASES)) as releases:
                        with _fd(self._open_dir(releases, f"v{version}")) as vfd:
                            return self._read_files(vfd)
            except FileNotFoundError:
                raise ReleaseNotFound(f"{host} v{version} not found") from None

    def list_versions(self, host: str) -> list[int]:
        with _fd(self._open_root()) as root:
            try:
                with _fd(self._host_dir(root, host, create=False)) as hostd:
                    with _fd(self._open_dir(hostd, RELEASES)) as releases:
                        names = os.listdir(releases)
            except FileNotFoundError:
                return []
        return sorted(int(m.group(1)) for n in names if (m := _VERSION_DIR.fullmatch(n)))

    @staticmethod
    def _read_current(hostd: int) -> int | None:
        try:
            st = os.stat(CURRENT, dir_fd=hostd, follow_symlinks=False)
        except FileNotFoundError:
            return None
        if not stat.S_ISLNK(st.st_mode):
            raise UnsafePath("current is not a symlink")
        m = _CURRENT_TARGET.fullmatch(os.readlink(CURRENT, dir_fd=hostd))
        if m is None:
            raise UnsafePath("current points outside releases/")
        return int(m.group(1))

    def active_version(self, host: str) -> int | None:
        with _fd(self._open_root()) as root:
            try:
                hostd = self._host_dir(root, host, create=False)
            except FileNotFoundError:
                return None
            with _fd(hostd):
                return self._read_current(hostd)

    def activate(self, host: str, version: int) -> int | None:
        """Atomically point `current` at releases/vN. Returns the previously active version."""
        _check_version(version)
        with _fd(self._open_root()) as root:
            try:
                hostd = self._host_dir(root, host, create=False)
            except FileNotFoundError:
                raise ReleaseNotFound(f"{host} has no releases") from None
            with _fd(hostd):
                try:
                    with _fd(self._open_dir(hostd, RELEASES)) as releases:
                        with _fd(self._open_dir(releases, f"v{version}")) as vfd:
                            if MANIFEST not in self._read_files(vfd):
                                raise UnsafePath("release has no manifest")
                except FileNotFoundError:
                    raise ReleaseNotFound(f"{host} v{version} not found") from None
                previous = self._read_current(hostd)
                tmp = f".current-{secrets.token_hex(8)}"
                os.symlink(f"{RELEASES}/v{version}", tmp, dir_fd=hostd)
                try:
                    os.replace(tmp, CURRENT, src_dir_fd=hostd, dst_dir_fd=hostd)
                except OSError:
                    with contextlib.suppress(FileNotFoundError):
                        os.unlink(tmp, dir_fd=hostd)
                    raise
                os.fsync(hostd)
                return previous
