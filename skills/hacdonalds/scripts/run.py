#!/usr/bin/env python3
"""Bridge a project-local or copied Skill to the source CLI."""

import os
from pathlib import Path
import sys

root = Path(os.environ.get("HACDONALDS_ROOT", Path(__file__).resolve().parents[3]))
if not (root / "src/hacdonalds/cli.py").is_file():
    raise SystemExit("找不到 Hacdonald's 项目，请设置 HACDONALDS_ROOT 为项目根目录。")
sys.path.insert(0, str(root / "src"))
from hacdonalds.cli import main

if __name__ == "__main__":
    raise SystemExit(main(root=root))
