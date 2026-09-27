"""Restricted, size-bounded task workspaces for hydraulic jobs.

The sandbox deliberately exposes only relative paths.  Callers never pass a
client filename to a final path, and every existing path component is checked
for traversal and reparse-point (Windows junction/symlink) escapes.
"""

from __future__ import annotations

import ctypes
import logging
import os
import re
import shutil
import stat
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Iterator
from uuid import UUID

logger = logging.getLogger(__name__)

MIB = 1024 * 1024
DEFAULT_INPUT_MAX_BYTES = 20 * MIB
DEFAULT_WORKSPACE_MAX_BYTES = 2 * 1024 * MIB
DEFAULT_FRAME_MAX_BYTES = 64 * MIB
DEFAULT_OUTPUT_MAX_BYTES = 8 * 1024 * MIB

_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class SandboxError(RuntimeError):
    """Base error for task sandbox failures."""

    code = "SANDBOX_ERROR"


class UnsafeSandboxPath(SandboxError):
    """Raised when a requested path can escape or alias the task workspace."""

    code = "UNSAFE_PATH"


class SandboxLimitExceeded(SandboxError):
    """Raised when task material exceeds its configured byte budget."""

    code = "OUTPUT_LIMIT_EXCEEDED"


class SandboxCleanupError(SandboxError):
    """Raised when final cleanup cannot remove an attempt workspace."""

    code = "SANDBOX_CLEANUP_PENDING"


