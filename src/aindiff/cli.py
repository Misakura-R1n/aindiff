"""Command line entry points for aindiff."""

from __future__ import annotations

import argparse
import csv
import os
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Optional

from . import APP_NAME, __version__
from .alice_tools import (
    AliceError,
    AliceNotFound,
    AliceTools,
    download_alice,
)
from .model import AlignedRow, count_changed
from .loading import load_pair as _load_pair, open_dump_with_fallback as _open_dump_with_fallback  # noqa: F401

__all__ = ["build_parser", "main"]


def _add_alice_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--alice",
        metavar="PATH",
        help="alice-tools 可执行文件路径（默认自动查找；可用环境变量 AINDIF_ALICE）",
    )


def _add_encoding_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--left-encoding", help="左侧 AIN 文本编码（默认自动识别）")
    parser.add_argument("--right-encoding", help="右侧 AIN 文本编码（默认自动识别）")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aindiff",
        description=APP_NAME + "：并排打开两个 AliceSoft .ain，对照/编辑全部文本节并写回原文件。",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    sub = parser.add_subparsers(dest="command")

    gui = sub.add_parser(
        "gui",
        help="启动图形界面（直接打开两个 AIN）",
        description="启动双栏对照编辑窗口。",
    )
    _add_alice_option(gui)
    _add_encoding_options(gui)
    gui.add_argument("left", nargs="?", help="左侧 .ain 文件")
    gui.add_argument("right", nargs="?", help="右侧 .ain 文件")

    export = sub.add_parser(
        "export",
        help="把两个 AIN 的全部文本节导出为 TSV 对照表",
        description="导出双栏对照 TSV，便于在电子表格中查看。",
    )
    _add_alice_option(export)
    _add_encoding_options(export)
    export.add_argument("left", help="左侧 .ain 文件")
    export.add_argument("right", help="右侧 .ain 文件")
    export.add_argument("-o", "--output", required=True, help="输出 TSV 路径")

    check = sub.add_parser(
        "check",
        help="只统计差异，不打开窗口",
        description="输出两个 AIN 的文本差异统计。",
    )
    _add_alice_option(check)
    _add_encoding_options(check)
    check.add_argument("left", help="左侧 .ain 文件")
    check.add_argument("right", help="右侧 .ain 文件")
    check.add_argument("--max-diffs", type=int, default=20, help="最多显示多少条差异（默认 20）")

    fetch = sub.add_parser(
        "fetch-alice",
        help="下载官方 alice-tools 中的 alice 可执行文件",
        description="下载并解压 nunuhara/alice-tools 的官方发布包。",
    )
    fetch.add_argument("--dest", help="下载/解压目录（默认用户缓存目录）")
    fetch.add_argument("--insecure", action="store_true", help="下载时禁用 TLS 证书校验")

    return parser


def _run_gui(args: argparse.Namespace) -> int:
    from .gui import run_gui

    return run_gui(
        left=args.left,
        right=args.right,
        left_encoding=args.left_encoding,
        right_encoding=args.right_encoding,
        alice=args.alice,
    )


def _run_export(args: argparse.Namespace) -> int:
    output = Path(args.output).expanduser().resolve()
    for source in (args.left, args.right):
        source_path = Path(source).expanduser().resolve()
        if output == source_path or (output.exists() and output.samefile(source_path)):
            raise AliceError("输出 TSV 路径不能与输入 AIN 文件相同")
    tools = AliceTools(args.alice) if args.alice else AliceTools()
    left_dump, right_dump, rows, notes = _load_pair(
        tools, args.left, args.right, args.left_encoding, args.right_encoding
    )
    for note in notes:
        print(f"注意：混合编码文件已回退到原始区段 {note}。")

    output.parent.mkdir(parents=True, exist_ok=True)
    # Write to a sibling temporary file; an interrupted export preserves any
    # existing result instead of leaving it truncated.
    with tempfile.TemporaryDirectory(prefix=".aindiff-export-", dir=output.parent) as tmp:
        staged = Path(tmp) / "output.tsv"
        _write_tsv(staged, rows)
        os.replace(staged, output)
    print(f"已导出 {len(rows)} 行（含节标题）到 {output}")
    print(f"差异条目：{count_changed(rows)}")
    return 0


