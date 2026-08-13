# Torggler Monitor

Monitor Preise für die Online-Buchung des Farmhouse Torgglerhofs.

## Features
- Playwright scraper für die Booking-Seite
- SQLite-Historie der günstigsten Preise
- E-Mail-Alarm über SendGrid
- kleines Flask-Dashboard mit Chart
- GitHub Actions Cron-Trigger
- Preis wird als Nachtpreis inkl. Halbpension interpretiert; Gesamtpreis berechnet sich aus Nachtpreis × Anzahl Nächte

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

## Production setup (free / low effort)

Use this stack:
- GitHub Actions: daily scraper run and DB update
- SendGrid: free email alerts
- Render or Railway: free dashboard hosting

### Dashboard hosting
1. Push this repo to GitHub.
2. Create a Render web service from the repo.
3. Set runtime to Python.
4. Start command: `gunicorn app:app --bind 0.0.0.0:$PORT`
5. Add env vars:
   - `PORT` (Render sets it automatically)
   - `SENDGRID_API_KEY`
   - `FROM_EMAIL`
   - `TO_EMAIL`
   - `ALARM_THRESHOLD_EUR`
   - `BOARD_TYPE=half_board`
   - `MIN_NIGHTS=2`
   - `LOOKAHEAD_DAYS=30`

### Email alerts
1. Create a free SendGrid account.
2. Create an API key with Mail Send permissions.
3. Save the key in GitHub Secrets as `SENDGRID_API_KEY`.
4. Set `FROM_EMAIL` and `TO_EMAIL` in repo secrets or config.
5. Keep `ALARM_THRESHOLD_EUR` low enough to trigger real alerts.

### Daily update
The workflow runs every day at 08:00 UTC and commits the updated `data.db` back to the repo.
The dashboard uses the latest DB row and shows `Letztes Update` in the UI.

### One-click deploy for Render
This repo includes a `render.yaml` file so the app can be deployed directly from the repo in Render.
Once you connect the GitHub repo to Render and create the web service, it will run with the default gunicorn command.

## Notes
The website is dynamic; the scraper is intentionally simple and uses a resilient price pattern. If selectors change, update `scraper.py`.
