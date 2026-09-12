"""Locate, download and run the official ``alice`` command-line tool.

This project intentionally does not reimplement the AIN container format.
It calls the battle-tested `alice-tools <https://github.com/nunuhara/alice-tools>`_
CLI for three operations:

* ``alice ain dump -t``   -- extract every text section as UTF-8,
* ``alice ain edit -t``   -- apply changed ``s[n]``/``m[n]`` assignments,
* ``alice ain dump -t``   -- used by encoding auto-detection.

The runner searches for an ``alice`` executable in this order:

1. ``$AINDIF_ALICE`` (path to the executable),
2. the repository-local ``vendor/alice/alice.exe`` (Windows) or
   ``vendor/alice/alice`` (POSIX),
3. ``alice`` on ``PATH``,
4. a downloaded copy in the user cache directory.

If none is found, ``aindiff fetch-alice`` downloads and extracts the
official 0.13.0 Windows release into the user cache.
"""

from __future__ import annotations

import os
import shutil
import ssl
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from collections.abc import Sequence
from pathlib import Path
from typing import List, Optional, Tuple

from . import ALICE_RELEASE_TAG, ALICE_RELEASE_URL
from . import xsys35

__all__ = [
    "AliceError",
    "AliceNotFound",
    "AliceRunError",
    "AliceTools",
    "download_alice",
    "find_alice",
]

_EXE_NAME = "alice.exe" if os.name == "nt" else "alice"


class AliceError(RuntimeError):
    """Base class for alice-tools integration errors."""


class AliceNotFound(AliceError):
    """The ``alice`` executable could not be located."""


class AliceRunError(AliceError):
    """The ``alice`` executable returned a non-zero exit code."""


def _repo_root() -> Path:
    # src/aindiff/alice_tools.py -> repository root (when running from source)
    return Path(__file__).resolve().parents[2]


def _cache_dir() -> Path:
    override = os.environ.get("AINDIF_ALICE_DIR")
    if override:
        return Path(override).expanduser()
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / "aindiff" / "alice-tools"
        return Path.home() / "AppData" / "Local" / "aindiff" / "alice-tools"
    base = os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))
    return Path(base) / "aindiff" / "alice-tools"


def _candidate_paths() -> List[Tuple[str, Path]]:
    candidates: List[Tuple[str, Path]] = []
    env = os.environ.get("AINDIF_ALICE")
    if env:
        candidates.append(("AINDIF_ALICE", Path(env).expanduser()))

    if getattr(sys, "frozen", False):
        bundle_base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        for suffix in ("alice.exe", "alice"):
            candidates.append((f"bundle/vendor/alice/{suffix}", bundle_base / "vendor" / "alice" / suffix))

    exe_dir = Path(sys.executable).resolve().parent
    for suffix in ("alice.exe", "alice"):
        candidates.append((f"exe-dir/vendor/alice/{suffix}", exe_dir / "vendor" / "alice" / suffix))

    for suffix in ("alice.exe", "alice"):
        bundled = _repo_root() / "vendor" / "alice" / suffix
        candidates.append((f"vendor/alice/{suffix}", bundled))

    which = shutil.which("alice")
    if which:
        candidates.append(("PATH", Path(which)))

    release_dir = _cache_dir() / f"alice-tools-{ALICE_RELEASE_TAG}"
    candidates.append(("cache", release_dir / _EXE_NAME))
    return candidates


def find_alice() -> Path:
    """Return the path of a usable ``alice`` executable or raise."""

    tried: List[str] = []
    for source, path in _candidate_paths():
        if path.is_file():
            return path
        tried.append(f"{source}: {path}")
    raise AliceNotFound(
        "找不到 alice-tools 的 alice 可执行文件。\n"
        "请运行 `python -m aindiff fetch-alice` 下载官方版本，"
        "或设置环境变量 AINDIF_ALICE 指向 alice(.exe)。\n"
        "查找过的位置：\n  " + "\n  ".join(tried)
    )


