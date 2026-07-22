"""Copy only explicitly approved public files into a fresh Pages directory."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from privacy_audit import PUBLIC_ALLOWLIST


def prepare(root: Path, destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    names = {Path("data/report.html"): "index.html"}
    for relative in PUBLIC_ALLOWLIST:
        source = root / relative
        if not source.is_file():
            raise RuntimeError(f"Missing allowlisted public artifact: {relative}")
        shutil.copyfile(source, destination / names[relative])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--destination", type=Path, default=Path("site"))
    args = parser.parse_args()
    prepare(args.root.resolve(), args.destination.resolve())


if __name__ == "__main__":
    main()
