from playwright.sync_api import sync_playwright
from datetime import datetime
import csv
import os
import time

APARTMENTS = {
    "The Capstone": "https://verisresidential.com/west-new-york-nj-apartments/the-capstone-at-port-imperial/",
    "RiverHouse 9": "https://verisresidential.com/weehawken-nj-apartments/riverhouse-9-at-port-imperial/",
    "RiverHouse 11": "https://verisresidential.com/weehawken-nj-apartments/riverhouse-11-at-port-imperial/",
}

CSV_FILE = "data/unit_prices.csv"

def scrape_apartment(apartment_name, url, page):
    print(f"[INFO] Navigating to {apartment_name} listings...")
    page.goto(url)

    # Click "View More" if present
    view_more_selector = "text=View More"
    while page.is_visible(view_more_selector):
        page.click(view_more_selector)
        print("[DEBUG] Clicked 'View More'...")
        time.sleep(2)

    cards = page.query_selector_all("div#prop_availability > div > div.omg_results_table > div > div")
    print(f"[INFO] Found {len(cards)} listings for {apartment_name}")

    units = []
    for idx, raw_card in enumerate(cards):
        try:
            card = raw_card.query_selector("div.omg-results-card-body")
            if not card:
                continue

            children = card.query_selector_all(":scope > div")
            if len(children) < 4:
                continue

            floorplan_group = children[0].query_selector_all(":scope > div")
            floorplan = floorplan_group[-1].inner_text().strip() if floorplan_group else "Unknown"
            sqft = children[1].inner_text().strip()
            move_in = children[2].inner_text().strip()
            price = children[3].inner_text().strip()

            if not price.startswith("$"):
                continue

            units.append({
                "timestamp": datetime.now().isoformat(),
                "apartment": apartment_name,
                "floorplan": floorplan,
                "sqft": sqft,
                "move_in": move_in,
                "price": price
            })
        except Exception as e:
            print(f"[WARN] Skipped unit {idx+1} from {apartment_name} due to error: {e}")
            continue

    return units

def save_units_csv(units, filename=CSV_FILE):
    os.makedirs(os.path.dirname(filename), exist_ok=True)
    print(f"[INFO] Saving {len(units)} units to {filename}...")

    with open(filename, "a", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=["timestamp", "apartment", "floorplan", "sqft", "move_in", "price"]
        )
        if f.tell() == 0:
            writer.writeheader()
        writer.writerows(units)
    print("[INFO] Save complete.")

def scrape_all():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        all_units = []

        for name, url in APARTMENTS.items():
            units = scrape_apartment(name, url, page)
            all_units.extend(units)

        browser.close()
        return all_units

if __name__ == "__main__":
    data = scrape_all()
    save_units_csv(data)
