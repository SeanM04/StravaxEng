# StravaXEng

A Django + PostgreSQL ETL and visualization app for Strava activity data. Syncs activities via the Strava API and presents them across seven analytics pages with Chart.js charts, personal records, streak tracking, and an AI coach summary.

---

## Pages

| Page | URL | Description |
| --- | --- | --- |
| Dashboard | `/` | Stat cards + run distance chart |
| Activities | `/activities/` | Sortable, filterable paginated table |
| Activity detail | `/activities/<strava_id>/` | Stats + segment achievements for one activity |
| Analytics | `/analytics/` | Weekly volume bar chart + pace trend line |
| Records & Streaks | `/records/` | PRs, distance bests, daily/weekly activity streaks, sport breakdown |
| AI Coach Notes | `/coach/` | Avg weekly km, pace trend, long-run count |
| Pipeline Health | `/pipeline/` | **Sync now** button, sync history, success rate |
| Settings | `/settings/` | Strava connection status + re-connect steps |

All pages require a logged-in Django user (`/admin/login/`).

---

## Project structure

```text
StravaXeng/
├── .env                           # Credentials (never commit — see .env.example)
├── .env.example                   # Template with placeholder values
├── requirements.txt
├── requirements-dev.txt           # + pylint and the docs builder
├── Procfile                       # gunicorn entry point for production
├── pytest.ini
├── manage.py
├── docs/                          # Documentation (Markdown + generated HTML)
├── scripts/
│   ├── sync_daily.cmd             # Daily sync, run by Windows Task Scheduler
│   └── build_docs.py              # Builds docs/*.html from docs/*.md
├── StravaxEng/                    # Django project config
│   ├── settings.py
│   ├── urls.py
│   └── wsgi.py
└── core/                          # Main app
    ├── models.py                  # StravaToken, SyncLog, Activity, Achievement, BestEffort
    ├── views.py                   # Page views, Sync now, health check
    ├── urls.py
    ├── admin.py
    ├── strava_client.py           # OAuth2 token renewal, API requests, rate limits
    ├── best_effort.py             # Best efforts from GPS streams
    ├── achievements.py            # Segment achievements
    ├── sync_lock.py               # One sync at a time + background sync
    ├── tests.py
    ├── templates/core/            # base.html layout + one template per page
    └── management/commands/
        ├── get_strava_token.py    # Connect your Strava account — use this first
        ├── sync.py                # Everything below, in order — the everyday command
        ├── sync_activities.py     # Incremental/full sync from Strava API
        ├── backfill_best_efforts.py
        ├── fetch_achievements.py
        └── seed_from_mcp.py       # Load activities from a local JSON file
```

Full layout and design decisions: [`docs/architecture.md`](docs/architecture.md).

---

## Prerequisites

- Python 3.11+
- PostgreSQL running locally (or a remote instance)
- A Strava API application ([strava.com/settings/api](https://www.strava.com/settings/api))

---

## Setup

### 1. Clone and create a virtual environment

```bash
git clone https://github.com/SeanM04/StravaxEng.git
cd StravaXeng
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure environment variables

```bash
cp .env.example .env
```

Edit `.env` and fill in your values:

```env
SECRET_KEY=<long random string>
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1

DB_NAME=stravaxeng
DB_USER=postgres
DB_PASSWORD=<your postgres password>
DB_HOST=localhost
DB_PORT=5432

STRAVA_CLIENT_ID=<your client id>
STRAVA_CLIENT_SECRET=<your client secret>

# Optional: local time zone for dates, streaks and weekly charts (default UTC)
TIME_ZONE=Africa/Johannesburg
```

Generate a `SECRET_KEY`:

```bash
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

### 4. Create the PostgreSQL database

```sql
CREATE DATABASE stravaxeng;
```

### 5. Run migrations

```bash
python manage.py migrate
```

### 6. Create a superuser

Required to log in to the app (all views are protected with `@login_required`).

```bash
python manage.py createsuperuser
```

### 7. Authenticate with Strava

Run the OAuth2 authorization flow — this opens your browser and saves the token to the database automatically:

```bash
python manage.py get_strava_token
```

Follow the prompt: authorize the app in the browser, paste the redirect URL back into the terminal. The token is stored in the `StravaToken` table.

### 8. Sync activities

```bash
# New activities, then best efforts and achievements (the first run fetches everything)
python manage.py sync

# Re-fetch every activity
python manage.py sync --full
```

The **Sync now** button on the Pipeline page runs the same command in the background.

### 9. Start the development server

```bash
python manage.py runserver
```

Open [http://localhost:8000](http://localhost:8000). Login at [http://localhost:8000/admin/login/](http://localhost:8000/admin/login/).

### 10. (Optional) Sync every day

On Windows, register `scripts/sync_daily.cmd` with Task Scheduler — see
[the daily schedule](docs/management-commands.md#sync--daily-schedule-windows-task-scheduler).

---

## Management commands

| Command | Description |
| --- | --- |
| `get_strava_token` | Connect (or re-connect) your Strava account — opens browser, saves token to DB |
| `sync` | The everyday command: runs the three below in order (`--full` to re-fetch all) |
| `sync_activities` | Incremental sync from Strava API (`--full` to re-sync all) |
| `backfill_best_efforts` | Best efforts for runs not processed yet |
| `fetch_achievements` | Segment achievements for activities not fetched yet |
| `seed_from_mcp` | Load activities from a local JSON file |

Details: [`docs/management-commands.md`](docs/management-commands.md).

---

## Running tests

```bash
pytest                          # or: python manage.py test core
```

Tests cover the models, views and page helpers, best-effort detection, the sync pipeline
(lock, statuses, retries, incremental cutoff), Strava rate limiting and token connection.

---

## Development

```bash
pip install -r requirements-dev.txt
pylint core StravaxEng scripts        # lint, incl. Google-style docstring checks
python scripts/build_docs.py          # rebuild docs/*.html after editing docs/*.md
```

---

## Production deployment

The app is production-ready with the following in place:

- `gunicorn` WSGI server (`Procfile`)
- `whitenoise` for static file serving
- `SECURE_*` headers active when `DEBUG=False`
- `CONN_MAX_AGE=60` DB connection pooling
- `db_index=True` on `Activity.sport_type` and `Activity.start_date`

To deploy on Railway / Render / Heroku, set `DEBUG=False` and all `.env` variables as environment variables in the platform dashboard, then run:

```bash
python manage.py migrate
python manage.py collectstatic --no-input
gunicorn StravaxEng.wsgi --workers 2
```

---

## How token refresh works

Strava uses **rolling refresh tokens** — every call to `/oauth/token` returns a brand-new refresh token and invalidates the old one. `strava_client.py` reads the current token from the `StravaToken` database row, refreshes if needed, and writes the new pair back before every API call. The database is the single source of truth; `.env` only holds the app's client ID and secret.
