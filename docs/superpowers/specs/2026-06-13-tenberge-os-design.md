# TenBerge OS — Design Spec

**Date:** 2026-06-13  
**Status:** Phase 1 complete and deployed

---

## Implementation Status

| Phase | Modules | Status |
|---|---|---|
| **1** | Home shell, Markets, Calendar, Feed | ✅ Live at `os.austin10berge.com` |
| **2** | Mail, Tasks, Net Worth, Habits | Not started |
| **3** | Health, HA controls, Notes, Subscriptions | Not started |
| **4** | Media, People (CRM), Travel, Memory | Not started |

### Phase 1 — What's Built (2026-06-13/14)

**Repo:** `/home/dev/workspace/TenBerge-OS/`  
**Deployed:** `https://os.austin10berge.com` (Caddy reverse proxy → port 8090)  
**Docker:** `docker compose up -d` from repo root; stack is running

**API** (`tenberge-api`, FastAPI on port 8001 inside Docker):
- `GET /api/health` — liveness check
- `GET /api/hud` — top strip (MI posture + weather) + bottom strip (next calendar event, Gmail unread count, HA status)
- `GET /api/markets` — MI posture, composite score, LLM summary, signals, watchlist
- `GET /api/calendar/events` — upcoming Google Calendar events (top 20)
- `GET /api/feed` — MI digest item + RSS headlines (Bloomberg, Reuters, WSJ)
- `GET /api/auth/google/initiate` — starts Google OAuth2 flow
- `GET /api/auth/google/callback` — OAuth2 callback, saves token to Docker volume
- `GET /api/auth/google/status` — `{"authorized": true/false}`

**Frontend** (`tenberge-web`, SvelteKit static build served by nginx):
- Home screen: 15-app grid (3×5) with HUD top + bottom strips
- `/markets` — posture pill, score, LLM summary, signals, watchlist chips
- `/calendar` — upcoming events list, today's events highlighted teal
- `/feed` — MI digest + RSS items with serif body text
- `[module]` — "Coming Soon" fallback for all Phase 2–4 apps

**Services wired:**
- Market Intelligence API (`http://10.0.1.51:8000`) — live, no auth needed
- Google Calendar + Gmail — OAuth2 authorized (token in Docker volume `tenberge-data`)
- Home Assistant — configured in `.env` (HA_URL + HA_TOKEN)
- OpenWeather — needs real API key in `.env` (currently placeholder)

**Known data format notes from live MI API:**
- `posture` returns free-text (e.g. "Cautiously Bullish") not RISK-ON/NEUTRAL/RISK-OFF — HUD pill mapping may need adjustment
- `composite_score` is 0–1 decimal, not 0–100 integer

**Plan file:** `docs/superpowers/plans/2026-06-13-tenberge-os-phase1.md` (all 13 tasks complete)

---

## 1. Overview

TenBerge OS is a personal web platform for managing Austin's entire life from a single interface. It runs on an iPhone 12 (390px viewport), feels like a terminal workstation, and integrates deeply with Market Intelligence without replacing or modifying it. The aesthetic is inspired by the 2026 Mercedes AMG F1 workstation: near-black background, Petronas teal accent, silver data text.

---

## 2. Architecture

### Repositories & Services

**Separate repo:** `TenBerge-OS` (independent from Market Intelligence).

Two Docker services, deployed at `os.austin10berge.com`:

| Service | Stack | Role |
|---|---|---|
| `tenberge-web` | SvelteKit + Vite, served by Nginx | Frontend SPA |
| `tenberge-api` | FastAPI + SQLite | Data aggregator / proxy |

### Relationship to Market Intelligence

TenBerge OS is a **consumer** of Market Intelligence — it calls the existing MI API (`dev-mi.austin10berge.com` in dev, `market.austin10berge.com` in prod) for market posture, composite score, signals, and watchlist data. It never modifies MI's codebase, database, or Docker stack.

### Frontend Architecture

- **SvelteKit** with file-based routing: each module maps to a route (`/markets`, `/calendar`, `/habits`, etc.)
- **Svelte stores** drive the live HUD strips globally — all views share the same reactive top/bottom bar state
- No build-time framework runtime overhead — Svelte compiles to direct DOM manipulation
- Single-page app; navigating between modules does not reload the shell

### Backend Architecture

FastAPI aggregator backend (`tenberge-api`) acts as a unified data gateway:

| Data source | Integration method |
|---|---|
| Market Intelligence | HTTP calls to MI REST API |
| Google Calendar | OAuth2 → Google Calendar API |
| Gmail | OAuth2 → Gmail API |
| Home Assistant | Local HTTP API (same LAN) |
| Obsidian vault | Read `.md` files from a git-synced or shared-mount vault path |
| Weather | OpenWeatherMap or similar free API |
| Owned data (habits, tasks, net worth, subscriptions, CRM, media queue, travel, memory log) | SQLite managed by `tenberge-api` |

OAuth2 tokens for Google services are stored server-side in `tenberge-api` — never exposed to the frontend.

---

## 3. Visual Design

### Color Palette (Mercedes AMG F1 2026)

| Role | Color | Hex |
|---|---|---|
| Background | Mercedes near-black | `#080804` |
| Primary accent | Petronas teal | `#00D7B6` |
| Data / primary text | Silver Arrow silver | `#C8CCCE` |
| Muted / secondary | Dark graphite | `#565F64` |

No gradients. No drop shadows. No rounded pill buttons. Sharp, dense, readable.

### Typography

