# StravaXEng Documentation

A Django + PostgreSQL ETL and visualization app for Strava activity data.

## Contents

| Page | Description |
| --- | --- |
| [Setup](setup.md) | Environment setup, installation, and first-run steps |
| [Architecture](architecture.md) | Project structure, directory layout, and design decisions |
| [Strava API](strava-api.md) | Strava OAuth2 flow, rolling refresh token design, rate limits |
| [Models](models.md) | All five database models and their field reference |
| [Dashboard](dashboard.md) | Multi-page UI — every page, layouts, charts, and streaks |
| [Management Commands](management-commands.md) | All `manage.py` commands, the daily schedule, and their arguments |
| [Best-Effort Detection](best-effort.md) | Sliding-window best-effort detection algorithm — design, complexity, and worked example |

## Quick start

```bash
# 1. Install dependencies
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt

# 2. Configure environment
cp .env.example .env   # fill in your values

# 3. Apply migrations and create a login
python manage.py migrate
python manage.py createsuperuser

# 4. Connect your Strava account (opens browser)
python manage.py get_strava_token

# 5. Pull your activities, best efforts and achievements
python manage.py sync

# 6. Start the development server
python manage.py runserver
```

## Conventions

- Docs are written in Markdown. The `.html` pages are generated from it — edit the `.md`,
  then run `python scripts/build_docs.py`; never edit the `.html` by hand.
- Code blocks use the appropriate language tag.
- Each file documents one concern — cross-reference by relative link.
- Update the relevant doc in the same commit as any feature change.
