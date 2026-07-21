"""Validate and anonymize the private per-residence building catalog."""

from __future__ import annotations

import argparse
import csv
import hashlib
import hmac
import os
from pathlib import Path
from typing import Iterable


PRIVATE_CATALOG_FILE = Path("private/unit_catalog.csv")
PUBLIC_CATALOG_FILE = Path("data/unit_traits.csv")
UNIT_HISTORY_FILE = Path("data/unit_snapshots.csv")
EXPECTED_FLOOR_COUNTS = {2: 1, 3: 20, 4: 40, 5: 43, 6: 47, 7: 47,
                         8: 47, 9: 45, 10: 35, 11: 35}
PUBLIC_FIELDS = [
    "unit_id", "floorplan", "exposure", "secondary_exposure", "facade",
    "pool_facing", "outlook", "sunlight", "view", "floor_band",
    "disturbance", "confidence",
]


def read_csv(filename: Path) -> list[dict[str, str]]:
    with filename.open(newline="", encoding="utf-8") as file:
        return list(csv.DictReader(file))


def write_csv(filename: Path, rows: Iterable[dict[str, str]], fields: list[str]) -> None:
    filename.parent.mkdir(parents=True, exist_ok=True)
    with filename.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def source_unit_id(room_number: str) -> str:
    """Return the inventory provider's canonical identifier for a room number."""
    return f"CAP-{int(room_number):04d}"


def legacy_unit_id(source_id: str) -> str:
    """Return the original unsalted public identifier, only for history migration."""
    return f"listing-{hashlib.sha256(source_id.encode('utf-8')).hexdigest()[:8]}"


def secure_unit_id(source_id: str, secret: str) -> str:
    """Return a stable keyed identifier that cannot be enumerated without the secret."""
    if len(secret) < 32:
        raise ValueError("UNIT_ID_HASH_KEY must contain at least 32 characters")
    digest = hmac.new(secret.encode("utf-8"), source_id.encode("utf-8"), hashlib.sha256)
    return f"listing-{digest.hexdigest()[:16]}"


def validate_private_catalog(
    rows: list[dict[str, str]], expected_floor_counts: dict[int, int] | None = None,
) -> None:
    required = {
        "room_number", "floor", "floorplan", "exposure", "secondary_exposure",
        "facade", "pool_facing", "outlook", "sunlight", "view", "floor_band",
        "disturbance", "confidence",
    }
    if not rows or not required.issubset(rows[0]):
        raise ValueError(f"Private catalog is missing fields: {sorted(required - set(rows[0] if rows else []))}")
    rooms = [row["room_number"] for row in rows]
    expected_floor_counts = expected_floor_counts or EXPECTED_FLOOR_COUNTS
    expected_total = sum(expected_floor_counts.values())
    if len(rows) != expected_total or len(set(rooms)) != expected_total:
        raise ValueError(
            f"Private catalog must contain exactly {expected_total} unique residences"
        )
    counts: dict[int, int] = {}
    for row in rows:
        room, floor = int(row["room_number"]), int(row["floor"])
        if room // 100 != floor:
            raise ValueError(f"Room {room} does not belong to floor {floor}")
        counts[floor] = counts.get(floor, 0) + 1
        if row["exposure"] not in {"NE", "NW", "SE", "SW"}:
            raise ValueError(f"Room {room} has invalid exposure {row['exposure']!r}")
        if row["facade"] not in {"internal", "external"}:
            raise ValueError(f"Room {room} has invalid facade {row['facade']!r}")
        if (row["facade"] == "internal") != (row["pool_facing"] == "yes"):
            raise ValueError(f"Room {room} has inconsistent pool-facing metadata")
    if counts != expected_floor_counts:
        raise ValueError(f"Unexpected residence counts by floor: {counts}")


def compile_public_catalog(
    rows: list[dict[str, str]], secret: str,
    expected_floor_counts: dict[int, int] | None = None,
) -> list[dict[str, str]]:
    validate_private_catalog(rows, expected_floor_counts)
    compiled = []
    for row in rows:
        source_id = source_unit_id(row["room_number"])
        compiled.append({"unit_id": secure_unit_id(source_id, secret), **{
            field: row[field] for field in PUBLIC_FIELDS if field != "unit_id"
        }})
    if len({row["unit_id"] for row in compiled}) != len(compiled):
        raise ValueError("Keyed unit identifiers collided")
    # Never preserve source room order in the public artifact: even without an
    # explicit room column, PDF order would otherwise make the mapping guessable.
    return sorted(compiled, key=lambda row: row["unit_id"])


def migrate_history_ids(filename: Path, catalog: list[dict[str, str]], secret: str) -> int:
    rows = read_csv(filename)
    mapping = {
        legacy_unit_id(source_unit_id(row["room_number"])):
        secure_unit_id(source_unit_id(row["room_number"]), secret)
        for row in catalog
    }
    changed = 0
    for row in rows:
        replacement = mapping.get(row.get("unit_id", ""))
        if replacement:
            row["unit_id"] = replacement
            changed += 1
    if rows:
        write_csv(filename, rows, list(rows[0]))
    return changed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private", type=Path, default=PRIVATE_CATALOG_FILE)
    parser.add_argument("--output", type=Path, default=PUBLIC_CATALOG_FILE)
    parser.add_argument("--migrate-history", action="store_true")
    args = parser.parse_args()
    secret = os.environ.get("UNIT_ID_HASH_KEY", "")
    catalog = read_csv(args.private)
    compiled = compile_public_catalog(catalog, secret)
    write_csv(args.output, compiled, PUBLIC_FIELDS)
    print(f"[INFO] Compiled {len(compiled)} anonymous residences to {args.output}.")
    if args.migrate_history:
        changed = migrate_history_ids(UNIT_HISTORY_FILE, catalog, secret)
        print(f"[INFO] Migrated {changed} historical unit identifiers.")


if __name__ == "__main__":
    main()
