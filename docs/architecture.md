# Architecture

## Directory structure

```text
StravaXeng/                              ← repository root
├── .env                                 ← credentials and config (never commit)
├── .gitignore
├── manage.py                            ← Django CLI entry point
├── requirements.txt
├── get_strava_token.py                  ← standalone OAuth helper (fallback)
├── mcp_activities.json                  ← MCP-exported activity seed data
│
├── StravaxEng/                          ← Django project config package
│   ├── settings.py                      ← all settings, reads from .env
│   ├── urls.py                          ← root URL conf (includes core.urls)
│   ├── wsgi.py
│   └── asgi.py
│
├── core/                                ← main Django app
│   ├── models.py                        ← StravaToken, SyncLog, Activity
│   ├── views.py                         ← 7 view functions
│   ├── urls.py                          ← 7 URL routes
│   ├── admin.py                         ← admin registrations
│   ├── strava_client.py                 ← OAuth2 token management + API helpers
│   ├── migrations/
│   │   ├── 0001_initial.py              ← Activity table
│   │   ├── 0002_stravatoken_synclog.py  ← StravaToken + SyncLog tables
│   │   └── 0003_activity_average_cadence.py
│   ├── templates/core/
│   │   ├── base.html                    ← master layout (sidebar, topbar, CSS)
│   │   ├── dashboard.html
│   │   ├── activities.html
│   │   ├── analytics.html
│   │   ├── records.html
│   │   ├── coach.html
│   │   ├── pipeline.html
│   │   └── settings_page.html
│   └── management/commands/
│       ├── get_strava_token.py          ← full OAuth flow → saves to DB
│       ├── bootstrap_token.py           ← seeds DB from .env refresh token
│       ├── sync_activities.py           ← ETL: Strava API → PostgreSQL
│       └── seed_from_mcp.py             ← seeds DB from MCP JSON export
│
└── docs/                                ← this folder
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

### No Celery (yet)

Syncing is triggered manually via `manage.py sync_activities`. A task queue
(Celery + Redis) would be added if scheduled/background syncing is needed.

### Single `core` app

All logic lives in one app. If the project grows (athlete profile, gear, segments),
additional apps can be split out.