class ProcessTreeJob:
    """Own a Windows kill-on-close Job Object for one solver process tree.

    POSIX intentionally has no handle here; callers use the independent process
    group created by ``Popen(start_new_session=True)``.  On Windows the job is
    retained after the root process exits, so descendants cannot escape cleanup
    merely because the solver parent returned first.
    """

    def __init__(self) -> None:
        self._handle: int | None = None
        self._closed = False

    @classmethod
    def create(cls) -> "ProcessTreeJob":
        self = cls()
        if os.name != "nt":
            return self
        from ctypes import wintypes

        class _BasicLimitInformation(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class _IoCounters(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class _ExtendedLimitInformation(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", _BasicLimitInformation),
                ("IoInfo", _IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_job = kernel32.CreateJobObjectW
        create_job.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        create_job.restype = wintypes.HANDLE
        set_info = kernel32.SetInformationJobObject
        set_info.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        set_info.restype = wintypes.BOOL
        assign = kernel32.AssignProcessToJobObject
        assign.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        assign.restype = wintypes.BOOL
        terminate = kernel32.TerminateJobObject
        terminate.argtypes = [wintypes.HANDLE, wintypes.UINT]
        terminate.restype = wintypes.BOOL
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [wintypes.HANDLE]
        close_handle.restype = wintypes.BOOL

        handle = create_job(None, None)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        info = _ExtendedLimitInformation()
        info.BasicLimitInformation.LimitFlags = 0x00002000  # KILL_ON_JOB_CLOSE
        if not set_info(handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            error = ctypes.get_last_error()
            close_handle(handle)
            raise ctypes.WinError(error)
        self._handle = int(handle)
        self._win32 = (assign, terminate, close_handle)  # type: ignore[attr-defined]
        return self

    def assign(self, process: object) -> None:
        if os.name != "nt" or self._handle is None:
            return
        assign, _terminate, _close = self._win32  # type: ignore[attr-defined]
        raw_handle = getattr(process, "_handle", None)
        if raw_handle is None:
            raise OSError("Popen handle is unavailable for Job Object assignment")
        if not assign(self._handle, int(raw_handle)):
            raise ctypes.WinError(ctypes.get_last_error())

    def terminate(self) -> None:
        if os.name != "nt" or self._handle is None or self._closed:
            return
        _assign, terminate, _close = self._win32  # type: ignore[attr-defined]
        if not terminate(self._handle, 1):
            error = ctypes.get_last_error()
            if error not in {6, 87}:  # ERROR_INVALID_HANDLE / ERROR_ACCESS_DENIED
                raise ctypes.WinError(error)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if os.name == "nt" and self._handle is not None:
            _assign, _terminate, close = self._win32  # type: ignore[attr-defined]
            if not close(self._handle):
                raise ctypes.WinError(ctypes.get_last_error())
            self._handle = None

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


def _is_reparse_point(path: Path) -> bool:
    """Return whether *path* is a symlink, junction, or other reparse point."""

    try:
        if path.is_symlink():
            return True
        metadata = path.lstat()
    except (FileNotFoundError, OSError):
        return False
    return bool(getattr(metadata, "st_file_attributes", 0) & _REPARSE_POINT)


def _validate_path_component(component: str) -> None:
    if not component or component in {".", ".."}:
        raise UnsafeSandboxPath(
            "empty, current-directory, and parent tokens are unsafe"
        )
    if not _SAFE_COMPONENT.fullmatch(component):
        raise UnsafeSandboxPath(
            "path components may only contain letters, digits, dot, underscore, "
            "and dash"
        )
    if component.endswith((" ", ".")):
        raise UnsafeSandboxPath("path components may not end with a dot or space")
    if component.split(".", maxsplit=1)[0].upper() in _WINDOWS_RESERVED_NAMES:
        raise UnsafeSandboxPath("reserved Windows path component is unsafe")


def validate_relative_path(value: str | os.PathLike[str]) -> PurePosixPath:
    """Validate a portable relative path before it reaches the filesystem.

    Backslashes, drive/UNC syntax, ADS colons, parent traversal, absolute paths,
    and ambiguous Windows names are rejected independently of the host OS.
    """

    raw = os.fspath(value)
    if not isinstance(raw, str) or not raw or "\x00" in raw:
        raise UnsafeSandboxPath("a non-empty relative path is required")
    if "\\" in raw:
        raise UnsafeSandboxPath("backslashes and UNC/device paths are unsafe")
    if ":" in raw:
        raise UnsafeSandboxPath(
            "drive letters and NTFS alternate data streams are unsafe"
        )
    if raw.startswith("/"):
        raise UnsafeSandboxPath("absolute paths are unsafe")

    candidate = PurePosixPath(raw)
    if candidate.is_absolute() or not candidate.parts:
        raise UnsafeSandboxPath("absolute paths are unsafe")
    for component in candidate.parts:
        _validate_path_component(component)
    if candidate.as_posix() != raw or raw in {".", ".."}:
        raise UnsafeSandboxPath("path must already be normalized")
    return candidate


def _validate_identity_component(value: str, field: str) -> str:
    if not _SAFE_COMPONENT.fullmatch(value) or value in {".", ".."}:
        raise UnsafeSandboxPath(f"{field} is not a safe path component")
    if value.endswith((" ", ".")) or ":" in value:
        raise UnsafeSandboxPath(f"{field} is not a safe path component")
    if value.split(".", maxsplit=1)[0].upper() in _WINDOWS_RESERVED_NAMES:
        raise UnsafeSandboxPath(f"{field} is a reserved Windows name")
    return value


def _is_within(candidate: Path, root: Path) -> bool:
    try:
        return os.path.commonpath((str(candidate), str(root))) == str(root)
    except ValueError:
        return False


class _BoundedBinaryWriter:
    """Proxy that rejects bytes before they can exceed a stream budget."""

    def __init__(
        self,
        stream: BinaryIO,
        sandbox: "TaskSandbox",
        file_limit: int,
    ) -> None:
        self._stream = stream
        self._sandbox = sandbox
        self._file_limit = file_limit
        self._written = 0

    def write(self, data: bytes) -> int:
        payload = memoryview(data)
        total = 0
        while payload:
            remaining = self._file_limit - self._written
            if remaining <= 0:
                raise SandboxLimitExceeded("sandbox file byte limit exceeded")
            chunk = payload[:remaining]
            self._sandbox.ensure_capacity(0)
            written = self._stream.write(chunk)
            if written is None or written <= 0:
                raise OSError("sandbox stream did not accept bytes")
            self._written += int(written)
            total += int(written)
            payload = payload[int(written) :]
        return total

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)

    def __enter__(self) -> "_BoundedBinaryWriter":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.flush()

    def __iter__(self):  # pragma: no cover - binary streams are not iterable
        return iter(())


class TaskSandbox:
    """An attempt-scoped temporary directory with strict cleanup semantics."""

    def __init__(
        self,
        root: str | os.PathLike[str],
        simulation_id: str | UUID,
        attempt: int,
        *,
        max_bytes: int = DEFAULT_WORKSPACE_MAX_BYTES,
        input_max_bytes: int = DEFAULT_INPUT_MAX_BYTES,
    ) -> None:
        if max_bytes <= 0 or input_max_bytes <= 0 or input_max_bytes > max_bytes:
            raise ValueError("sandbox byte limits must be positive and ordered")

        configured_root = Path(root).expanduser()
        if _is_reparse_point(configured_root):
            raise UnsafeSandboxPath("sandbox root may not be a symlink or junction")
        self._root = configured_root.resolve(strict=False)
        if _is_reparse_point(self._root):
            raise UnsafeSandboxPath("sandbox root may not be a symlink or junction")
        self._root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if _is_reparse_point(self._root):
            raise UnsafeSandboxPath("sandbox root may not be a symlink or junction")

        self._simulation_id = _validate_identity_component(
            str(simulation_id), "simulation_id"
        )
        if (
            not isinstance(attempt, int)
            or isinstance(attempt, bool)
            or not 1 <= attempt <= 3
        ):
            raise ValueError("attempt must be an integer between 1 and 3")

        self.attempt = attempt
        self.max_bytes = max_bytes
        self.input_max_bytes = input_max_bytes
        self._path = self._root / self._simulation_id / f"attempt-{attempt}"
        if self._path.exists() or self._path.is_symlink():
            raise SandboxError("attempt workspace already exists")
        self._path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._assert_no_reparse_points(self._path.parent)
        self._path.mkdir(mode=0o700, parents=False, exist_ok=False)
        self._cleanup_pending = False
        self.cleanup_error: BaseException | None = None

    @property
    def root(self) -> Path:
        return self._root

    @property
    def path(self) -> Path:
        return self._path

    @property
    def cleanup_pending(self) -> bool:
        return self._cleanup_pending

    def _assert_no_reparse_points(self, candidate: Path) -> None:
        current = self._root
        relative = candidate.relative_to(self._root)
        for component in relative.parts:
            current = current / component
            if _is_reparse_point(current):
                raise UnsafeSandboxPath(
                    "symlink and junction path components are unsafe"
                )

    def resolve(
        self,
        relative_path: str | os.PathLike[str],
        *,
        create_parents: bool = False,
    ) -> Path:
        """Resolve a validated relative path while enforcing root containment."""

        relative = validate_relative_path(relative_path)
        candidate = self._path.joinpath(*relative.parts)
        self._assert_no_reparse_points(candidate)
        resolved = candidate.resolve(strict=False)
        if not _is_within(resolved, self._path) or resolved == self._path:
            raise UnsafeSandboxPath("resolved path escapes the attempt workspace")
        if _is_reparse_point(resolved):
            raise UnsafeSandboxPath("symlink and junction targets are unsafe")
        if create_parents:
            resolved.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            self._assert_no_reparse_points(resolved.parent)
        return resolved

    def usage_bytes(self) -> int:
        """Return the current regular-file byte usage without following links."""

        total = 0
        if not self._path.exists():
            return 0
        self._assert_no_reparse_points(self._path)
        for directory, directory_names, file_names in os.walk(
            self._path, topdown=True, followlinks=False
        ):
            directory_path = Path(directory)
            for name in directory_names:
                if _is_reparse_point(directory_path / name):
                    raise UnsafeSandboxPath(
                        "workspace contains a reparse-point directory"
                    )
            for name in file_names:
                file_path = directory_path / name
                if _is_reparse_point(file_path):
                    raise UnsafeSandboxPath("workspace contains a reparse-point file")
                metadata = file_path.stat(follow_symlinks=False)
                if stat.S_ISREG(metadata.st_mode):
                    total += metadata.st_size
        return total

    def ensure_capacity(self, additional_bytes: int) -> None:
        if additional_bytes < 0:
            raise ValueError("additional_bytes may not be negative")
        if self.usage_bytes() + additional_bytes > self.max_bytes:
            raise SandboxLimitExceeded("task workspace byte limit exceeded")

    def write_bytes(
        self,
        relative_path: str | os.PathLike[str],
        payload: bytes,
        *,
        overwrite: bool = False,
    ) -> Path:
        """Write one file while enforcing the aggregate workspace budget."""

        if not isinstance(payload, bytes):
            raise TypeError("payload must be bytes")
        if len(payload) > self.input_max_bytes:
            raise SandboxLimitExceeded("task input byte limit exceeded")

        target = self.resolve(relative_path, create_parents=True)
        existing_size = 0
        if overwrite and target.exists():
            existing_size = target.stat(follow_symlinks=False).st_size
        self.ensure_capacity(max(0, len(payload) - existing_size))

        mode = "wb" if overwrite else "xb"
        created = False
        try:
            with target.open(mode) as stream:
                created = True
                stream.write(payload)
            self.ensure_capacity(0)
        except Exception:
            if created:
                target.unlink(missing_ok=True)
            raise
        return target

    @contextmanager
    def open_write(
        self,
        relative_path: str | os.PathLike[str],
        *,
        max_bytes: int | None = None,
    ) -> Iterator[Any]:
        """Stream a file and remove it if the per-file/aggregate limit is exceeded."""

        file_limit = self.input_max_bytes if max_bytes is None else max_bytes
        if file_limit <= 0 or file_limit > self.max_bytes:
            raise ValueError("file limit must be positive and fit the workspace budget")
        self.ensure_capacity(0)
        target = self.resolve(relative_path, create_parents=True)
        created = False
        try:
            with target.open("xb") as stream:
                created = True
                yield _BoundedBinaryWriter(stream, self, file_limit)
                stream.flush()
                size = os.fstat(stream.fileno()).st_size
                if size > file_limit:
                    raise SandboxLimitExceeded("sandbox file byte limit exceeded")
            self.ensure_capacity(0)
        except Exception:
            if created:
                target.unlink(missing_ok=True)
            raise

    def cleanup(self) -> None:
        """Remove this attempt directory; repeated cleanup is a no-op."""

        if not self._path.exists() and not self._path.is_symlink():
            self._cleanup_pending = False
            self.cleanup_error = None
            return
        try:
            self._assert_no_reparse_points(self._path)
            if self._path.is_symlink():
                raise UnsafeSandboxPath("refusing to clean a symlinked attempt path")
            shutil.rmtree(self._path)
        except Exception as exc:
            self._cleanup_pending = True
            self.cleanup_error = exc
            logger.error(
                "sandbox cleanup pending simulation_id=%s attempt=%s error=%s",
                self._simulation_id,
                self.attempt,
                type(exc).__name__,
            )
            raise SandboxCleanupError("attempt workspace cleanup is pending") from exc
        self._cleanup_pending = False
        self.cleanup_error = None

    def close(self) -> None:
        self.cleanup()

    def __enter__(self) -> "TaskSandbox":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        try:
            self.cleanup()
        except SandboxCleanupError:
            if exc is None:
                raise
            logger.error(
                "preserving active exception while sandbox cleanup remains pending "
                "simulation_id=%s attempt=%s",
                self._simulation_id,
                self.attempt,
            )
        return None

    @classmethod
    def cleanup_attempt(
        cls,
        root: str | os.PathLike[str],
        simulation_id: str | UUID,
        attempt: int,
    ) -> bool:
        """Remove a deterministic attempt path during crash recovery."""

        if (
            not isinstance(attempt, int)
            or isinstance(attempt, bool)
            or not 1 <= attempt <= 3
        ):
            raise ValueError("attempt must be an integer between 1 and 3")
        configured_root = Path(root).expanduser()
        if _is_reparse_point(configured_root):
            raise UnsafeSandboxPath("sandbox root may not be a symlink or junction")
        resolved_root = configured_root.resolve(strict=False)
        identity = _validate_identity_component(str(simulation_id), "simulation_id")
        attempt_path = resolved_root / identity / f"attempt-{attempt}"
        if not attempt_path.exists() and not attempt_path.is_symlink():
            return False
        current = resolved_root
        parent_parts = attempt_path.relative_to(resolved_root).parts[:-1]
        for component in parent_parts:
            current = current / component
            if _is_reparse_point(current):
                raise UnsafeSandboxPath("refusing to clean a reparse-point path")
        if attempt_path.is_symlink():
            attempt_path.unlink(missing_ok=True)
            return True
        if not _is_within(attempt_path.resolve(strict=False), resolved_root):
            raise UnsafeSandboxPath("cleanup path escapes the sandbox root")
        shutil.rmtree(attempt_path)
        return True

    @classmethod
    def cleanup_stale(
        cls,
        root: str | os.PathLike[str],
        *,
        older_than_seconds: int = 24 * 60 * 60,
        now: datetime | None = None,
    ) -> tuple[Path, ...]:
        """Remove stale ``<simulation>/attempt-N`` directories without traversal."""

        if older_than_seconds <= 0:
            raise ValueError("older_than_seconds must be positive")
        configured_root = Path(root).expanduser()
        if _is_reparse_point(configured_root):
            raise UnsafeSandboxPath("sandbox root may not be a symlink or junction")
        resolved_root = configured_root.resolve(strict=False)
        if not resolved_root.exists():
            return ()
        if _is_reparse_point(resolved_root):
            raise UnsafeSandboxPath("sandbox root may not be a symlink or junction")

        current_time = now or datetime.now(timezone.utc)
        if current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=timezone.utc)
        cutoff = current_time.timestamp() - older_than_seconds
        removed: list[Path] = []
        for simulation_path in resolved_root.iterdir():
            if _is_reparse_point(simulation_path) or not simulation_path.is_dir():
                continue
            try:
                _validate_identity_component(simulation_path.name, "simulation_id")
            except UnsafeSandboxPath:
                continue
            for attempt_path in simulation_path.iterdir():
                if _is_reparse_point(attempt_path) or not attempt_path.is_dir():
                    continue
                if not re.fullmatch(r"attempt-[1-3]", attempt_path.name):
                    continue
                if attempt_path.stat(follow_symlinks=False).st_mtime > cutoff:
                    continue
                if not _is_within(attempt_path.resolve(strict=False), resolved_root):
                    raise UnsafeSandboxPath("stale cleanup path escapes the root")
                shutil.rmtree(attempt_path)
                removed.append(attempt_path)
            try:
                simulation_path.rmdir()
            except OSError:
                pass
        return tuple(removed)


__all__ = [
    "DEFAULT_FRAME_MAX_BYTES",
    "DEFAULT_INPUT_MAX_BYTES",
    "DEFAULT_OUTPUT_MAX_BYTES",
    "DEFAULT_WORKSPACE_MAX_BYTES",
    "MIB",
    "ProcessTreeJob",
    "SandboxCleanupError",
    "SandboxError",
    "SandboxLimitExceeded",
    "TaskSandbox",
    "UnsafeSandboxPath",
    "validate_relative_path",
]
