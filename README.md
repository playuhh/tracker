# Apartment Price Tracker

Tracks advertised rental-market prices and saves each run as a CSV snapshot.
Published history uses stable pseudonyms for the building, layouts, and listings.
The scheduled collector uses direct, public inventory requests;
it does not launch a browser or install Chromium.

The tracker records:

- floor plan, square footage, move-in date, and advertised starting rent
- anonymized listing key, rent, availability date, and anonymized layout ID
- UTC snapshot timestamp
- personalized, layout-level recommendation inputs from an anonymous verified
  residence catalog (exposure, pool-facing/exterior facade, sunlight, view,
  floor band, disturbance risk, and reviewed floor-plan geometry)

For the durable product context—privacy boundaries, data definitions, decision
interpretation, and the deployment runbook—see
[PROJECT_CONTEXT.md](PROJECT_CONTEXT.md).

## Setup

The core tracker has no third-party Python dependencies.

Set the target building's page ID locally; do not commit it:

```bash
export RENTAL_BUILDING_PAGE_ID="private-page-id"
export UNIT_ID_HASH_KEY="a-private-random-string-with-at-least-32-characters"
python3 -m venv .venv
.venv/bin/python scraper.py --no-sheets
```

The tracker uses these six local files; each complete run regenerates the five
history/report files while retaining the verified traits table:

- `data/unit_prices.csv`: floor-plan price history
- `data/unit_snapshots.csv`: anonymized individual-listing history, retained only
  to calculate aggregate inventory and price movement
- `data/floorplan_daily.csv`: daily floor-plan inventory, min/median/max asking
  rent, rent per square foot, newly visible units, and price reductions
- `data/scrape_runs.csv`: complete collection-run coverage, used to distinguish
  an absent advertisement from a failed or partial scrape
- `data/unit_traits.csv`: anonymous, verified unit characteristics used only to
  calculate layout-level recommendations; it contains no apartment numbers or
  exact floors
- `data/report.html`: an anonymous layout market dashboard with inventory, asking-rent,
  rent-per-square-foot, renter-timing signals, and personalized fit; it never
  publishes individual listings

On later runs, the terminal reports every floor-plan price increase or decrease
compared with the last saved price. Open the dashboard after a run:

```bash
open data/report.html
```

Useful flags:

```bash
# Verify the collector without writing CSV rows.
.venv/bin/python scraper.py --dry-run --no-sheets

# Regenerate the dashboard from saved history without collecting new data.
.venv/bin/python scraper.py --report-only

# Validate an ignored private registry without writing history or reports.
.venv/bin/python scraper.py --probe-properties --no-write
```

## Private portfolio mode

Portfolio collection is configured with an ignored JSON registry rather than
tracked property identities or source IDs. Copy
`config/property-registry.example.json` to `private/property-registry.json`,
replace its placeholders locally, and set:

```bash
export RENTAL_PROPERTY_REGISTRY="$PWD/private/property-registry.json"
python3 scraper.py --probe-properties --no-write
```

Each property has a stable internal key and anonymous public label, its public
page and private source configuration, location/provider metadata, adapter
version, enablement and compliance state, verification date, capability flags,
and mode. New properties must start as `market_only`. They can receive market
recommendations (budget, value, timing), but the UI must say **Personal fit
unavailable — no verified property traits** instead of guessing or awarding
neutral points. `trait_enriched` requires a property-bound private catalog,
keyed identifiers, provenance/confidence, schema and coverage validation.

The legacy `RENTAL_BUILDING_PAGE_ID` entry point remains available for Building
A. Portfolio snapshots are atomic: no property history is written unless every
enabled property validates, so a failed source is never interpreted as zero
inventory. Cross-property reports belong under ignored `private/`; public
portfolio publishing remains off unless the explicit permission gate is set.

All requests for the provider domain share one sequential governor with a fixed
interval, total request/retry/property/elapsed-time budgets, and non-sensitive
metrics. HTTP 401/403/429 or challenge content opens the domain circuit breaker,
stops remaining requests, and prevents history writes. No alternate endpoint is
attempted. See [the compatibility notes](docs/portfolio-compatibility.md) before
adding another property.

## Private floor-plan geometry catalog

`floorplan_catalog.py` discovers one- and two-bedroom image assets whose media
names contain `Floorplan_A#` or `Floorplan_B#`, removes thumbnail query strings,
caches the canonical images under ignored `private/floorplans/`, and records a
SHA-256 digest in ignored `private/floorplan_catalog.csv`. A suffix such as
`B6(1).png` is normalized to plan `B6`. The image URL pattern is useful for
refreshing known assets, but the gallery remains the discovery source because
not every plan uses an identical filename.

