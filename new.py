from playwright.sync_api import sync_playwright
from datetime import datetime
import csv
import time


def scrape_apartment_data():
    print("[INFO] Starting browser session...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        print("[INFO] Navigating to listing page...")
        page.goto(
            # "https://verisresidential.com/jersey-city-nj-apartments/the-blvd-collection/"
            "https://verisresidential.com/west-new-york-nj-apartments/the-capstone-at-port-imperial/"
            # "https://verisresidential.com/weehawken-nj-apartments/riverhouse-9-at-port-imperial/"
            # "https://verisresidential.com/weehawken-nj-apartments/riverhouse-11-at-port-imperial/"
            # "https://verisresidential.com/jersey-city-nj-apartments/haus25/"
        )

        # Click "View More" if present
        view_more_selector = "text=View More"
        while page.is_visible(view_more_selector):
            page.click(view_more_selector)
            print("[DEBUG] Clicked 'View More'...")
            time.sleep(3)

        print("[INFO] Scraping apartment units...")

        # Adjusted selector based on your new structure
        cards = page.query_selector_all(
            "div#prop_availability > div > div.omg_results_table > div > div"
        )
        print(f"[INFO] Found {len(cards)} apartment listings")

        units = []
        for idx, raw_card in enumerate(cards):
            try:
                card = raw_card.query_selector("div.omg-results-card-body")
                if not card:
                    continue

                children = card.query_selector_all(":scope > div")
                if len(children) < 4:
                    print(f"[WARN] Skipping item {idx + 1} due to unexpected structure.")
                    continue

                floorplan_group = children[0].query_selector_all(":scope > div")
                floorplan = (
                    floorplan_group[-1].inner_text().strip()
                    if floorplan_group else "Unknown"
                )

                sqft = children[1].inner_text().strip()
                move_in = children[2].inner_text().strip()
                price = children[3].inner_text().strip()

                if not price.startswith("$"):
                    print(
                        f"[WARN] Skipping item {idx + 1} due to invalid price: {price}"
                    )
                    continue

                print(
                    f"[DEBUG] Unit {idx+1}: {floorplan}, {sqft} sqft, Move-in: {move_in}, Price: {price}"
                )

                units.append(
                    {
                        "timestamp": datetime.now().isoformat(),
                        "floorplan": floorplan,
                        "sqft": sqft,
                        "move_in": move_in,
                        "price": price,
                    }
                )
            except Exception as e:
                print(f"[WARN] Skipping unit {idx + 1} due to error: {e}")
                continue


        browser.close()
        return units


def save_units_csv(units, filename="unit_prices.csv"):
    print(f"[INFO] Saving {len(units)} units to {filename}...")
    with open(filename, "a", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=["timestamp", "floorplan", "sqft", "move_in", "price"]
        )
        if f.tell() == 0:
            writer.writeheader()
        writer.writerows(units)
    print("[INFO] Save complete.")


if __name__ == "__main__":
    units = scrape_apartment_data()
    save_units_csv(units)