class AliceTools:
    """Small wrapper around the ``alice`` executable."""

    def __init__(self, executable: Optional[str | Path] = None) -> None:
        self.executable = Path(executable).expanduser() if executable else find_alice()

    # ------------------------------------------------------------------
    # low-level
    # ------------------------------------------------------------------
    def run(
        self,
        args: Sequence[str | os.PathLike[str]],
        *,
        check: bool = True,
        timeout: Optional[float] = None,
    ) -> subprocess.CompletedProcess[str]:
        cmd = [str(self.executable), *(str(a) for a in args)]
        try:
            completed = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise AliceError(
                "无法执行 alice 命令：" + " ".join(cmd) + chr(10) + str(exc)
            ) from exc
        if check and completed.returncode != 0:
            tail = (completed.stderr or completed.stdout or "").strip().splitlines()[-12:]
            raise AliceRunError(
                f"alice 命令失败（退出码 {completed.returncode}）：\n"
                f"  {' '.join(cmd)}\n"
                + "".join(f"  {line}\n" for line in tail)
            )
        return completed

    # ------------------------------------------------------------------
    # .ain operations
    # ------------------------------------------------------------------
    def map_dump(
        self,
        ain_path: str | os.PathLike[str],
        input_encoding: str = "CP932",
    ) -> str:
        """Return the ASCII section map from ``alice ain dump --map``."""
        if xsys35.is_xsys35_file(ain_path):
            return xsys35.map_text(str(ain_path))
        completed = self.run(
            [
                "ain", "dump", "--map",
                "--input-encoding", input_encoding,
                str(ain_path),
            ],
            timeout=120,
        )
        return completed.stdout

    def decrypted_bytes(self, ain_path: str | os.PathLike[str]) -> bytes:
        """Return the decrypted AIN image used by ``ain dump --map`` offsets."""
        with tempfile.TemporaryDirectory(prefix="aindiff-decrypt-") as tmp:
            out_file = Path(tmp) / "decrypted.ain"
            self.run(["ain", "dump", "-d", "-o", str(out_file), str(ain_path)], timeout=300)
            return out_file.read_bytes()

    def dump_text(
        self,
        ain_path: str | os.PathLike[str],
        input_encoding: str,
        *,
        output_encoding: str = "UTF-8",
    ) -> str:
        """Return the UTF-8 text dump of *ain_path*."""

        if xsys35.is_xsys35_file(ain_path):
            return xsys35.dump_text_syntax(str(ain_path), input_encoding)

        with tempfile.TemporaryDirectory(prefix="aindiff-dump-") as tmp:
            out_file = Path(tmp) / "dump.txt"
            self.run(
                [
                    "ain", "dump", "-t",
                    "--input-encoding", input_encoding,
                    "--output-encoding", output_encoding,
                    "-o", str(out_file),
                    str(ain_path),
                ],
                timeout=300,
            )
            read_encoding = (
                "utf-8-sig"
                if output_encoding.upper().replace("_", "-") == "UTF-8"
                else output_encoding
            )
            return out_file.read_text(encoding=read_encoding)

    def detect_encoding(
        self,
        ain_path: str | os.PathLike[str],
        candidates: Optional[Sequence[str]] = None,
    ) -> str:
        """Return the first encoding for which ``ain dump -t`` succeeds.

        The default order tries CP932 first because the overwhelming majority
        of AliceSoft originals use it.  A Chinese translation build (CP936)
        usually contains byte sequences that are invalid CP932, so the CP932
        attempt fails cleanly and CP936 is chosen next.
        """

        if xsys35.is_xsys35_file(ain_path):
            return xsys35.detect_encoding(str(ain_path))
        candidates = list(candidates or ("CP932", "CP936", "UTF-8", "GBK", "BIG5"))
        last_error: Optional[BaseException] = None
        for encoding in candidates:
            try:
                self.dump_text(ain_path, encoding)
                return encoding
            except AliceRunError as exc:
                last_error = exc
        raise AliceError(
            f"无法自动识别 {ain_path} 的文本编码，"
            f"尝试过：{', '.join(candidates)}。请手动指定编码。"
        ) from last_error

    def edit_text(
        self,
        ain_path: str | os.PathLike[str],
        edit_text_data: str,
        output_encoding: str,
        *,
        backup: bool = True,
        backup_suffix: str = ".bak",
        timeout: Optional[float] = 600,
    ) -> Tuple[Path, int, int]:
        """Apply *edit_text_data* and atomically replace *ain_path*.

        Returns ``(written_path, old_size, new_size)``.  When *backup* is
        true the previous file is copied to ``<ain_path>.bak`` before the
        replacement.
        """

        ain_path = Path(ain_path).resolve()
        if not ain_path.is_file():
            raise AliceError(f"输入文件不存在：{ain_path}")
        old_size = ain_path.stat().st_size

        if xsys35.is_xsys35_file(ain_path):
            try:
                return xsys35.edit_file(
                    ain_path,
                    edit_text_data,
                    output_encoding,
                    backup=backup,
                    backup_suffix=backup_suffix,
                )
            except (ValueError, LookupError) as exc:
                raise AliceError(str(exc)) from exc

        # Keep each save isolated and on the destination filesystem so that
        # os.replace remains atomic without deleting an existing .ain-new file.
        with tempfile.TemporaryDirectory(prefix=".aindiff-edit-", dir=ain_path.parent) as tmp:
            tmp_path = Path(tmp)
            edit_file = tmp_path / "changes.txt"
            edit_file.write_text(edit_text_data, encoding="utf-8")

            out_file = tmp_path / "output.ain"

            try:
                self.run(
                    [
                        "ain", "edit",
                        "-t", str(edit_file),
                        "--input-encoding", "UTF-8",
                        "--output-encoding", output_encoding,
                        "-o", str(out_file),
                        str(ain_path),
                    ],
                    timeout=timeout,
                )

                if not out_file.is_file():
                    raise AliceError(f"alice 未生成输出文件：{out_file}")
                if out_file.stat().st_size == 0:
                    raise AliceError("alice 生成了空文件，已取消写回")

                if backup:
                    backup_path = ain_path.with_name(ain_path.name + backup_suffix)
                    shutil.copy2(ain_path, backup_path)

                os.replace(out_file, ain_path)
            finally:
                try:
                    out_file.unlink()
                except FileNotFoundError:
                    pass

        return ain_path, old_size, ain_path.stat().st_size


