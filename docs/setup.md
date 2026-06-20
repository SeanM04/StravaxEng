# Setup

## Prerequisites

- Python 3.11+
- PostgreSQL (local or remote)
- A Strava API application — see [strava-api.md](strava-api.md)

## Steps

### 1. Clone the repository

```bash
git clone <repo-url>
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
```

### 4. Configure environment variables

Edit `.env` in the project root. Required variables:

```env
# Strava
STRAVA_CLIENT_ID=<your client id>
STRAVA_CLIENT_SECRET=<your client secret>
STRAVA_REFRESH_TOKEN=<your refresh token>

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

### 7. (Optional) Create an admin superuser

```bash
python manage.py createsuperuser
```

### 8. Sync activities from Strava

```bash
python manage.py sync_activities --pages 3
```

### 9. Start the development server

```bash
python manage.py runserver
```

- Dashboard: http://localhost:8000
- Admin panel: http://localhost:8000/admin
