import json
import os
import urllib.request
from datetime import date, timedelta
from urllib.error import HTTPError

import requests
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


DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.farmhouse-torgglerhof.com/",
    "Origin": "https://www.farmhouse-torgglerhof.com",
    "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
}


def fetch_json(url: str):
    # Tier 1: Try requests
    try:
        resp = requests.get(url, headers=DEFAULT_HEADERS, timeout=15)
        if resp.status_code < 400:
            return resp.json()
        print(f"Requests fetch returned status {resp.status_code}, trying urllib...")
    except Exception as exc:
        print(f"Requests fetch failed ({exc}), trying urllib...")

    # Tier 2: Try urllib
    req = urllib.request.Request(url, headers=DEFAULT_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        print(f"Urllib fetch failed ({exc}), trying Playwright response interceptor...")

    # Tier 3: Try Playwright with response interception on site booking page
    browser_error = None
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox",
            ],
        )
        context = browser.new_context(
            locale="de-DE",
            user_agent=DEFAULT_HEADERS["User-Agent"],
            bypass_csp=True,
        )
        page = context.new_page()
        captured_data = []
        captured_statuses = []

        def handle_response(response):
            if "bookingsuedtirol.com" in response.url:
                captured_statuses.append(f"{response.status} {response.url}")
                if "rooms" in response.url and response.status == 200:
                    try:
                        captured_data.append(response.json())
                    except Exception:
                        pass

        page.on("response", handle_response)
        try:
            # First try direct request in browser context
            api_resp = context.request.get(url, headers=DEFAULT_HEADERS, timeout=15000)
            if api_resp is not None and api_resp.status < 400:
                browser.close()
                return json.loads(api_resp.text())

            # Navigate to booking page where widget fires the API call natively
            page.goto(
                "https://www.farmhouse-torgglerhof.com/de/online-buchung/",
                wait_until="domcontentloaded",
                timeout=25000,
            )
            page.wait_for_timeout(4000)

            if not captured_data:
                try:
                    eval_res = page.evaluate(
                        """async (apiUrl) => {
                            const r = await fetch(apiUrl, { headers: { 'Accept': 'application/json' } });
                            return { status: r.status, text: await r.text() };
                        }""",
                        url,
                    )
                    if eval_res and eval_res.get("status") == 200:
                        captured_data.append(json.loads(eval_res["text"]))
                except Exception as eval_exc:
                    print(f"Page evaluate fetch failed: {eval_exc}")
        except Exception as exc:
            browser_error = exc
        finally:
            browser.close()

        if captured_data:
            return captured_data[0]

        print(f"Intercepted responses status: {captured_statuses}")

    raise RuntimeError(f"Could not fetch booking API (error: {browser_error}, statuses: {captured_statuses})") from browser_error


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
    try:
        cfg = load_config()
        init_db()
        best = run_scan(cfg)
        if not best:
            print("No price found")
            import sys
            sys.exit(1)

        print(
            f"Best: €{best['price']} pro Nacht inkl. {best['board_type']} "
            f"ab {best['start']} für {best['nights']} Nächte "
            f"(gesamt: €{best['total_price']}; {best['room']} / {best['room_code']})"
        )
        save_scan(best["price"], best["start"], best["nights"], best["room"])

        threshold = cfg.get("ALARM_THRESHOLD_EUR")
        if threshold is not None and str(threshold).strip() != "":
            try:
                thresh_val = float(threshold)
                if float(best["price"]) <= thresh_val:
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
    except Exception as exc:
        import traceback
        import sys
        print(f"Scraper execution failed: {exc}")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
