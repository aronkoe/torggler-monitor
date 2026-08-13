import json
import os
import urllib.request
from datetime import date, timedelta

import yaml

from db import init_db, save_scan
from sendgrid_email import send_email

PROPERTY_ID = 11806
SOURCE_ID = 98
ROOMS_URL = f"https://api.widgets.bookingsuedtirol.com/v6/properties/{PROPERTY_ID}/rooms?lang=de&sourceId={SOURCE_ID}"


def load_config():
    cfg_path = os.getenv("CONFIG_PATH", "config.yaml")
    if os.path.exists(cfg_path):
        with open(cfg_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    if os.path.exists("config.example.yaml"):
        with open("config.example.yaml", "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


def fetch_json(url: str):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
            "Referer": "https://www.farmhouse-torgglerhof.com/",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def find_cheapest_room():
    rooms = fetch_json(ROOMS_URL)
    if not isinstance(rooms, list):
        return None

    best = None
    for room in rooms:
        price = room.get("price_from")
        if price is None:
            continue
        try:
            value = float(price)
        except (TypeError, ValueError):
            continue
        if best is None or value < best["price"]:
            best = {
                "price": value,
                "room": room.get("title") or "Unbekanntes Zimmer",
                "room_code": room.get("room_code") or "-",
            }
    return best


def find_best_date_window(cfg):
    min_nights = int(cfg.get("MIN_NIGHTS", 2))
    lookahead_days = int(cfg.get("LOOKAHEAD_DAYS", 30))
    cheapest_room = find_cheapest_room()
    if cheapest_room is None:
        return None

    best = None
    for offset in range(lookahead_days):
        start = date.today() + timedelta(days=offset)
        test_window = {
            "price": cheapest_room["price"],
            "start": start.isoformat(),
            "nights": min_nights,
            "room": cheapest_room["room"],
            "room_code": cheapest_room["room_code"],
        }
        if best is None or test_window["price"] < best["price"]:
            best = test_window
    return best


def run_scan(cfg):
    return find_best_date_window(cfg)


def main():
    cfg = load_config()
    init_db()
    best = run_scan(cfg)
    if not best:
        print("No price found")
        return

    print(
        f"Best: €{best['price']} ab {best['start']} für {best['nights']} Nächte "
        f"({best['room']} / {best['room_code']})"
    )
    save_scan(best["price"], best["start"], best["nights"], best["room"])

    threshold = cfg.get("ALARM_THRESHOLD_EUR")
    if threshold is not None:
        try:
            if float(best["price"]) <= float(threshold):
                subject = f"Preisalarm: €{best['price']}"
                content = (
                    f"Gefunden: €{best['price']} ab {best['start']} für {best['nights']} Nächte "
                    f"({best['room']} / {best['room_code']})"
                )
                send_email(subject, content, cfg)
                print("Email sent")
        except Exception as exc:
            print("Alert error:", exc)


if __name__ == "__main__":
    main()
