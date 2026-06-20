# StravaXEng

A Django + PostgreSQL ETL and visualization app for Strava activity data. Syncs activities via the Strava API and presents them across seven analytics pages with Chart.js charts, personal records, streak tracking, and an AI coach summary.

---

## Pages

| Page | URL | Description |
| --- | --- | --- |
| Dashboard | `/` | Stat cards + run distance chart |
| Activities | `/activities/` | Sortable, filterable paginated table |
| Analytics | `/analytics/` | Weekly volume bar chart + pace trend line |
| Records & Streaks | `/records/` | PRs, daily/weekly activity streaks, sport breakdown |
| AI Coach Notes | `/coach/` | Avg weekly km, pace trend, long-run count |
| Pipeline Health | `/pipeline/` | Sync log history + success rate |
| Settings | `/settings/` | Token status |

All pages require a logged-in Django user (`/admin/login/`).

---

## Project structure

```
StravaXeng/
├── .env                           # Credentials (never commit — see .env.example)
├── .env.example                   # Template with placeholder values
├── requirements.txt
├── Procfile                       # gunicorn entry point for production
├── pytest.ini
├── manage.py
├── StravaxEng/                    # Django project config
│   ├── settings.py
│   ├── urls.py
│   └── wsgi.py
└── core/                          # Main app
    ├── models.py                  # StravaToken, SyncLog, Activity
    ├── views.py                   # 7 view functions + helper utilities
    ├── urls.py
    ├── admin.py
    ├── strava_client.py           # OAuth2 token refresh + Strava API helpers
    ├── tests.py                   # pytest-django unit tests
    ├── templates/core/
    │   ├── base.html              # Sidebar + topbar master layout
    │   ├── dashboard.html
    │   ├── activities.html
    │   ├── analytics.html
    │   ├── records.html
    │   ├── coach.html
    │   ├── pipeline.html
    │   └── settings_page.html
    └── management/commands/
        ├── get_strava_token.py    # Full OAuth2 flow — use this first
        ├── bootstrap_token.py     # Legacy: exchange refresh token from .env
        ├── sync_activities.py     # Incremental/full sync from Strava API
        └── seed_from_mcp.py       # Load activities from a local JSON file
```

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

```
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
# Incremental sync (only new activities since last run)
python manage.py sync_activities

# Full sync (all pages)
python manage.py sync_activities --full

# Control page size
python manage.py sync_activities --per-page 100
```

### 9. Start the development server

```bash
python manage.py runserver
```

Open [http://localhost:8000](http://localhost:8000). Login at [http://localhost:8000/admin/login/](http://localhost:8000/admin/login/).

---

## Management commands

| Command | Description |
| --- | --- |
| `get_strava_token` | Full OAuth2 flow — opens browser, saves token to DB |
| `bootstrap_token` | Exchange the refresh token in `.env` for a live token (legacy) |
| `sync_activities` | Incremental sync from Strava API (`--full` to re-sync all) |
| `seed_from_mcp` | Load activities from a local JSON file |

---

## Running tests

```bash
pytest
```

Tests cover Activity model properties, daily/weekly streak helpers, and the pace calculation utility.

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

Strava uses **rolling refresh tokens** — every call to `/oauth/token` returns a brand-new refresh token and invalidates the old one. `strava_client.py` reads the current token from the `StravaToken` database row, refreshes if needed, and writes the new pair back before every API call. The database is the single source of truth; `.env` only provides the initial credential.
