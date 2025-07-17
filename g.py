import gspread

gc = gspread.service_account(filename="g.json")

# Open the sheet
sheet = gc.open("apt data").sheet1

# Example: update cell A1
print(sheet.get("A1"))


def update_google_sheet(units):
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

    # Append rows to the sheet (at the bottom)
    sheet.append_rows(rows, value_input_option="USER_ENTERED")
