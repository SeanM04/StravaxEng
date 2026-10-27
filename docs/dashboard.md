# UI — Pages & Templates

StravaXEng is a **multi-page Django app** with a fixed dark sidebar and a Strava-orange topbar.
All pages extend a shared `base.html` master layout.

## Base layout

File: [`core/templates/core/base.html`](../core/templates/core/base.html)

```
┌─────────────────────────────────────────────────┐
│  Topbar (orange, sticky, 56 px)                 │
├──────────────┬──────────────────────────────────┤
│              │                                  │
│   Sidebar    │   .page-body (scrollable)        │
│   240 px     │   {% block content %}            │
│   dark       │                                  │
│   (#111827)  │                                  │
│              │                                  │
└──────────────┴──────────────────────────────────┘
```

Active sidebar link is highlighted with an orange left border. The current page is
identified by the `active_page` context variable passed from each view.

Chart.js 4.4 is loaded from CDN in `<head>` and available on every page.

## Pages

### Dashboard — `/`

View: `dashboard`  
Template: `core/templates/core/dashboard.html`

- 6 stat cards: Total Activities, Total Distance (km), Moving Time (hrs), Elevation Gain (m), Avg Cadence (spm), Avg Heart Rate (bpm)
- Chart.js line chart of run distance over time
- Table of the 8 most recent activities (all sport types)

### Activities — `/activities/`

View: `activities_list`  
Template: `core/templates/core/activities.html`

- Full paginated table of all activities (25 per page, Django `Paginator`)
- Filter by sport type (dropdown) and name search (text input)
- Sortable columns: Date, Distance, Elevation, Kudos, Cadence — toggling the same column reverses order
- Sort state and filters are preserved across pagination via query parameters

### Analytics — `/analytics/`

View: `analytics`  
Template: `core/templates/core/analytics.html`

- Weekly volume bar chart (km per ISO week, all sport types)
- Weekly activity count bar chart (same X-axis)
- Pace trend line chart for runs only (min/km, Y-axis inverted so faster = higher)
- Summary stat cards: total run count and total run distance

### Records & Streaks — `/records/`

View: `records`  
Template: `core/templates/core/records.html`

**Activity Streaks** (daily, consecutive calendar days with ≥ 1 activity):
- Current Streak — counts backwards from today (or yesterday if no activity today)
- Longest Streak

**Weekly Streaks** (ISO Mon–Sun weeks with ≥ 1 activity):
- Current Weekly Streak — counts backwards from the current ISO week
- Longest Weekly Streak
- Computed via `date.isocalendar()[:2]` and `date.fromisocalendar()` to handle year boundaries correctly

**Personal Records:**
- Longest Run (km)
- Fastest Pace for runs ≥ 3 km (min/km)
- Highest Elevation in a single run (m)
- Most Kudos in a single activity

**Sport Breakdown** table — activity count and total distance per sport type.

### AI Coach Notes — `/coach/`

View: `coach`  
Template: `core/templates/core/coach.html`

Rule-based insights computed from DB data:
- Average weekly running volume (km)
- Pace trend — compares average pace (shown as M:SS /km) of the last 10 runs vs the previous
  10. Changes under 2 % (`PACE_TREND_THRESHOLD`, ≈ 7 s/km at 6:00/km) are reported as
  **steady** rather than improving/declining, since they are day-to-day noise
- Long-run habit analysis (runs ≥ 15 km)
- Heart rate card — Huawei Health sync instructions while no run has heart rate data
- Table of the 5 most recent runs

### Pipeline Health — `/pipeline/`

View: `pipeline_health`  
Template: `core/templates/core/pipeline.html`

- **Sync now** button (runs `manage.py sync` in the background)
- Health stat cards: total activities, total runs, total syncs, success rate %, last successful sync timestamp, items waiting for the next sync, Strava connection
- Table of the 20 most recent `SyncLog` entries with status, type (incremental/full), created/updated counts, and error messages
- Quick-reference command list

### Settings — `/settings/`

View: `settings_view`  
Template: `core/templates/core/settings_page.html`

- Strava connection: **Connected**, **Re-connection needed** or **Not connected**, with
  step-by-step `manage.py get_strava_token` instructions when action is needed. An expired
  *access* token is not shown as a problem — it lasts 6 hours and renews automatically on the
  next sync; re-connecting is only asked for when the latest sync failed because the refresh
  token was revoked (and no new token has been saved since). Token values are never shown.
- App configuration reference (athlete, watch, time zone, stack)

## Local time

All pages show dates and times in `TIME_ZONE` (set in `.env`, e.g. `Africa/Johannesburg`;
default UTC). Streaks, weekly charts and best-effort dates use the local calendar day, so a run
at 01:30 local time counts for that day, not the previous UTC day. The database stores UTC.

## Phones

Below 768 px wide the sidebar becomes a sticky, horizontally scrolling navigation bar across
the top and the page body uses the full width; wide tables scroll sideways.

## URL routes

Defined in [`core/urls.py`](../core/urls.py):

| URL | View | Name |
|-----|------|------|
| `/` | `dashboard` | `dashboard` |
| `/activities/` | `activities_list` | `activities` |
| `/analytics/` | `analytics` | `analytics` |
| `/records/` | `records` | `records` |
| `/coach/` | `coach` | `coach` |
| `/pipeline/` | `pipeline_health` | `pipeline` |
| `/settings/` | `settings_view` | `settings` |

## Shared CSS classes (defined in `base.html`)

| Class | Purpose |
|-------|---------|
| `.stat-grid` / `.stat-card` | Summary metric cards |
| `.card` / `.card-title` | White content panels |
| `.streak-grid` / `.streak-card` | Dark gradient streak cards |
| `.record-grid` / `.record-card` | PR cards with orange top border |
| `.insight-grid` / `.insight-card` | Coach insight cards with orange left border |
| `.badge-run`, `.badge-ride`, … | Sport type colour badges |
| `.chart-wrap` | Fixed-height canvas wrapper (260 px) |
| `.filter-bar` | Inline form row for filters |
| `.pagination` | Numbered page links |
