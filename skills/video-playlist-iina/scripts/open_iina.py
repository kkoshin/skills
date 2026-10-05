#!/usr/bin/env python3
"""Open a playlist in a new IINA window at a requested playback speed."""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple


def _find_cli() -> Tuple[Optional[str], Optional[Path]]:
    cli = shutil.which("iina-cli")
    if cli:
        return cli, None
    candidates = [
        Path("/Applications/IINA.app/Contents/MacOS/iina-cli"),
        Path.home() / "Applications/IINA.app/Contents/MacOS/iina-cli",
    ]
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate), None
    return None, next((p.parent.parent.parent for p in candidates if p.parent.parent.parent.is_dir()), None)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("playlist", type=Path)
    parser.add_argument("--speed", type=float, default=1.5)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    playlist = args.playlist.expanduser().resolve()
    if not playlist.is_file():
        parser.error(f"播放列表不存在：{playlist}")
    if args.speed <= 0:
        parser.error("--speed 必须大于 0")

    cli, app = _find_cli()
    command: list[str]
    if cli:
        command = [cli, "--separate-windows", "--no-stdin", f"--mpv-speed={args.speed}", str(playlist)]
    elif app:
        command = [
            "open",
            "-na",
            str(app),
            "--args",
            "--separate-windows",
            "--no-stdin",
            f"--mpv-speed={args.speed}",
            str(playlist),
        ]
    else:
        print("找不到 IINA。请安装 IINA，或把 iina-cli 放入 PATH。", file=sys.stderr)
        return 1

    if args.dry_run:
        print(" ".join(subprocess.list2cmdline([part]) for part in command))
        return 0
    try:
        subprocess.Popen(command)
    except OSError as exc:
        print(f"启动 IINA 失败：{exc}", file=sys.stderr)
        return 1
    print(f"已在 IINA 新窗口打开：{playlist}")
    print(f"本次播放速度：{args.speed:g}x")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
