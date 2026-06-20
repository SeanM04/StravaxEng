# StravaXEng Documentation

A Django + PostgreSQL ETL and visualization app for Strava activity data.

## Contents

| File | Description |
|------|-------------|
| [setup.md](setup.md) | Environment setup, installation, and first-run steps |
| [architecture.md](architecture.md) | Project structure, directory layout, and design decisions |
| [strava-api.md](strava-api.md) | Strava OAuth2 flow, rolling refresh token design, and API usage |
| [models.md](models.md) | All three database models and their field reference |
| [dashboard.md](dashboard.md) | Multi-page UI — all 7 pages, layouts, charts, and streaks |
| [management-commands.md](management-commands.md) | All `manage.py` commands and their arguments |

## Quick start

```bash
# 1. Install dependencies
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt

# 2. Configure environment
cp .env.example .env   # fill in your values

# 3. Apply migrations
python manage.py migrate

# 4. Obtain a fresh Strava token (opens browser)
python manage.py get_strava_token

# 5. Seed your activity data
python manage.py sync_activities

# 6. Start the development server
python manage.py runserver
```

## Conventions

- All documentation is written in Markdown with matching `.html` companions.
- Code blocks use the appropriate language tag.
- Each file documents one concern — cross-reference by relative link.
- Update the relevant doc in the same commit as any feature change.