# ----------------------------------------------------------------------
# download helper for `aindiff fetch-alice`
# ----------------------------------------------------------------------
def download_alice(
    dest_dir: Optional[str | os.PathLike[str]] = None,
    *,
    insecure: bool = False,
    url: str = ALICE_RELEASE_URL,
) -> Path:
    """Download the official alice-tools release and extract ``alice``."""

    dest_dir = Path(dest_dir) if dest_dir else _cache_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)
    zip_path = dest_dir / f"alice-tools-{ALICE_RELEASE_TAG}.zip"
    extract_dir = dest_dir / f"alice-tools-{ALICE_RELEASE_TAG}"

    _download(url, zip_path, insecure=insecure)

    extract_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.namelist():
            base = member.split("/", 1)[1] if "/" in member else member
            if not base:
                continue
            if base in ("alice.exe", "alice", "COPYING.txt"):
                target = extract_dir / base
                with zf.open(member) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)

    exe = extract_dir / _EXE_NAME
    if not exe.is_file():
        raise AliceError(f"下载包中未找到 {_EXE_NAME}：{url}")
    if os.name != "nt":
        exe.chmod(0o755)
    return exe


def _download(url: str, dest: Path, *, insecure: bool) -> None:
    try:
        _download_urllib(url, dest, insecure=insecure)
    except (urllib.error.URLError, OSError) as exc:
        raise AliceError(
            f"下载 alice-tools 失败：{exc}\n"
            "可手动从 https://github.com/nunuhara/alice-tools/releases "
            f"下载 {ALICE_RELEASE_URL}，解压后将 alice(.exe) 放入 vendor/alice/，"
            "或设置 AINDIF_ALICE 环境变量。"
        ) from exc


def _download_urllib(url: str, dest: Path, *, insecure: bool) -> None:
    context = None
    if insecure:
        context = ssl._create_unverified_context()
    request = urllib.request.Request(url, headers={"User-Agent": "aindiff/0.1"})
    with urllib.request.urlopen(request, context=context, timeout=120) as response:
        tmp = dest.with_suffix(dest.suffix + ".part")
        with open(tmp, "wb") as out:
            shutil.copyfileobj(response, out)
        os.replace(tmp, dest)