| Use | Font | Notes |
|---|---|---|
| HUD strips, data labels, all monospaced output | Geist Mono (free, Vercel) | All uppercase labels, numbers, system readouts |
| Prose content (Notes, Feed articles, Mail body, Memory entries) | Source Serif 4 (free, Google Fonts) | Body reading content only |

### Aesthetic Principles

- Dark-first; no light mode in Phase 1
- Petronas teal used sparingly: active states, selected items, live indicator dots, accent lines
- Silver (`#C8CCCE`) for primary readable text
- Graphite (`#565F64`) for muted labels, borders, inactive items
- Subtle horizontal rule separators; no heavy card borders
- Terminal-style density: information-rich, not airy

---

## 4. Home Screen

The home screen is the OS shell — always visible as the "launcher." Every other view is a module opened from it.

### Layout (zero vertical scroll on iPhone 12)

```
┌─────────────────────────────┐
│ TOP HUD STRIP          ~48px │
│ [time]  [date]  [MI posture] │
│ [MI score]  [weather temp]   │
├──────────────────────────────┤
│                              │
│  [MARKETS] [CALENDAR] [FEED] │
│                              │
│  [TASKS]   [MAIL]   [HEALTH] │
│                              │
│  [HA]      [NOTES]  [WEALTH] │  3×5 icon grid
│                              │
│  [HABITS]  [MEDIA]  [PEOPLE] │
│                              │
│  [SUBS]   [TRAVEL] [MEMORY]  │
│                              │
├──────────────────────────────┤
│ BOTTOM HUD STRIP       ~48px │
│ NEXT: [event name] ▸ [ETA]   │
│ STREAK: [n]d  MAIL: [n]  HA: [status] │
└──────────────────────────────┘
```

### Top HUD Strip Content

- Current time (Geist Mono, large)
- Current date (abbreviated: `SAT 13 JUN`)
- MI market posture pill (`RISK-OFF` / `NEUTRAL` / `RISK-ON`)
- MI composite score (0–100)
- Current temperature + conditions (weather API)

### Bottom HUD Strip Content

- Next Google Calendar event + countdown to start time
- Current active habit streak (days)
- Unread Gmail count
- Home Assistant home status (`HOME: OK` or alert)

### App Icon Grid

- 3 columns × 5 rows = 15 apps, no scroll required on iPhone 12 (390px)
- Each tile: terminal-style SVG icon + uppercase Geist Mono label
- Teal glow ring on tap/active state
- Grid gap and tile sizing calibrated so all 15 fit between the two HUD strips

---

## 5. Modules

### Phase 1 (Core Shell)

| Module | Key integrations | Primary data |
|---|---|---|
| **Home** | MI API, Google Calendar, Gmail, HA, Weather | HUD strips + app grid |
| **Markets** | MI API | Posture, signals, composite score, watchlist, CSP scanner link |
| **Calendar** | Google Calendar API | Month/week view, event detail, create event |
| **Feed** | MI signals, configurable RSS/news sources | Market signals + general news stream |

### Phase 2

| Module | Key integrations | Primary data |
|---|---|---|
| **Mail** | Gmail API | Inbox threads, read/draft, send |
| **Tasks** | SQLite | To-dos, projects, due dates |
| **Net Worth** | SQLite (manual entries + MI for portfolio value) | Total NW, asset breakdown |
| **Habits** | SQLite | Daily habit list, streak counts, calendar view |

### Phase 3

| Module | Key integrations | Primary data |
|---|---|---|
| **Health** | SQLite (manual + future wearable API) | Workouts, sleep, nutrition log |
| **HA** | Home Assistant local API | Device controls, automations, sensor readouts |
| **Notes** | Obsidian vault (`.md` file reads via `tenberge-api`) | Browse, read, and search Obsidian notes |
| **Subscriptions** | SQLite | Monthly subscription list, total monthly cost |

### Phase 4

| Module | Key integrations | Primary data |
|---|---|---|
| **Media** | SQLite | Books reading, shows watching, YouTube watch queue |
| **People** (CRM) | SQLite | Key contacts, last contacted, relationship notes |
| **Travel** | SQLite | Upcoming trips, itineraries, packing lists |
| **Memory** | SQLite | Places visited, restaurants, events — a personal log |

---

## 6. Navigation Model

- All navigation originates from the home screen icon grid
- Each module opens full-screen (no sidebar, no persistent nav except the HUD strips)
- Back arrow (top-left, Geist Mono `←`) returns to home screen
- HUD strips remain visible in all module views
- No bottom tab bar (unlike MI v2) — the home screen IS the navigation

---

## 7. Mobile Constraints (iPhone 12)

- Viewport: 390px × 844px (logical pixels)
- Touch targets: minimum 44×44px per Apple HIG
- No horizontal scroll; vertical scroll permitted within modules (not on home screen)
- Font sizes: minimum 11px for label text, 14px for readable body, 20px+ for HUD primary values
- Safe area insets respected (notch top, home indicator bottom)

---

## 8. Build Phases

| Phase | Modules | Goal |
|---|---|---|
| 1 | Home screen shell, Markets, Calendar, Feed | Deployable foundation with daily-use value |
| 2 | Mail, Tasks, Net Worth, Habits | Communication and personal tracking |
| 3 | Health, HA, Notes, Subscriptions | Life management completeness |
| 4 | Media, People, Travel, Memory | Rich personal history and context |

Each phase is independently deployable. Phase 1 alone is useful every day.

---

## 9. Out of Scope

- Light mode (dark only for v1)
- Focus / Pomodoro timer
- Native mobile app (web-only)
- Modification of Market Intelligence codebase
- Production MI deployment (dev only until explicitly asked)
