import os
import re
import math
import yaml
from datetime import date, timedelta
from playwright.sync_api import sync_playwright

from db import init_db, save_scan
from sendgrid_email import send_email

SELECTORS = {
    "price_regex": r"(\d+[.,]?\d*)\s?€",
}


def load_config():
    cfg_path = os.getenv("CONFIG_PATH", "config.yaml")
    if os.path.exists(cfg_path):
        with open(cfg_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    if os.path.exists("config.example.yaml"):
        with open("config.example.yaml", "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


def parse_price(text: str):
    m = re.search(SELECTORS["price_regex"], text)
    if not m:
        return None
    s = m.group(1).replace('.', '').replace(',', '.')
    try:
        return float(s)
    except ValueError:
        return None


def find_min_price_on_page(page):
    text = page.content()
    values = []
    for p in re.findall(SELECTORS["price_regex"], text):
        s = p.replace('.', '').replace(',', '.')
        try:
            values.append(float(s))
        except ValueError:
            pass
    return min(values) if values else None


def run_scan(cfg):
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto('https://www.farmhouse-torgglerhof.com/de/online-buchung/', wait_until='domcontentloaded', timeout=60000)
        page.wait_for_timeout(2000)
        best = None
        try:
            # this is intentionally simple: look for a minimum price on the site across several date windows
            for offset in range(0, int(cfg.get('LOOKAHEAD_DAYS', 30)), 7):
                start = date.today() + timedelta(days=offset)
                # open page again per offset to keep logic simple
                page.goto('https://www.farmhouse-torgglerhof.com/de/online-buchung/', wait_until='domcontentloaded', timeout=60000)
                page.wait_for_timeout(1500)
                price = find_min_price_on_page(page)
                if price is not None and (best is None or price < best['price']):
                    best = {'price': price, 'start': start.isoformat(), 'nights': int(cfg.get('MIN_NIGHTS', 2))}
        except Exception as e:
            print('Scan error:', e)
        browser.close()
        return best


def main():
    cfg = load_config()
    init_db()
    best = run_scan(cfg)
    if not best:
        print('No price found')
        return

    print(f"Best: €{best['price']} ab {best['start']} für {best['nights']} Nächte")
    save_scan(best['price'], best['start'], best['nights'], 'best-room')

    threshold = cfg.get('ALARM_THRESHOLD_EUR')
    if threshold is not None:
        try:
            if float(best['price']) <= float(threshold):
                subject = f"Preisalarm: €{best['price']}"
                content = f"Gefunden: €{best['price']} ab {best['start']} für {best['nights']} Nächte"
                send_email(subject, content, cfg)
                print('Email sent')
        except Exception as exc:
            print('Alert error:', exc)


if __name__ == '__main__':
    main()
