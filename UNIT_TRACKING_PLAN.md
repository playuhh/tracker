# Unit-Level Apartment Price Tracking Plan

## Goal

Run the apartment collector daily without depending on a local computer, keep a
durable history of advertised unit prices, and make unit-level trends easy to
explore in the published GitHub Pages report.

## Recommendation

Use GitHub Actions as the scheduled collector and GitHub Pages as the primary
dashboard. Keep the repository CSV files as the source of truth.

Google Sheets is useful for ad-hoc analysis or sharing, but is not needed for
the dashboard. Do not make it a required part of the scheduled job.

```text
GitHub Actions (daily HTTP collector)
              |
              v
data/unit_prices.csv + data/unit_snapshots.csv (Git history)
              |
              v
report.py -> data/report.html -> GitHub Pages
```

## Current State

- `scraper.py` uses public HTTP inventory endpoints; it does not require a
  browser, Playwright, or Chromium.
- `.github/workflows/scraper.yml` schedules the collector daily, commits CSV
  history, and deploys `data/report.html` to GitHub Pages.
- `unit_snapshots.csv` already records the data needed for unit-level tracking:
  timestamp, floor plan, floor-plan ID, unit ID, square footage, availability,
  and price.
- The current report shows the latest unit table, but not a selected unit's
  historical price trend.

## Phase 1: Ensure the Cloud Automation Is Live

1. Push the migrated HTTP collector and workflow to the repository's default
   branch.
2. Enable GitHub Pages with **Source: GitHub Actions** in repository settings.
3. Manually run **Apartment Price Tracker** once from the Actions tab.
4. Confirm that the run:
   - collects non-empty floor-plan and unit snapshots;
   - commits `data/unit_prices.csv`, `data/unit_snapshots.csv`, and
     `data/report.html` when data changes; and
   - publishes the report at `https://account.github.io/tracker/`.
5. Monitor the first scheduled run and confirm it succeeds without local
   credentials or a browser installation.

Operational note: scheduled GitHub Actions runs use the default branch and may
occasionally be delayed. A manual `workflow_dispatch` run remains available as
a recovery path.

## Phase 2: Add Unit-Level Dashboard Exploration

Extend `report.py` and the generated static report without adding a database or
a client-side service.

### Report features

- A floor-plan filter and a unit selector populated from `unit_snapshots.csv`.
- An inline SVG price chart for the selected unit across snapshots.
- Summary fields for the selected unit:
  - first observed date;
  - latest observed date;
  - first and latest advertised rent;
  - dollar change and percentage change;
  - number of snapshots in which the unit was visible.
- A sortable current-unit table with floor plan, unit, availability, current
  rent, and change since first observation.
- A clear empty state for units with a single snapshot or no retained history.

### Data semantics

- A unit is identified by `(apartment, floorplan_id, unit_id)`.
- A missing unit in a later snapshot means it is no longer advertised; it is
  not treated as a $0 price or a confirmed lease.
- If a unit disappears and returns, retain both observation periods and draw a
  visible gap in its trend line.
- Continue to store displayed rents as formatted strings in the existing CSV
  schema; convert to cents only when calculating comparisons or chart points.

### Implementation steps

1. Add report-level helpers that group snapshots by unit identity and sort them
   by timestamp.
2. Calculate per-unit first/latest values and price deltas.
3. Embed a compact JSON representation of unit history in `report.html`.
4. Add dependency-free JavaScript to update the unit summary and inline SVG
   chart when a filter changes.
5. Preserve the existing floor-plan sparklines and current-availability table.
6. Add unit tests for unit grouping, price deltas, one-point histories, and
   disappear/reappear gaps.

## Phase 3: Optional Alerts

After the dashboard is useful, decide whether price changes need proactive
notifications.

- Trigger only for meaningful events: a new unit, a unit price decrease, or a
  configured price threshold.
- Keep notification credentials in GitHub Actions Secrets.
- Prefer a single selected channel (for example, email, Telegram, or Pushover)
  rather than adding multiple services.
- Ensure notification failures do not prevent CSV history or Pages deployment.

## Optional Phase 4: Google Sheets Export

Only add this if a spreadsheet is regularly needed for manual analysis or
sharing.

1. Create a Google service account and share the target spreadsheet with its
   service-account email.
2. Store its JSON credentials as a GitHub Actions Secret; do not commit the
   credential file.
3. Add a workflow input or repository variable that explicitly enables the
   Sheets export.
4. Install `gspread` only in runs where the export is enabled, then invoke the
   existing optional exporter.
5. Keep CSV files and the static report as the source of truth if Sheets is
   unavailable.

## Validation

- Unit tests cover historical grouping, selected-unit summaries, chart gaps,
  and current-table values.
- A report generated from fixture history renders a unit selector and a
  correct selected-unit trend.
- A manual GitHub Actions run updates snapshots and deploys the dashboard.
- The published page works without a local server and shows current unit data.

## Definition of Done

- Daily collection, history persistence, and Pages deployment run entirely in
  GitHub Actions.
- The published report lets a user inspect a unit's price history, not only the
  latest availability list.
- A local machine is needed only for optional development or diagnosis, never
  for scheduled collection.