def _write_tsv(output: Path, rows: list[AlignedRow]) -> None:
    with output.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh, delimiter="\t", lineterminator="\n")
        writer.writerow(
            ["no", "section_left", "section_right", "type", "id", "occurrence",
             "left", "right", "state"]
        )
        for row in rows:
            if row.is_section:
                writer.writerow(
                    ["", row.left_section or "", row.right_section or "",
                     "SECTION", "", "", "", "", ""]
                )
                continue
            if row.left and row.right:
                state = "changed" if row.changed else "same"
            elif row.left:
                state = "left_only"
            else:
                state = "right_only"
            left_entry = row.left
            right_entry = row.right
            writer.writerow(
                [
                    row.no,
                    (left_entry.section if left_entry else "") or "",
                    (right_entry.section if right_entry else "") or "",
                    (left_entry or right_entry).kind,
                    (left_entry or right_entry).index,
                    (left_entry or right_entry).occurrence,
                    left_entry.text if left_entry else "",
                    right_entry.text if right_entry else "",
                    state,
                ]
            )


def _run_check(args: argparse.Namespace) -> int:
    tools = AliceTools(args.alice) if args.alice else AliceTools()
    left_dump, right_dump, rows, notes = _load_pair(
        tools, args.left, args.right, args.left_encoding, args.right_encoding
    )
    for note in notes:
        print(f"注意：混合编码文件已回退到原始区段 {note}。")
    changed = [row for row in rows if not row.is_section and row.changed]
    print(f"左侧：{args.left}（{left_dump.encoding}，{len(left_dump.entries)} 条文本）")
    print(f"右侧：{args.right}（{right_dump.encoding}，{len(right_dump.entries)} 条文本）")
    print(f"对齐行数：{sum(1 for r in rows if not r.is_section)}，差异：{len(changed)}")
    shown = changed[: args.max_diffs]
    for row in shown:
        left_text = row.left.text if row.left else "<缺失>"
        right_text = row.right.text if row.right else "<缺失>"
        kind = (row.left or row.right).kind
        index = (row.left or row.right).index
        print(f"[{kind} {index}]")
        print(f"  L: {left_text!r}")
        print(f"  R: {right_text!r}")
    if len(changed) > len(shown):
        print(f"... 其余 {len(changed) - len(shown)} 条差异省略")
    return 0


def _run_fetch(args: argparse.Namespace) -> int:
    try:
        exe = download_alice(args.dest, insecure=args.insecure)
    except AliceError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"alice 可执行文件已就绪：{exe}")
    print("可通过环境变量 AINDIF_ALICE 指向它，或将它复制到项目 vendor/alice/ 目录。")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()

    # Accept `aindiff left.ain right.ain` as shorthand for `aindiff gui ...`.
    raw = list(argv) if argv is not None else sys.argv[1:]
    commands = {"gui", "export", "check", "fetch-alice"}
    if raw and raw[0] in commands:
        args = parser.parse_args(raw)
    elif raw and raw[0] in {"-h", "--help", "--version"}:
        args = parser.parse_args(raw)
    else:
        # Rewrite to a synthetic `gui` invocation but keep option parsing sane.
        args = parser.parse_args(["gui", *raw])

    try:
        if args.command == "gui":
            return _run_gui(args)
        if args.command == "export":
            return _run_export(args)
        if args.command == "check":
            return _run_check(args)
        if args.command == "fetch-alice":
            return _run_fetch(args)
    except AliceNotFound as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except (AliceError, OSError, ValueError, LookupError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
