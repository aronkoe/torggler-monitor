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
MAX_QUOTE_NIGHTS = 3


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
        "ALARM_THRESHOLD_2_NIGHTS_TOTAL_EUR",
        "ALARM_THRESHOLD_3_NIGHTS_TOTAL_EUR",
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

    availability = []
    chunk_size = 60
    today = date.today()
    max_stay_nights = max(min_nights, MAX_QUOTE_NIGHTS)
    chunk_step = max(1, chunk_size - min(max_stay_nights, chunk_size - 1))
    for offset in range(0, lookahead_days, chunk_step):
        chunk_from = today + timedelta(days=offset)
        chunk_to = chunk_from + timedelta(days=chunk_size)
        if chunk_from >= chunk_to:
            break
        chunk_query = (
            f"?from={chunk_from.isoformat()}"
            f"&to={chunk_to.isoformat()}"
            f"&guests=%5B%5B18%2C18%5D%5D&sourceId={SOURCE_ID}"
        )
        try:
            chunk_data = fetch_json(AVAILABILITIES_URL + chunk_query, browser_fallback=False)
            if isinstance(chunk_data, list):
                availability.extend(chunk_data)
        except Exception as exc:
            print(f"Availabilities chunk fetch failed for {chunk_from} to {chunk_to}: {exc}")

    available_starts = set()
    available_nights_by_start = {}
    for item in availability:
        if not item.get("date"):
            continue
        start_date = date.fromisoformat(item["date"])
        for departure in item.get("departures", []):
            departure_date = date.fromisoformat(departure["departure"])
            nights = (departure_date - start_date).days
            if departure_date == start_date or nights == min_nights:
                available_starts.add(start_date)
            if 1 <= nights <= MAX_QUOTE_NIGHTS:
                available_nights_by_start.setdefault(start_date, set()).add(nights)

    best = None
    request_delay = float(cfg.get("SCRAPER_REQUEST_DELAY", 0.5))
    for offset in range(lookahead_days):
        start = date.today() + timedelta(days=offset)
        if start not in available_starts:
            continue
        try:
            if offset:
                time.sleep(request_delay)
            offers = fetch_json(build_offers_url(start, min_nights), browser_fallback=False)
        except RuntimeError as exc:
            if "SCRAPER_PROXY_URL is unavailable" in str(exc):
                raise
            print(f"Offer fetch failed for {start}: {exc}")
            continue

        test_window = select_best_stay(
            offers,
            service_id,
            room_details,
            start,
            min_nights,
            board_label,
        )
        if test_window and (best is None or test_window["total_price"] < best["total_price"]):
            best = test_window

    if best is None:
        # Fallback to cheapest room base price from ROOMS_URL
        cheapest_base = None
        for room in rooms:
            if not isinstance(room, dict):
                continue
            price = room.get("price_from")
            if price is None:
                continue
            try:
                val = float(price)
            except (TypeError, ValueError):
                continue
            if cheapest_base is None or val < cheapest_base["price"]:
                cheapest_base = {
                    "price": val,
                    "room": room.get("title") or "Unbekanntes Zimmer",
                    "room_code": room.get("room_code") or "-",
                }
        if cheapest_base:
            best = {
                "price": cheapest_base["price"],
                "total_price": cheapest_base["price"] * min_nights,
                "start": today.isoformat(),
                "nights": min_nights,
                "room": cheapest_base["room"],
                "room_code": cheapest_base["room_code"],
                "board_type": board_label,
            }

    if best:
        best.update(
            {
                "following_night_price": None,
                "following_night_offer": None,
                "two_night_total": None,
                "two_night_offer": None,
                "two_night_start": None,
                "two_night_room": None,
                "three_night_total": None,
                "three_night_offer": None,
                "three_night_start": None,
                "three_night_room": None,
            }
        )
        start = date.fromisoformat(best["start"])
        if best.get("room_id") is not None:
            try:
                time.sleep(request_delay)
                quote = find_stay_quote(
                    start + timedelta(days=1),
                    1,
                    service_id,
                    room_details,
                    board_label,
                    best["room_id"],
                )
                if quote:
                    best["following_night_price"] = quote["total_price"]
                    best["following_night_offer"] = quote["offer_name"]
            except Exception as exc:
                print(f"Following-night offer fetch failed for {start + timedelta(days=1)}: {exc}")

        for nights in (2, 3):
            if nights == min_nights and best.get("room_id") is not None:
                quote = best
            else:
                quote = find_best_stay_for_nights(
                    nights,
                    today,
                    lookahead_days,
                    available_nights_by_start,
                    service_id,
                    room_details,
                    board_label,
                    request_delay,
                    {4, 5} if nights == 2 else {3, 4, 5},
                )
            if quote:
                prefix = "two" if nights == 2 else "three"
                best[f"{prefix}_night_total"] = quote["total_price"]
                best[f"{prefix}_night_offer"] = quote["offer_name"]
                best[f"{prefix}_night_start"] = quote["start"]
                best[f"{prefix}_night_room"] = quote["room"]
    return best


