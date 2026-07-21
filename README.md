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
  unit-traits table (exposure, sunlight, view, floor band, and disturbance risk)

For the durable product context—privacy boundaries, data definitions, decision
interpretation, and the deployment runbook—see
[PROJECT_CONTEXT.md](PROJECT_CONTEXT.md).

## Setup

The core tracker has no third-party Python dependencies.

Set the target building's page ID locally; do not commit it:

```bash
export RENTAL_BUILDING_PAGE_ID="private-page-id"
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
```

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

The recommendation score is regenerated automatically on every run. It assigns
35 points to a currently advertised home's rent relative to its layout peers
and 65 points to verified exposure, direct sunlight, view, floor band, and
disturbance/privacy. The public layout score is the best verified current option
for that layout; coverage is shown so an unrated new listing is never silently
treated as average.

Before the first remote run, push the repository changes to GitHub and enable
**Settings -> Pages -> Source: GitHub Actions** in the GitHub repository. Also
add the page ID as the repository
secret `RENTAL_BUILDING_PAGE_ID`; without it, the collector intentionally stops
instead of exposing or guessing a building target.

```text
https://<account>.github.io/<repository>/
```

You can run it immediately from GitHub: **Actions -> Apartment Price Tracker
-> Run workflow**. To change it from daily to weekly, edit the `cron` line in
`.github/workflows/scraper.yml`.

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
