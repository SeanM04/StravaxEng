# Architecture

## Directory structure

```text
StravaXeng/                              ← repository root
├── .env                                 ← credentials and config (never commit)
├── .env.example                         ← template for .env
├── .gitignore
├── .pylintrc                            ← pylint + Google docstring rules
├── manage.py                            ← Django CLI entry point
├── requirements.txt
├── requirements-dev.txt                 ← adds pylint and the docs builder
├── Procfile                             ← gunicorn entry point
├── mcp_activities.json                  ← MCP-exported activity seed data (not committed)
├── sync.log                             ← output of background and scheduled syncs (not committed)
│
├── .claude/                             ← Claude Code settings: pylint hook on edited files
├── scripts/
│   ├── sync_daily.cmd                   ← run daily by Windows Task Scheduler
│   └── build_docs.py                    ← builds docs/*.html from docs/*.md
│
├── StravaxEng/                          ← Django project config package
│   ├── settings.py                      ← all settings, reads from .env
│   ├── urls.py                          ← root URL conf (includes core.urls)
│   ├── wsgi.py
│   └── asgi.py
│
├── core/                                ← main Django app
│   ├── models.py                        ← StravaToken, SyncLog, Activity, Achievement, BestEffort
│   ├── views.py                         ← page views, Sync now, health check
│   ├── urls.py                          ← URL routes (see dashboard.md)
│   ├── admin.py                         ← admin registrations
│   ├── strava_client.py                 ← OAuth2 token renewal, API requests, rate limits
│   ├── best_effort.py                   ← sliding-window best efforts from GPS streams
│   ├── achievements.py                  ← segment achievements from activity details
│   ├── sync_lock.py                     ← one-sync-at-a-time lock + background sync
│   ├── tests.py
│   ├── migrations/                      ← 0001–0010, see models.md
│   ├── templates/core/
│   │   ├── base.html                    ← master layout (sidebar, topbar, CSS)
│   │   ├── dashboard.html
│   │   ├── activities.html
│   │   ├── activity_detail.html
│   │   ├── analytics.html
│   │   ├── records.html
│   │   ├── coach.html
│   │   ├── pipeline.html
│   │   └── settings_page.html
│   └── management/
│       ├── activity_stage.py            ← shared base for the per-activity commands
│       └── commands/
│           ├── sync.py                  ← the whole pipeline: the three commands below
│           ├── sync_activities.py       ← ETL: Strava API → PostgreSQL
│           ├── backfill_best_efforts.py ← best efforts still to compute
│           ├── fetch_achievements.py    ← segment achievements still to fetch
│           ├── get_strava_token.py      ← connect Strava (OAuth) → saves to DB
│           └── seed_from_mcp.py         ← seeds DB from MCP JSON export
│
└── docs/                                ← this folder (edit the .md; the .html is generated)
```

## Design decisions

### Rolling refresh tokens stored in the DB

Strava uses **rolling refresh tokens**: every call to `/oauth/token` returns a brand-new
refresh token and invalidates the previous one. Storing only the refresh token in `.env`
loses the rotated value on the first exchange.

`StravaToken` (a single-row DB table) is the source of truth for the token pair.
`strava_client.get_access_token()` reads the row, refreshes if expired, and writes
the new token pair back — so rotation is never lost.

See [`strava-api.md`](strava-api.md) for the full OAuth flow.

### SyncLog audit trail

Every run of `sync_activities` writes a `SyncLog` row that records start/end time,
status (`running` / `success` / `partial` / `failed`), counts of created/updated rows,
pages fetched, and any error message. The Pipeline Health page reads this table.

The incremental sync's `?after=` cutoff is the start time of the newest stored activity minus 7 days (`INCREMENTAL_OVERLAP`). Strava's `?after=` filters on when an activity *started*, so this catches activities uploaded late (e.g. a watch that syncs days afterwards); the re-fetched activities are simply upserted, usually costing one page request.

### Template inheritance

All pages extend `core/templates/core/base.html`. The base template contains:

- All shared CSS (custom properties, sidebar, topbar, stat cards, charts, badges, tables)
- The sidebar navigation with active-state logic via `active_page` context variable
- Chart.js 4.4 CDN script tag
- `{% block content %}` for page-specific content

### `raw_data` JSON field

The full Strava API response is stored in `Activity.raw_data`. This allows querying
new fields from already-synced data without a re-sync or migration.

### No Celery

Syncing is a management command, `manage.py sync`, started three ways: by Windows Task
Scheduler once a day, by the **Sync now** button (as a detached background process), or by
hand. A lock file stops runs overlapping. A task queue (Celery + Redis) would only be worth
adding if the app were hosted rather than run on one PC.

### Single `core` app

All logic lives in one app. If the project grows (athlete profile, gear, segments),
additional apps can be split out.