def build_offers_url(start, nights):
    end = start + timedelta(days=nights)
    return OFFERS_URL + (
        f"?correlationId=torggler-monitor&from={start.isoformat()}"
        f"&to={end.isoformat()}&guestCount=2&guests=%5B%5B18%2C18%5D%5D"
        f"&lang=de&maxAdults=4&maxChildren=3&sourceId={SOURCE_ID}"
    )


def select_best_stay(offers, service_id, room_details, start, nights, board_label, room_id=None):
    if not isinstance(offers, dict):
        return None

    offer_names = {
        str(offer.get("offer_id")): offer.get("title")
        for offer in offers.get("defaultOffers", [])
        if isinstance(offer, dict) and offer.get("offer_id") is not None
    }
    best_quote = None
    for rate in offers.get("rates", []):
        if (
            rate.get("service") != service_id
            or (room_id is not None and str(rate.get("room_id")) != str(room_id))
            or rate.get("price_total") is None
        ):
            continue
        try:
            total_price = float(rate["price_total"])
        except (TypeError, ValueError):
            continue
        if best_quote is None or total_price < best_quote["total_price"]:
            offer_name = offer_names.get(str(rate.get("offer_id")))
            room = room_details.get(
                str(rate.get("room_id")),
                {"name": "Unbekanntes Zimmer", "code": "-"},
            )
            display_room = room["name"]
            if offer_name and offer_name.lower() != "tagespreis":
                display_room = f"{display_room} – {offer_name}"
            best_quote = {
                "price": total_price / nights,
                "total_price": total_price,
                "start": start.isoformat(),
                "nights": nights,
                "room": display_room,
                "room_code": room["code"],
                "room_id": rate.get("room_id"),
                "board_type": board_label,
                "offer_name": offer_name if offer_name and offer_name.lower() != "tagespreis" else None,
            }
    return best_quote


def find_stay_quote(start, nights, service_id, room_details, board_label, room_id=None):
    offers = fetch_json(build_offers_url(start, nights), browser_fallback=False)
    return select_best_stay(
        offers,
        service_id,
        room_details,
        start,
        nights,
        board_label,
        room_id,
    )


