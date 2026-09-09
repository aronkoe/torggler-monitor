import json
import os
import time
import urllib.request
from datetime import date, timedelta

import requests
import yaml
from playwright.sync_api import sync_playwright

from db import init_db, save_scan
from sendgrid_email import send_email

# Torggler Price Monitor Scraper
PROPERTY_ID = 11806
SOURCE_ID = 98
ROOMS_URL = f"https://api.widgets.bookingsuedtirol.com/v6/properties/{PROPERTY_ID}/rooms?lang=de&sourceId={SOURCE_ID}"
OFFERS_URL = f"https://api.widgets.bookingsuedtirol.com/v6/properties/{PROPERTY_ID}/offers"
AVAILABILITIES_URL = f"https://api.widgets.bookingsuedtirol.com/v6/properties/{PROPERTY_ID}/availabilities"


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
        "SCRAPER_PROXY_URL",
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


def get_proxy_url():
    return os.getenv("SCRAPER_PROXY_URL", "").strip() or None


def fetch_json(url: str, browser_fallback: bool = True, retries: int = 3):
    # Tier 1: Try requests.Session with session initialization on target website
    response_status = None
    try:
        session = requests.Session()
        proxy_url = get_proxy_url()
        if proxy_url:
            session.proxies.update({"http": proxy_url, "https": proxy_url})
        session_headers = {
            "User-Agent": DEFAULT_HEADERS["User-Agent"],
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": DEFAULT_HEADERS["Accept-Language"],
        }
        session.get("https://www.farmhouse-torgglerhof.com/de/online-buchung/", headers=session_headers, timeout=10)
        api_headers = DEFAULT_HEADERS.copy()
        api_headers["Referer"] = "https://www.farmhouse-torgglerhof.com/de/online-buchung/"
        for attempt in range(retries):
            response = session.get(url, headers=api_headers, timeout=20)
            response_status = response.status_code
            if response.status_code < 400:
                return response.json()
            if response.status_code != 429:
                print(f"Requests Session fetch returned status {response.status_code}, trying urllib...")
                break
            delay = 5.0 * (2 ** attempt)
            print(f"Booking API rate limited (429); retrying in {delay:.0f}s")
            time.sleep(delay)
    except Exception as exc:
        if "402 Payment Required" in str(exc) or "ProxyError" in str(exc):
            raise RuntimeError("SCRAPER_PROXY_URL is unavailable or out of bandwidth") from exc
        print(f"Requests Session fetch failed ({exc}), trying urllib...")

    if response_status == 429:
        raise RuntimeError(f"Booking API rate limit persisted for {url}")

    # Tier 2: Try urllib
    req = urllib.request.Request(url, headers=DEFAULT_HEADERS)
    try:
        proxy_url = get_proxy_url()
        if proxy_url:
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
            )
        else:
            opener = urllib.request.build_opener()
        with opener.open(req, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        if "402 Payment Required" in str(exc):
            raise RuntimeError("SCRAPER_PROXY_URL is unavailable or out of bandwidth") from exc
        print(f"Urllib fetch failed ({exc}), trying Playwright expect_response...")

    if not browser_fallback:
        raise RuntimeError(f"Could not fetch booking API URL: {url}")

    # Tier 3: Try Playwright expect_response on site booking page
    browser_error = None
    with sync_playwright() as playwright:
        launch_options = {
            "headless": True,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox",
            ],
        }
        proxy_url = get_proxy_url()
        if proxy_url:
            launch_options["proxy"] = {"server": proxy_url}
        browser = playwright.chromium.launch(**launch_options)
        context = browser.new_context(
            locale="de-DE",
            user_agent=DEFAULT_HEADERS["User-Agent"],
        )
        page = context.new_page()
        try:
            with page.expect_response(
                lambda r: "bookingsuedtirol.com" in r.url and "rooms" in r.url and r.status == 200,
                timeout=30000,
            ) as resp_info:
                page.goto(
                    "https://www.farmhouse-torgglerhof.com/de/online-buchung/",
                    wait_until="domcontentloaded",
                    timeout=30000,
                )
            resp = resp_info.value
            return resp.json()
        except Exception as exc:
            browser_error = exc
        finally:
            browser.close()

    raise RuntimeError(f"Could not fetch booking API: {browser_error}") from browser_error