When the public gallery does not permit a standard HTTP page fetch, save its
rendered image elements to ignored `private/floorplan_source.html`, then run:

```bash
python3 floorplan_catalog.py \
  --html-file private/floorplan_source.html \
  --reviews-file private/floorplan_reviews.csv
UNIT_ID_HASH_KEY="a-private-random-string-with-at-least-32-characters" \
  python3 catalog.py
```

Manual reviews use `preferred`, `acceptable`, or `penalized`, and separately
classify circulation/usable-space efficiency. Refreshes preserve
reviews (or apply the review override file), while the anonymous public unit
traits inherit only geometry, fit, and review confidence—not source URLs,
images, notes, room numbers, or exact floors.

## Google Sheets (optional)

The collector works without Google credentials. To append each floor-plan
snapshot to a sheet, install the optional dependency, create a Google
service-account JSON file, share the target sheet with its service-account
email, then set:

```bash
.venv/bin/pip install gspread
export GOOGLE_SERVICE_ACCOUNT_FILE="/absolute/path/to/service-account.json"
export GOOGLE_SHEET_NAME="apt data"
.venv/bin/python scraper.py
```

Credentials are excluded from Git. Price history and derived daily aggregates
are committed by the remote tracker so renter-facing trends persist between runs.

## Remote daily tracking

GitHub Actions runs the standard-library collector every day at `13:00 UTC`
(9 AM during Toronto daylight time, 8 AM during standard time). It commits the
anonymized histories, derived aggregates, and regenerated report back to the repository, then deploys
the report to GitHub Pages.

The workflow first runs all tests, audits the current public boundary, collects,
audits generated output again, and only then may commit. Pages is built by an
explicit allowlist containing only `data/report.html`; private reports and
registry files cannot enter the artifact. The report shows its latest complete
snapshot, age, complete-history day count, collector health, and a warning after
the 36-hour stale threshold.

The recommendation score is regenerated automatically on every run. It assigns
35 points to a currently advertised home's rent relative to its layout peers
and 65 points to verified exposure, direct sunlight, pool-facing preference,
view, floor band, and disturbance/privacy. The weights and point values are
published at the bottom of the dashboard. The public layout score is the best
verified current option for that layout; coverage is shown so an unrated new
listing is never silently treated as average.

Irregular geometry alone contributes no penalty. A usable but mildly awkward
plan deducts 4 points; substantial circulation or hard-to-use space deducts 15.
This is not a gate, so price and stronger residence traits can offset it.

Floor 5 is a hard minimum when an eligible home exists, floors 5–6 are
acceptable but cannot receive `Best match`, and floor 7+ is preferred. A
skyline label also requires floor 7+; exposure alone is not treated as proof of
a Manhattan view. The private catalog distinguishes PDF/geometry modeling from
resident-confirmed evidence.

Before the first remote run, push the repository changes to GitHub and enable
**Settings -> Pages -> Source: GitHub Actions** in the GitHub repository. Also
add the page ID as the repository
secret `RENTAL_BUILDING_PAGE_ID`; without it, the collector intentionally stops
instead of exposing or guessing a building target.
Add `UNIT_ID_HASH_KEY` as a second repository secret. It must be the same
32-plus-character random key used to compile `data/unit_traits.csv`; the keyed
identifier lets the collector join each advertised residence to its verified
traits without publishing an enumerable room-number mapping.

```text
https://<account>.github.io/<repository>/
```

You can run it immediately from GitHub: **Actions -> Apartment Price Tracker
-> Run workflow**. To change it from daily to weekly, edit the `cron` line in
`.github/workflows/scraper.yml`.

For GitHub CLI authentication failures inside Codex, workflow dispatch, and run
log inspection, follow the
[GitHub Actions troubleshooting runbook](docs/github-actions-troubleshooting.md).

## Troubleshooting a site change

The collector sends one overview request and one detail request per floor plan,
with short timeouts and limited retries. It deliberately stops before writing a
snapshot if the site returns no floor plans, malformed records, or a unit count
that does not match the overview. This protects the historical data from empty
or partial runs.

If that happens, use a browser manually to inspect network activity on the
availability page. Find the public `admin-ajax.php` request with
`action=omg_apt_search_main_query`, then the `floorplan_query` detail requests.
Update the fixtures and parser only after confirming the new public response
shape. The browser is a discovery and debugging tool, not part of the scheduled
workflow.
