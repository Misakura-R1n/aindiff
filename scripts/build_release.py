#!/usr/bin/env python3
"""One-command release builder.

Produces the extract-and-run zip in ``release/`` from the current checkout:

    python scripts/build_release.py

Prerequisites:
* Python with tkinter
* PyInstaller (``python -m pip install pyinstaller``)
* ``vendor/alice/alice.exe`` (copy from the cache printed by ``fetch-alice``)
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from aindiff import __version__  # noqa: E402


def run(cmd: list[str]) -> None:
    print("+", " ".join(cmd))
    subprocess.run(cmd, cwd=ROOT, check=True)


def main() -> int:
    alice = ROOT / "vendor" / "alice" / "alice.exe"
    if not alice.is_file():
        print(
            "缺少 vendor/alice/alice.exe。请运行 python -m aindiff fetch-alice，"
            "再将提示的缓存目录中的 alice.exe 复制到 vendor/alice/alice.exe。",
            file=sys.stderr,
        )
        return 1

    dist = ROOT / "dist" / "aindiff"

    run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "aindiff.spec",
        ]
    )

    usage = f"AinDiff v{__version__} —— AliceSoft AIN 文本对照编辑工具\n\n"
    usage += (ROOT / "docs" / "windows-quickstart.txt").read_text(encoding="utf-8-sig")
    (dist / "使用说明.txt").write_text(usage, encoding="utf-8")
    for name in ("README.md", "CHANGELOG.md", "CONTRIBUTING.md", "LICENSE"):
        shutil.copy2(ROOT / name, dist / name)
    (dist / "docs").mkdir(exist_ok=True)
    for name in ("usage.md", "development.md", "alice-tools-integration.md"):
        shutil.copy2(ROOT / "docs" / name, dist / "docs" / name)
    shutil.copy2(ROOT / "LICENSE", dist / "LICENSE.txt")
    licenses = dist / "licenses" / "alice-tools"
    licenses.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "licenses" / "alice-tools" / "COPYING.txt", licenses / "COPYING.txt")

    release_dir = ROOT / "release"
    release_dir.mkdir(exist_ok=True)
    zip_path = release_dir / f"aindiff-v{__version__}-win64.zip"
    if zip_path.exists():
        zip_path.unlink()
    base = f"aindiff-v{__version__}-win64"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for file in sorted(dist.rglob("*")):
            if file.is_file():
                zf.write(file, Path(base) / file.relative_to(dist))

    digest = hashlib.sha256()
    with zip_path.open("rb") as archive:
        for chunk in iter(lambda: archive.read(1024 * 1024), b""):
            digest.update(chunk)
    checksum_path = zip_path.with_suffix(".zip.sha256")
    checksum_path.write_text(f"{digest.hexdigest()}  {zip_path.name}\n", encoding="ascii")
    print(f"release: {zip_path} ({zip_path.stat().st_size} bytes)")
    print(f"checksum: {checksum_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