def find_best_stay_for_nights(
    nights,
    today,
    lookahead_days,
    available_nights_by_start,
    service_id,
    room_details,
    board_label,
    request_delay,
    arrival_weekdays,
):
    best = None
    requests_made = 0
    for offset in range(lookahead_days):
        start = today + timedelta(days=offset)
        if (
            start.weekday() not in arrival_weekdays
            or nights not in available_nights_by_start.get(start, set())
        ):
            continue
        time.sleep(request_delay)
        requests_made += 1
        try:
            offers = fetch_json(build_offers_url(start, nights), browser_fallback=False)
        except RuntimeError as exc:
            if "SCRAPER_PROXY_URL is unavailable" in str(exc):
                raise
            print(f"{nights}-night offer fetch failed for {start}: {exc}")
            continue
        stay = select_best_stay(offers, service_id, room_details, start, nights, board_label)
        if stay and (best is None or stay["total_price"] < best["total_price"]):
            best = stay
    print(f"{nights}-night search checked {requests_made} available arrival dates")
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

        checkout = (date.fromisoformat(best["start"]) + timedelta(days=best["nights"])).isoformat()
        print(
            f"Best: €{best['price']} pro Nacht inkl. {best['board_type']} "
            f"von {best['start']} bis {checkout} ({best['nights']} Nächte) "
            f"(gesamt: €{best['total_price']}; {best['room']} / {best['room_code']})"
        )
        save_scan(
            best["price"],
            best["start"],
            best["nights"],
            best["room"],
            following_night_price=best.get("following_night_price"),
            following_night_offer=best.get("following_night_offer"),
            two_night_total=best.get("two_night_total"),
            two_night_offer=best.get("two_night_offer"),
            two_night_start=best.get("two_night_start"),
            two_night_room=best.get("two_night_room"),
            three_night_total=best.get("three_night_total"),
            three_night_offer=best.get("three_night_offer"),
            three_night_start=best.get("three_night_start"),
            three_night_room=best.get("three_night_room"),
        )

        following_day = date.fromisoformat(best["start"]) + timedelta(days=1)
        if best.get("following_night_price") is not None:
            offer_label = f" ({best['following_night_offer']})" if best.get("following_night_offer") else ""
            print(
                f"Following night: €{best['following_night_price']} pro Nacht ab {following_day}{offer_label}"
            )
        for nights in (2, 3):
            prefix = "two" if nights == 2 else "three"
            total = best.get(f"{prefix}_night_total")
            if total is not None:
                stay_start = date.fromisoformat(best[f"{prefix}_night_start"])
                stay_checkout = stay_start + timedelta(days=nights)
                stay_room = best.get(f"{prefix}_night_room") or best["room"]
                offer = best.get(f"{prefix}_night_offer")
                offer_label = f" ({offer})" if offer else ""
                print(
                    f"Best {nights}-night stay: €{total} total from {stay_start} "
                    f"through {stay_checkout} ({stay_room}{offer_label})"
                )

        alert_rules = (
            ("ALARM_THRESHOLD_EUR", 1, "price"),
            ("ALARM_THRESHOLD_2_NIGHTS_TOTAL_EUR", 2, "two_night_total"),
            ("ALARM_THRESHOLD_3_NIGHTS_TOTAL_EUR", 3, "three_night_total"),
        )
        configured_rules = 0
        triggered_alerts = []
        for threshold_key, nights, price_key in alert_rules:
            threshold = cfg.get(threshold_key)
            if threshold is None or str(threshold).strip() == "":
                continue
            try:
                threshold = float(threshold)
            except (TypeError, ValueError):
                print(f"Email alert disabled: {threshold_key} is not numeric")
                continue
            configured_rules += 1
            price = best.get(price_key)
            if price is None or float(price) > threshold:
                continue

            if nights == 1:
                stay_start = date.fromisoformat(best["start"])
                stay_room = best["room"]
                stay_total = best["total_price"]
                subject = f"Preisalarm: €{best['price']}/Nacht inkl. {best['board_type']}"
            else:
                prefix = "two" if nights == 2 else "three"
                stay_start = date.fromisoformat(best[f"{prefix}_night_start"])
                stay_room = best.get(f"{prefix}_night_room") or best["room"]
                stay_total = best[f"{prefix}_night_total"]
                subject = f"Preisalarm: {nights} Nächte für €{stay_total} inkl. {best['board_type']}"

            stay_checkout = stay_start + timedelta(days=nights)
            content = (
                f"Aufenthalt: {stay_start} bis {stay_checkout} ({nights} Nächte)<br>"
                f"Zimmer: {stay_room}<br>"
                f"Gesamt: €{stay_total}"
            )
            if nights > 1:
                content += f"<br>Durchschnitt: €{float(stay_total) / nights:.2f} pro Nacht"
            triggered_alerts.append((subject, content))

        if configured_rules == 0:
            print("Email alert skipped: no alert thresholds are configured")
        for subject, content in triggered_alerts:
            try:
                status_code, _ = send_email(subject, content, cfg)
                print(f"Email sent (HTTP {status_code})")
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
