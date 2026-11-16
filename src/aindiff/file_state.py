"""Version checks and transactional write-back for AIN files."""
from __future__ import annotations

import hashlib
import os
import shutil
import stat
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path


def _stamp(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, stat.S_IMODE(info.st_mode)


@dataclass(frozen=True)
class FileRevision:
    path: Path
    stamp: tuple[int, int, int, int, int]
    digest: bytes

    @property
    def size(self) -> int:
        return self.stamp[2]

    @classmethod
    def capture(cls, path: str | os.PathLike[str]) -> FileRevision:
        target = Path(path).expanduser().resolve(strict=True)
        before = target.stat()
        if not stat.S_ISREG(before.st_mode):
            raise OSError(f"不是普通文件：{target}")
        digest = hashlib.sha256()
        with target.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if _stamp(opened) != _stamp(before):
                raise _changed(target)
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
            after = os.fstat(stream.fileno())
        named = target.stat()
        if (
            _stamp(after) != _stamp(opened)
            or after.st_ctime_ns != opened.st_ctime_ns
            or _stamp(named) != _stamp(after)
            or named.st_ctime_ns != before.st_ctime_ns
        ):
            raise _changed(target)
        return cls(target, _stamp(after), digest.digest())

    def same_file(self, other: FileRevision) -> bool:
        return self.path == other.path or (
            self.stamp[1] != 0 and self.stamp[:2] == other.stamp[:2]
        )

    def check(self) -> None:
        try:
            current = self.capture(self.path)
        except (OSError, RuntimeError) as exc:
            raise _changed(self.path) from exc
        if current != self:
            raise _changed(self.path)

    def snapshot(self, directory: Path) -> Path:
        copied = directory / "input.ain"
        shutil.copy2(self.path, copied)
        if self.capture(copied).digest != self.digest:
            raise _changed(self.path)
        return copied


def _changed(path: Path) -> OSError:
    return OSError(f"文件已被替换或修改，请重新加载后再编辑：{path}")


@contextmanager
def _commit_lock(directory: Path):
    """Serialize cooperating editors across the short commit phase."""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
        kernel.CreateMutexW.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.ReleaseMutex.argtypes = (wintypes.HANDLE,)
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        key = hashlib.sha256(os.fsencode(os.path.normcase(directory))).hexdigest()
        mutex = kernel.CreateMutexW(None, False, f"Local\\AinDiff-{key}")
        if not mutex:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            result = kernel.WaitForSingleObject(mutex, 0)
            if result == 258:  # WAIT_TIMEOUT
                raise OSError(f"另一个 AinDiff 正在此目录写回：{directory}")
            if result not in (0, 0x80):  # WAIT_OBJECT_0 or WAIT_ABANDONED
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                yield
            finally:
                kernel.ReleaseMutex(mutex)
        finally:
            kernel.CloseHandle(mutex)
    else:
        import fcntl

        fd = os.open(directory, os.O_RDONLY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield
        finally:
            os.close(fd)


def _move_aside(path: Path) -> Path | None:
    if not os.path.lexists(path):
        return None
    if stat.S_ISDIR(path.lstat().st_mode):
        raise IsADirectoryError(f"目标路径已经是目录：{path}")
    # Move the directory entry itself so a link cannot redirect the write.
    fd, name = tempfile.mkstemp(prefix=".aindiff-previous-", suffix=".bak", dir=path.parent)
    os.close(fd)
    previous = Path(name)
    try:
        os.replace(path, previous)
    except BaseException:
        previous.unlink()
        raise
    return previous


def _remove_previous(path: Path | None) -> None:
    if path is not None:
        try:
            path.unlink()
        except OSError:
            # A locked or read-only previous file may remain after commit.
            pass


def _install_with_backup(
    source: FileRevision, snapshot: Path, output: Path, backup: Path
) -> None:
    if backup == source.path:
        raise ValueError("备份路径不能与原文件相同")
    previous = _move_aside(backup)

    published = False
    try:
        os.replace(snapshot, backup)
        published = True
        source.check()
        os.replace(output, source.path)
    except BaseException:
        try:
            if previous is not None:
                os.replace(previous, backup)
            elif published:
                backup.unlink()
        except OSError as exc:
            recovery = previous if previous is not None else backup
            raise OSError(f"写回失败，备份未能恢复到原路径，备份保留在：{recovery}（{exc}）") from exc
        raise
    else:
        _remove_previous(previous)


def commit_edit(
    source: FileRevision, snapshot: Path, output: Path, backup_suffix: str | None
) -> FileRevision:
    if os.name != "nt":
        shutil.copymode(snapshot, output)
    written = FileRevision.capture(output)
    with _commit_lock(source.path.parent):
        source.check()
        if backup_suffix is None:
            os.replace(output, source.path)
        else:
            backup = source.path.with_name(source.path.name + backup_suffix)
            _install_with_backup(source, snapshot, output, backup)
    return replace(written, path=source.path)


def publish_files(directory: Path, files: list[tuple[Path, Path]]) -> None:
    """Install staged files and roll back earlier replacements on failure."""
    root = directory.resolve(strict=True)
    if any(not target.parent.resolve().is_relative_to(root) for _, target in files):
        raise OSError(f"发布目标位于预期目录之外：{root}")
    with _commit_lock(root):
        previous: dict[Path, Path | None] = {}
        installed: set[Path] = set()
        try:
            for staged, target in files:
                previous[target] = _move_aside(target)
                os.replace(staged, target)
                installed.add(target)
        except BaseException:
            recovery = []
            for target, prior in reversed(tuple(previous.items())):
                try:
                    if prior is not None:
                        os.replace(prior, target)
                    elif target in installed:
                        target.unlink()
                except OSError:
                    recovery.append(str(prior if prior is not None else target))
            if recovery:
                raise OSError(f"提交失败，部分文件需手动恢复：{', '.join(recovery)}")
            raise
        else:
            for prior in previous.values():
                _remove_previous(prior)