def find_best_date_window(cfg):
    min_nights = int(cfg.get("MIN_NIGHTS", 1))
    lookahead_days = int(cfg.get("LOOKAHEAD_DAYS", 365))
    board_type = str(cfg.get("BOARD_TYPE", "half_board")).lower()
    board_label = "Halbpension" if board_type in {"half_board", "halbpension", "hp"} else board_type
    service_id = 3 if board_type in {"half_board", "halbpension", "hp"} else 2

    rooms = fetch_json(ROOMS_URL)
    room_details = {
        str(room.get("room_id")): {
            "name": room.get("title") or "Unbekanntes Zimmer",
            "code": room.get("room_code") or "-",
        }
        for room in rooms
        if isinstance(room, dict) and room.get("room_id") is not None
    }

    availability_query = (
        f"?from={date.today().isoformat()}"
        f"&to={(date.today() + timedelta(days=lookahead_days)).isoformat()}"
        f"&guests=%5B%5B18%2C18%5D%5D&sourceId={SOURCE_ID}"
    )
    availability = fetch_json(AVAILABILITIES_URL + availability_query, browser_fallback=False)
    available_starts = {
        date.fromisoformat(item["date"])
        for item in availability
        if item.get("date")
        and any(
            departure.get("departure") == date.fromisoformat(item["date"]).isoformat()
            or departure.get("departure") == (date.fromisoformat(item["date"]) + timedelta(days=min_nights)).isoformat()
            for departure in item.get("departures", [])
        )
    }

    best = None
    request_delay = float(cfg.get("SCRAPER_REQUEST_DELAY", 1.5))
    for offset in range(lookahead_days):
        start = date.today() + timedelta(days=offset)
        if start not in available_starts:
            continue
        end = start + timedelta(days=min_nights)
        query = (
            f"?correlationId=torggler-monitor&from={start.isoformat()}"
            f"&to={end.isoformat()}&guestCount=2&guests=%5B%5B18%2C18%5D%5D"
            f"&lang=de&maxAdults=4&maxChildren=3&sourceId={SOURCE_ID}"
        )
        try:
            if offset:
                time.sleep(request_delay)
            offers = fetch_json(OFFERS_URL + query, browser_fallback=False)
        except RuntimeError as exc:
            if "SCRAPER_PROXY_URL is unavailable" in str(exc):
                raise
            print(f"Offer fetch failed for {start}: {exc}")
            continue

        offer_names = {
            str(offer.get("offer_id")): offer.get("title")
            for offer in offers.get("defaultOffers", [])
            if isinstance(offer, dict) and offer.get("offer_id") is not None
        }
        for rate in offers.get("rates", []):
            if rate.get("service") != service_id or rate.get("price_total") is None:
                continue
            try:
                total_stay_price = float(rate["price_total"])
            except (TypeError, ValueError):
                continue
            room = room_details.get(
                str(rate.get("room_id")),
                {"name": "Unbekanntes Zimmer", "code": "-"},
            )
            room_name = room["name"]
            offer_name = offer_names.get(str(rate.get("offer_id")))
            display_room = room_name
            if offer_name and offer_name.lower() != "tagespreis":
                display_room = f"{room_name} – {offer_name}"
            test_window = {
                "price": total_stay_price / min_nights,
                "total_price": total_stay_price,
                "start": start.isoformat(),
                "nights": min_nights,
                "room": display_room,
                "room_code": room["code"],
                "board_type": board_label,
            }
            if best is None or test_window["total_price"] < best["total_price"]:
                best = test_window
    return best


def run_scan(cfg):
    return find_best_date_window(cfg)


def main():
    try:
        cfg = load_config()
        if not get_proxy_url():
            print("SCRAPER_PROXY_URL is not set; GitHub Actions may receive HTTP 403 from the booking API")
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
