# StravaXEng

A Django application that ingests Strava activity data via the Strava API and displays it on a dashboard.

## Project structure

```
StravaXeng/
├── .env                          # Credentials and config (never commit this)
├── requirements.txt
├── manage.py
├── StravaXEng/                   # Django project config
│   ├── settings.py
│   ├── urls.py
│   ├── wsgi.py
│   └── asgi.py
└── core/                         # Main app
    ├── models.py                 # Activity model
    ├── views.py                  # Dashboard view
    ├── urls.py
    ├── admin.py
    ├── strava_client.py          # OAuth2 token refresh + API helpers
    ├── templates/core/
    │   └── dashboard.html
    └── management/commands/
        └── sync_activities.py    # Management command to pull from Strava
```

## Prerequisites

- Python 3.11+
- PostgreSQL running locally (or a remote instance)

## Setup

### 1. Clone and create a virtual environment

```bash
git clone <repo-url>
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

Copy `.env` and fill in your values:

```
STRAVA_CLIENT_ID=<your client id>
STRAVA_CLIENT_SECRET=<your client secret>
STRAVA_REFRESH_TOKEN=<your refresh token>

SECRET_KEY=<long random string>
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1

DB_NAME=stravaxeng
DB_USER=postgres
DB_PASSWORD=<your postgres password>
DB_HOST=localhost
DB_PORT=5432
```

To get a `SECRET_KEY` quickly:

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

### 6. Create a superuser (optional, for the admin panel)

```bash
python manage.py createsuperuser
```

### 7. Sync activities from Strava

```bash
# Fetch one page (30 activities)
python manage.py sync_activities

# Fetch multiple pages
python manage.py sync_activities --pages 5
```

### 8. Start the development server

```bash
python manage.py runserver
```

Open [http://localhost:8000](http://localhost:8000) to see the dashboard.  
The admin panel is at [http://localhost:8000/admin](http://localhost:8000/admin).

## Getting Strava API credentials

1. Go to [strava.com/settings/api](https://www.strava.com/settings/api) and create an application.
2. Note your **Client ID** and **Client Secret**.
3. Use the [Strava OAuth flow](https://developers.strava.com/docs/authentication/) to obtain a **Refresh Token** with the `activity:read_all` scope.

## How token refresh works

`core/strava_client.py` calls `POST /oauth/token` with your refresh token before every API request. Strava access tokens expire after 6 hours, so this ensures every call is authenticated without manual intervention. The new access token is used in-memory only; the refresh token in `.env` remains valid until revoked.
