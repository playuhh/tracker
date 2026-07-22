"""Build a private, reviewable floor-plan template catalog from public images."""

from __future__ import annotations

import argparse
import csv
import hashlib
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen


USER_AGENT = "RentalMarketTracker/1.0"
MAX_PAGE_BYTES = 5_000_000
MAX_IMAGE_BYTES = 10_000_000
PLAN_PATTERN = re.compile(
    r"Floorplan_(?P<plan>[AB]\d+)(?:\(\d+\))?\.(?:png|jpe?g)$", re.IGNORECASE
)
REVIEW_FIELDS = [
    "geometry", "layout_efficiency", "layout_fit", "review_notes", "review_confidence",
]
CATALOG_FIELDS = [
    "floorplan", "bedrooms", "image_url", "image_file", "image_bytes",
    "image_sha256", "fetched_at", *REVIEW_FIELDS,
]


class ImageSourceParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.sources: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() not in {"img", "source"}:
            return
        values = dict(attrs)
        for field in ("src", "data-src", "srcset"):
            value = values.get(field)
            if not value:
                continue
            for candidate in value.split(","):
                self.sources.append(candidate.strip().split()[0])


def canonical_image_url(value: str) -> str:
    """Drop resize parameters while preserving the canonical media path."""
    parts = urlsplit(value)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def extract_floorplan_images(html: str) -> dict[str, str]:
    """Return one canonical one-/two-bedroom image URL per plan code."""
    parser = ImageSourceParser()
    parser.feed(html)
    result: dict[str, str] = {}
    for source in parser.sources:
        url = canonical_image_url(source)
        match = PLAN_PATTERN.search(urlsplit(url).path)
        if not match:
            continue
        plan = match.group("plan").upper()
        prior = result.get(plan)
        if prior and prior != url:
            raise ValueError(f"Floor plan {plan} has conflicting image URLs")
        result[plan] = url
    return dict(sorted(result.items()))


def _bounded_read(response, limit: int) -> bytes:
    body = response.read(limit + 1)
    if len(body) > limit:
        raise RuntimeError(f"Remote response exceeds {limit} bytes")
    return body


def fetch_bytes(url: str, limit: int, expected_type: str | None = None) -> bytes:
    if urlsplit(url).scheme != "https":
        raise ValueError("Floor-plan sources must use HTTPS")
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=20) as response:
        content_type = response.headers.get_content_type()
        if expected_type and not content_type.startswith(expected_type):
            raise RuntimeError(f"Unexpected content type {content_type!r} for {url}")
        return _bounded_read(response, limit)


def read_existing_reviews(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as file:
        return {row["floorplan"]: row for row in csv.DictReader(file)}


def read_review_overrides(path: Path | None) -> dict[str, dict[str, str]]:
    if path is None:
        return {}
    reviews = read_existing_reviews(path)
    for plan, row in reviews.items():
        missing = [field for field in REVIEW_FIELDS if not row.get(field)]
        if missing:
            raise ValueError(f"Review for {plan} is missing fields: {missing}")
    return reviews


def crawl_floorplans(
    page_url: str,
    output_dir: Path,
    catalog_path: Path,
    fetch: Callable[[str, int, str | None], bytes] = fetch_bytes,
    reviews_path: Path | None = None,
) -> list[dict[str, str]]:
    """Fetch a listing page, cache its A/B plan images, and preserve reviews."""
    html = fetch(page_url, MAX_PAGE_BYTES, "text/").decode("utf-8", errors="replace")
    return import_floorplans(
        html, output_dir, catalog_path, fetch=fetch, reviews_path=reviews_path
    )


def import_floorplans(
    html: str,
    output_dir: Path,
    catalog_path: Path,
    fetch: Callable[[str, int, str | None], bytes] = fetch_bytes,
    reviews_path: Path | None = None,
) -> list[dict[str, str]]:
    """Cache floor-plan images discovered in previously saved public HTML."""
    images = extract_floorplan_images(html)
    if not images:
        raise RuntimeError("Source page exposes no one- or two-bedroom floor-plan images")
    existing = read_existing_reviews(catalog_path)
    review_overrides = read_review_overrides(reviews_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows: list[dict[str, str]] = []
    for plan, image_url in images.items():
        body = fetch(image_url, MAX_IMAGE_BYTES, "image/")
        suffix = Path(urlsplit(image_url).path).suffix.casefold() or ".png"
        image_name = f"{plan}{suffix}"
        (output_dir / image_name).write_bytes(body)
        review = review_overrides.get(plan, existing.get(plan, {}))
        rows.append({
            "floorplan": plan,
            "bedrooms": "1" if plan.startswith("A") else "2",
            "image_url": image_url,
            "image_file": image_name,
            "image_bytes": str(len(body)),
            "image_sha256": hashlib.sha256(body).hexdigest(),
            "fetched_at": fetched_at,
            **{field: review.get(field, "") for field in REVIEW_FIELDS},
        })
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    with catalog_path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=CATALOG_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--page-url", help="Public page containing the floor-plan gallery")
    source.add_argument(
        "--html-file", type=Path,
        help="Saved public HTML used when the gallery blocks non-browser page requests",
    )
    parser.add_argument("--output-dir", type=Path, default=Path("private/floorplans"))
    parser.add_argument("--catalog", type=Path, default=Path("private/floorplan_catalog.csv"))
    parser.add_argument(
        "--reviews-file", type=Path,
        help="Private manual review CSV whose values override preserved catalog reviews",
    )
    args = parser.parse_args()
    if args.html_file:
        rows = import_floorplans(
            args.html_file.read_text(encoding="utf-8"), args.output_dir, args.catalog,
            reviews_path=args.reviews_file,
        )
    else:
        rows = crawl_floorplans(
            args.page_url, args.output_dir, args.catalog, reviews_path=args.reviews_file
        )
    print(f"[INFO] Cached {len(rows)} one-/two-bedroom floor plans in {args.output_dir}.")


if __name__ == "__main__":
    main()
