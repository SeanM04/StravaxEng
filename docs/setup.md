# Setup

Everything you need to get StravaXEng running locally from scratch.

## Prerequisites

- Python 3.11+
- PostgreSQL (local or remote)
- A Strava API application — see [Strava API](strava-api.md)

## Steps

### 1. Clone the repository

```bash
git clone https://github.com/SeanM04/StravaxEng.git
cd StravaXeng
```

### 2. Create and activate the virtual environment

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt

# For development (adds pylint and the docs builder)
pip install -r requirements-dev.txt
```

### 4. Configure environment variables

Copy `.env.example` to `.env` in the project root and fill in your values:

```env
# Strava app (strava.com/settings/api). Tokens are not kept here — see step 8.
STRAVA_CLIENT_ID=<your client id>
STRAVA_CLIENT_SECRET=<your client secret>

# Django
SECRET_KEY=<long random string>
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1

# PostgreSQL
DB_NAME=stravaxeng
DB_USER=postgres
DB_PASSWORD=<your password>
DB_HOST=localhost
DB_PORT=5432

# Optional: your local time zone (IANA name; default UTC). Dates, streaks and
# weekly charts are shown and grouped in it.
TIME_ZONE=Africa/Johannesburg
```

Generate a `SECRET_KEY`:

```bash
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

### 5. Create the PostgreSQL database

```sql
CREATE DATABASE stravaxeng;
```

### 6. Run migrations

```bash
python manage.py migrate
```

### 7. Create a login

Every page requires a logged-in user:

```bash
python manage.py createsuperuser
```

### 8. Connect your Strava account

```bash
python manage.py get_strava_token
```

Your browser opens Strava's authorization page. Click **Authorize**, then copy the
`http://localhost/?code=…` URL from the address bar (the page itself won't load — that's
expected) and paste it into the terminal. The token is saved to the database. See
[`get_strava_token`](management-commands.md#get_strava_token).

### 9. Sync your activities

```bash
python manage.py sync
```

The first sync fetches your whole history, then best efforts and achievements; later syncs
only fetch what's new. See [`sync`](management-commands.md#sync).

### 10. Start the development server

```bash
python manage.py runserver
```

- Dashboard: <http://localhost:8000> — log in with the user from step 7
- Admin panel: <http://localhost:8000/admin>

### 11. (Optional) Sync every day

On Windows, register [`scripts/sync_daily.cmd`](../scripts/sync_daily.cmd) with Task
Scheduler — see
[daily schedule](management-commands.md#sync--daily-schedule-windows-task-scheduler).
