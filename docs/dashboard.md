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
- Pace trend — compares average pace of last 10 runs vs previous 10
- Long-run habit analysis (runs ≥ 15 km)
- Heart rate data note (HUAWEI Watch GT 5 HR sync status)
- Table of the 5 most recent runs

### Pipeline Health — `/pipeline/`

View: `pipeline_health`  
Template: `core/templates/core/pipeline.html`

- Health stat cards: total activities, total runs, total syncs, success rate %, last successful sync timestamp, token status
- Table of the 20 most recent `SyncLog` entries with status, type (incremental/full), created/updated counts, and error messages
- Quick-reference command list

### Settings — `/settings/`

View: `settings_view`  
Template: `core/templates/core/settings_page.html`

- Strava token status (valid / expired / missing) with masked token values
- App configuration reference (athlete, watch, stack)

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
