# Torggler Monitor

Monitor Preise für die Online-Buchung des Farmhouse Torgglerhofs.

## Features
- Playwright scraper für die Booking-Seite
- SQLite-Historie der günstigsten Preise
- E-Mail-Alarm über SendGrid
- kleines Flask-Dashboard mit Chart
- GitHub Actions Cron-Trigger

## Local setup

```bash
cd ~/torggler-monitor
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install --with-deps
cp config.example.yaml config.yaml
# configure SENDGRID_API_KEY, FROM_EMAIL, TO_EMAIL, ALARM_THRESHOLD_EUR
python scraper.py
python app.py
```

Open http://localhost:5000

## GitHub Actions
The workflow in `.github/workflows/scrape.yml` runs daily and can push the SQLite database back to the repo if changed.

## Notes
The website is dynamic; the scraper is intentionally simple and uses a resilient price pattern. If selectors change, update `scraper.py`.
