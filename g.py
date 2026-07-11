"""Optional Google Sheets output for apartment price snapshots."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable


def update_google_sheet(units: Iterable[dict[str, str]]) -> bool:
    """Append rows only when the required Google configuration is present."""
    credentials = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
    sheet_name = os.getenv("GOOGLE_SHEET_NAME")
    if not credentials or not sheet_name:
        print(
            "[INFO] Google Sheets export skipped. Set GOOGLE_SERVICE_ACCOUNT_FILE and "
            "GOOGLE_SHEET_NAME to enable it."
        )
        return False

    credentials_path = Path(credentials)
    if not credentials_path.exists():
        raise FileNotFoundError(f"Google service-account file not found: {credentials_path}")

    import gspread

    rows = [
        [
            unit["timestamp"],
            unit["apartment"],
            unit["floorplan"],
            unit["sqft"],
            unit["move_in"],
            unit["price"],
        ]
        for unit in units
    ]
    if not rows:
        return False

    sheet = gspread.service_account(filename=str(credentials_path)).open(sheet_name).sheet1
    sheet.append_rows(rows, value_input_option="USER_ENTERED")
    print(f"[INFO] Appended {len(rows)} rows to Google Sheet '{sheet_name}'.")
    return True
