#!/usr/bin/env python3
"""One-command release builder.

Produces the extract-and-run zip in ``release/`` from the current checkout:

    python scripts/build_release.py

Prerequisites:
* Python with tkinter
* PyInstaller (``python -m pip install pyinstaller``)
* ``vendor/alice/alice.exe`` (fetch with ``python -m aindiff fetch-alice``)
"""

from __future__ import annotations

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
        print("缺少 vendor/alice/alice.exe。请先运行：python -m aindiff fetch-alice", file=sys.stderr)
        return 1

    dist = ROOT / "dist" / "aindiff"
    if dist.exists():
        shutil.rmtree(dist)

    run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--windowed",
            "--name",
            "aindiff",
            "--paths",
            "src",
            "--add-data",
            "vendor/alice/alice.exe;vendor/alice",
            "scripts/launcher.py",
        ]
    )

    usage = f"AinDiff v{__version__} —— AliceSoft AIN 文本对照编辑工具\n\n"
    usage += (ROOT / "docs" / "windows-quickstart.txt").read_text(encoding="utf-8-sig")
    (dist / "使用说明.txt").write_text(usage, encoding="utf-8")
    for name in ("README.md", "CHANGELOG.md", "CONTRIBUTING.md", "LICENSE"):
        shutil.copy2(ROOT / name, dist / name)
    (dist / "docs").mkdir(exist_ok=True)
    for name in ("usage.md", "development.md"):
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

    print(f"release: {zip_path} ({zip_path.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
