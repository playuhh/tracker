"""Audit tracked public outputs and the exact GitHub Pages artifact allowlist."""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path
from typing import Iterable


PUBLIC_ALLOWLIST = (Path("data/report.html"),)
ANONYMOUS_FIXTURES = (Path("tests/fixtures"),)
FORBIDDEN_NAMES = ("RiverHouse", "RiverTrace", "Soho Lofts")
CREDENTIAL_PATTERNS = (
    re.compile(r'"type"\s*:\s*"service_account"', re.I),
    re.compile(r'-----BEGIN (?:RSA |EC )?PRIVATE KEY-----'),
    re.compile(r'private_key_id', re.I),
)
RAW_UNIT_PATTERN = re.compile(r"\b(?:UNIT|APT|CAP)[-_ ]?\d{3,6}\b", re.I)
PAGE_ID_PATTERN = re.compile(r'(?i)(?:page[_ -]?id|RENTAL_BUILDING_PAGE_ID)\s*[=:]\s*["\']?[0-9]{3,}')
ADDRESS_PATTERN = re.compile(
    r"\b\d{1,6}\s+[A-Za-z0-9.' -]+\s(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Drive|Dr|Way)\b",
    re.I,
)
SAFE_UNIT_ID_PATTERN = re.compile(r"^listing-[0-9a-f]{16,64}$")


def tracked_files(root: Path) -> list[Path]:
    try:
        output = subprocess.run(
            ["git", "ls-files", "-z"], cwd=root, check=True, capture_output=True
        ).stdout
    except (OSError, subprocess.CalledProcessError) as error:
        raise RuntimeError("Unable to enumerate tracked files for privacy audit") from error
    return [root / item.decode() for item in output.split(b"\0") if item]


def pages_artifact_files(root: Path) -> list[Path]:
    return [root / path for path in PUBLIC_ALLOWLIST]


def audit_paths(paths: Iterable[Path], root: Path) -> list[str]:
    problems: list[str] = []
    for path in paths:
        if not path.exists() or not path.is_file():
            problems.append(f"required public artifact is missing: {path.relative_to(root)}")
            continue
        relative = path.relative_to(root)
        if "private" in relative.parts:
            problems.append(f"private file is in public scope: {relative}")
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for name in FORBIDDEN_NAMES:
            if name.casefold() in text.casefold():
                problems.append(f"real property name found in {relative}")
        if ADDRESS_PATTERN.search(text):
            problems.append(f"street address found in {relative}")
        if PAGE_ID_PATTERN.search(text):
            problems.append(f"source page ID found in {relative}")
        if RAW_UNIT_PATTERN.search(text):
            problems.append(f"raw unit identifier found in {relative}")
        for pattern in CREDENTIAL_PATTERNS:
            if pattern.search(text):
                problems.append(f"credential material found in {relative}")
                break
    return problems


def audit_unit_snapshot_ids(root: Path) -> list[str]:
    path = root / "data/unit_snapshots.csv"
    if not path.exists():
        return []
    import csv
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    return [
        "unkeyed or malformed unit identifier found in data/unit_snapshots.csv"
        for row in rows if not SAFE_UNIT_ID_PATTERN.fullmatch(row.get("unit_id", ""))
    ][:1]


def audit_repository(root: Path = Path(".")) -> list[str]:
    root = root.resolve()
    tracked = tracked_files(root)
    problems = []
    for path in tracked:
        relative = path.relative_to(root)
        if relative.parts and relative.parts[0] == "private":
            problems.append(f"private content is tracked: {relative}")
    public_scope = pages_artifact_files(root)
    public_scope += [path for directory in ANONYMOUS_FIXTURES for path in (root / directory).rglob("*") if path.is_file()]
    problems.extend(audit_paths(public_scope, root))
    problems.extend(audit_unit_snapshot_ids(root))
    return sorted(set(problems))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args()
    problems = audit_repository(args.root)
    if problems:
        for problem in problems:
            print(f"[PRIVACY] {problem}")
        raise SystemExit(1)
    print("[PRIVACY] Public outputs and Pages allowlist passed.")


if __name__ == "__main__":
    main()
