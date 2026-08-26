import json
import os
import urllib.request
from datetime import date, timedelta
from urllib.error import HTTPError

import yaml
from playwright.sync_api import sync_playwright

from db import init_db, save_scan
from sendgrid_email import send_email

PROPERTY_ID = 11806
SOURCE_ID = 98
ROOMS_URL = f"https://api.widgets.bookingsuedtirol.com/v6/properties/{PROPERTY_ID}/rooms?lang=de&sourceId={SOURCE_ID}"


def load_dotenv_if_exists(path: str = ".env"):
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"\'')
            os.environ.setdefault(key, value)


def load_config():
    load_dotenv_if_exists()
    cfg = {}
    cfg_path = os.getenv("CONFIG_PATH", "config.yaml")
    if os.path.exists(cfg_path):
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    elif os.path.exists("config.example.yaml"):
        with open("config.example.yaml", "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}

    env_keys = [
        "SENDGRID_API_KEY",
        "EMAIL_PROVIDER",
        "SMTP_HOST",
        "SMTP_PORT",
        "SMTP_USERNAME",
        "SMTP_PASSWORD",
        "SMTP_USE_TLS",
        "FROM_EMAIL",
        "TO_EMAIL",
        "BOARD_TYPE",
        "MIN_NIGHTS",
        "MAX_NIGHTS",
        "LOOKAHEAD_DAYS",
        "ALARM_THRESHOLD_EUR",
    ]
    for key in env_keys:
        value = os.getenv(key)
        if value is not None:
            cfg[key] = value
    return cfg


def fetch_json(url: str):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
            "Referer": "https://www.farmhouse-torgglerhof.com/",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        if exc.code != 403:
            raise

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(
            locale="de-DE",
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
        )
        response = page.goto(url, wait_until="domcontentloaded", timeout=30000)
        if response is None or response.status >= 400:
            status = response.status if response is not None else "no response"
            browser.close()
            raise RuntimeError(f"Browser request failed with status {status}")
        payload = response.text()
        browser.close()
        return json.loads(payload)


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

    board_type = str(cfg.get("BOARD_TYPE", "half_board")).lower()
    board_label = "Halbpension" if board_type in {"half_board", "halbpension", "hp"} else board_type

    best = None
    for offset in range(lookahead_days):
        start = date.today() + timedelta(days=offset)
        nightly_rate = cheapest_room["price"]
        total_stay_price = nightly_rate * min_nights
        test_window = {
            "price": nightly_rate,
            "total_price": total_stay_price,
            "start": start.isoformat(),
            "nights": min_nights,
            "room": cheapest_room["room"],
            "room_code": cheapest_room["room_code"],
            "board_type": board_label,
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
        f"Best: €{best['price']} pro Nacht inkl. {best['board_type']} "
        f"ab {best['start']} für {best['nights']} Nächte "
        f"(gesamt: €{best['total_price']}; {best['room']} / {best['room_code']})"
    )
    save_scan(best["price"], best["start"], best["nights"], best["room"])

    threshold = cfg.get("ALARM_THRESHOLD_EUR")
    if threshold is not None:
        try:
            if float(best["price"]) <= float(threshold):
                subject = f"Preisalarm: €{best['price']}/Nacht inkl. {best['board_type']}"
                content = (
                    f"Gefunden: €{best['price']} pro Nacht inkl. {best['board_type']} "
                    f"ab {best['start']} für {best['nights']} Nächte "
                    f"(gesamt: €{best['total_price']}; {best['room']} / {best['room_code']})"
                )
                send_email(subject, content, cfg)
                print("Email sent")
        except Exception as exc:
            print("Alert error:", exc)


if __name__ == "__main__":
    main()
