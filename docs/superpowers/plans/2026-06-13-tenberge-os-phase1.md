# TenBerge OS — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and deploy Phase 1 of TenBerge OS — a new standalone platform at `os.austin10berge.com` with a terminal-aesthetic home screen (top+bottom HUD strips + 15-app icon grid) and three live modules: Markets (MI integration), Calendar (Google Calendar OAuth2), and Feed (MI signals + RSS news).

**Architecture:** New repo `TenBerge-OS` at `/home/dev/workspace/TenBerge-OS/`. Two Docker services: `tenberge-web` (SvelteKit compiled to static files by nginx; all routes serve `index.html` for SPA routing) and `tenberge-api` (FastAPI aggregator on port 8001, proxied via nginx under `/api/*`). SvelteKit uses a root layout that mounts persistent HUD strips; a Svelte writable store polls `/api/hud` every 60 seconds to drive them. Google OAuth2 tokens are stored server-side in a Docker volume; the frontend never handles tokens.

**Tech Stack:** SvelteKit 2.x + `@sveltejs/adapter-static` + Vite (frontend), FastAPI + uvicorn (backend), httpx (async HTTP), google-auth-oauthlib (OAuth2), Python 3.12, Node 20, nginx:alpine, Docker Compose v2, pytest + httpx for API tests, vitest + @testing-library/svelte for component tests.

**Spec:** `docs/superpowers/specs/2026-06-13-tenberge-os-design.md`

---

## File Map

```
/home/dev/workspace/TenBerge-OS/
├── docker-compose.yml
├── .env.example
│
├── api/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── main.py                   # FastAPI app, CORS, router mount
│   ├── config.py                 # Pydantic Settings (reads .env)
│   ├── routers/
│   │   ├── hud.py                # GET /api/hud  (top + bottom strip data)
│   │   ├── markets.py            # GET /api/markets (MI proxy)
│   │   ├── calendar.py           # GET /api/calendar/events
│   │   ├── feed.py               # GET /api/feed
│   │   └── auth.py               # GET /api/auth/google/initiate + /callback
│   ├── services/
│   │   ├── mi.py                 # async MI API client
│   │   ├── weather.py            # async OpenWeatherMap client
│   │   ├── google_auth.py        # OAuth2 token load/save/refresh
│   │   ├── google_calendar.py    # Google Calendar API calls
│   │   ├── google_gmail.py       # Gmail unread count
│   │   ├── home_assistant.py     # HA local HTTP API
│   │   └── rss.py                # RSS feed fetcher
│   └── tests/
│       ├── conftest.py
│       ├── test_hud.py
│       ├── test_markets.py
│       ├── test_calendar.py
│       └── test_feed.py
│
└── web/
    ├── Dockerfile
    ├── nginx.conf
    ├── package.json
    ├── svelte.config.js
    ├── vite.config.js
    ├── vitest.config.js
    └── src/
        ├── app.html              # root HTML, Google Fonts links
        ├── app.css               # design system: CSS vars, typography, resets
        ├── lib/
        │   ├── stores/
        │   │   └── hud.js        # writable store + startHudPolling()
        │   ├── api.js            # fetch wrapper (relative /api/* calls)
        │   ├── apps.js           # APPS constant (15 module definitions)
        │   └── components/
        │       ├── HudTop.svelte
        │       ├── HudBottom.svelte
        │       └── AppTile.svelte
        └── routes/
            ├── +layout.svelte    # mounts HudTop + HudBottom, starts polling
            ├── +layout.js        # export ssr = false, prerender = false
            ├── +page.svelte      # home screen: AppGrid (3×5 tiles)
            ├── markets/
            │   └── +page.svelte
            ├── calendar/
            │   └── +page.svelte
            ├── feed/
            │   └── +page.svelte
            └── [module]/
                └── +page.svelte  # "Coming Soon" fallback for Phase 2-4 apps
```

---

## Task 1: Repo Scaffold + Docker Compose + nginx

**Files:**
- Create: `/home/dev/workspace/TenBerge-OS/docker-compose.yml`
- Create: `/home/dev/workspace/TenBerge-OS/.env.example`
- Create: `/home/dev/workspace/TenBerge-OS/api/Dockerfile`
- Create: `/home/dev/workspace/TenBerge-OS/web/Dockerfile`
- Create: `/home/dev/workspace/TenBerge-OS/web/nginx.conf`

- [ ] **Step 1: Create the repo directory and git init**

```bash
mkdir -p /home/dev/workspace/TenBerge-OS
cd /home/dev/workspace/TenBerge-OS
git init
echo ".env" > .gitignore
echo "node_modules/" >> .gitignore
echo "web/build/" >> .gitignore
echo "__pycache__/" >> .gitignore
echo "*.pyc" >> .gitignore
```

- [ ] **Step 2: Write docker-compose.yml**

```yaml
# docker-compose.yml
services:
  tenberge-api:
    build: ./api
    env_file: .env
    volumes:
      - tenberge-data:/app/data
    restart: unless-stopped
    networks:
      - internal

  tenberge-web:
    build: ./web
    ports:
      - "8090:80"
    depends_on:
      - tenberge-api
    restart: unless-stopped
    networks:
      - internal

volumes:
  tenberge-data:

networks:
  internal:
```

Port 8090 avoids collision with MI's dashboard on 8080.

- [ ] **Step 3: Write .env.example**

```bash
# .env.example — copy to .env and fill in
MI_API_URL=http://10.0.1.51:8000
HA_URL=http://10.0.1.X:8123
HA_TOKEN=your_long_lived_ha_token
OPENWEATHER_API_KEY=your_key
OPENWEATHER_LAT=44.9778
OPENWEATHER_LON=-93.2650
GOOGLE_CLIENT_ID=your_client_id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your_client_secret
GOOGLE_REDIRECT_URI=https://os.austin10berge.com/api/auth/google/callback
TOKEN_PATH=/app/data/google_tokens.json
DB_PATH=/app/data/tenberge.db
```

- [ ] **Step 4: Write api/Dockerfile**

```dockerfile
FROM python:3.12-slim
WORKDIR /app
RUN mkdir -p /app/data
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "1"]
```

- [ ] **Step 5: Write web/nginx.conf**

```nginx
server {
    listen 80;
    root /usr/share/nginx/html;
    index index.html;

    location /api/ {
        proxy_pass http://tenberge-api:8001/api/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 30s;
    }

    location / {
        try_files $uri $uri/ /index.html;
    }
}
```

- [ ] **Step 6: Write web/Dockerfile**

```dockerfile
FROM node:20-alpine AS builder
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=builder /app/build /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
```

- [ ] **Step 7: Copy .env.example to .env and fill in real values**

```bash
cp .env.example .env
# Edit .env: set HA_URL, HA_TOKEN, MI_API_URL (use 10.0.1.51:8000 for dev)
# OPENWEATHER: sign up at openweathermap.org (free tier), get API key
# GOOGLE: skip for now — will be set up in Task 8
```

- [ ] **Step 8: Commit scaffold**

```bash
git add docker-compose.yml .env.example api/Dockerfile web/Dockerfile web/nginx.conf .gitignore
git commit -m "chore: initial TenBerge-OS repo scaffold"
```

---

## Task 2: FastAPI Backend Scaffold

**Files:**
- Create: `api/requirements.txt`
- Create: `api/config.py`
- Create: `api/main.py`
- Create: `api/tests/conftest.py`
- Create: `api/tests/test_health.py`

- [ ] **Step 1: Write api/requirements.txt**

```
fastapi==0.115.0
uvicorn[standard]==0.30.6
httpx==0.27.2
pydantic-settings==2.4.0
google-auth==2.35.0
google-auth-oauthlib==1.2.1
google-api-python-client==2.149.0
feedparser==6.0.11
aiosqlite==0.20.0
pytest==8.3.3
pytest-asyncio==0.24.0
```

- [ ] **Step 2: Write api/config.py**

```python
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    mi_api_url: str = "http://10.0.1.51:8000"
    ha_url: str = ""
    ha_token: str = ""
    openweather_api_key: str = ""
    openweather_lat: float = 0.0
    openweather_lon: float = 0.0
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8001/api/auth/google/callback"
    token_path: str = "/app/data/google_tokens.json"
    db_path: str = "/app/data/tenberge.db"

    class Config:
        env_file = ".env"

settings = Settings()
```

- [ ] **Step 3: Write api/main.py**

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routers import hud, markets, calendar, feed, auth

app = FastAPI(title="TenBerge API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(hud.router, prefix="/api")
app.include_router(markets.router, prefix="/api")
app.include_router(calendar.router, prefix="/api")
app.include_router(feed.router, prefix="/api")
app.include_router(auth.router, prefix="/api")

@app.get("/api/health")
async def health():
    return {"status": "ok"}
```

- [ ] **Step 4: Write api/tests/conftest.py**

```python
import pytest
from httpx import AsyncClient, ASGITransport
from main import app

@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
```

Also create `api/pytest.ini`:

```ini
[pytest]
asyncio_mode = auto
```

- [ ] **Step 5: Write failing test for health endpoint**

`api/tests/test_health.py`:
```python
async def test_health(client):
    r = await client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
```

- [ ] **Step 6: Create stub router files so imports don't fail**

```bash
mkdir -p api/routers api/services api/tests
touch api/routers/__init__.py api/services/__init__.py api/tests/__init__.py
```

Each stub router (copy this pattern for hud, markets, calendar, feed, auth):

`api/routers/hud.py`:
```python
from fastapi import APIRouter
router = APIRouter()
```

Repeat the same one-liner for `markets.py`, `calendar.py`, `feed.py`, `auth.py`.

- [ ] **Step 7: Run the health test**

```bash
cd /home/dev/workspace/TenBerge-OS/api
pip install -r requirements.txt  # local dev install
pytest tests/test_health.py -v
```

Expected: `PASSED tests/test_health.py::test_health`

- [ ] **Step 8: Commit**

```bash
git add api/
git commit -m "feat: FastAPI backend scaffold with health endpoint"
```

---

## Task 3: SvelteKit Frontend Scaffold

**Files:**
- Create: `web/package.json`
- Create: `web/svelte.config.js`
- Create: `web/vite.config.js`
- Create: `web/vitest.config.js`
- Create: `web/src/app.html`
- Create: `web/src/routes/+layout.js`

- [ ] **Step 1: Write web/package.json**

```json
{
  "name": "tenberge-web",
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite dev",
    "build": "vite build",
    "preview": "vite preview",
    "test": "vitest run"
  },
  "devDependencies": {
    "@sveltejs/adapter-static": "^3.0.4",
    "@sveltejs/kit": "^2.5.24",
    "@sveltejs/vite-plugin-svelte": "^3.1.2",
    "@testing-library/svelte": "^5.2.3",
    "svelte": "^4.2.19",
    "vite": "^5.4.2",
    "vitest": "^2.0.5",
    "jsdom": "^25.0.0"
  }
}
```

- [ ] **Step 2: Write web/svelte.config.js**

```js
import adapter from '@sveltejs/adapter-static';

export default {
  kit: {
    adapter: adapter({
      fallback: 'index.html',
      strict: false
    })
  }
};
```

- [ ] **Step 3: Write web/vite.config.js**

```js
import { sveltekit } from '@sveltejs/vite-plugin-svelte';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [sveltekit()]
});
```

- [ ] **Step 4: Write web/vitest.config.js**

```js
import { defineConfig } from 'vitest/config';
import { svelte } from '@sveltejs/vite-plugin-svelte';

export default defineConfig({
  plugins: [svelte({ hot: !process.env.VITEST })],
  test: {
    environment: 'jsdom',
    globals: true,
    include: ['src/**/*.{test,spec}.{js,ts}']
  }
});
```

- [ ] **Step 5: Write web/src/app.html**

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
    <meta name="theme-color" content="#080804" />
    <link rel="preconnect" href="https://fonts.googleapis.com" />
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
    <link href="https://fonts.googleapis.com/css2?family=Geist+Mono:wght@300;400;500;600&family=Source+Serif+4:ital,opsz,wght@0,8..60,300;0,8..60,400;0,8..60,600;1,8..60,400&display=swap" rel="stylesheet" />
    %sveltekit.head%
  </head>
  <body data-sveltekit-preload-data="hover">
    <div id="app">%sveltekit.body%</div>
  </body>
</html>
```

- [ ] **Step 6: Write web/src/routes/+layout.js**

```js
export const ssr = false;
export const prerender = false;
```

- [ ] **Step 7: Install dependencies and verify dev server starts**

```bash
cd /home/dev/workspace/TenBerge-OS/web
npm install
npm run dev
```

Expected: Vite dev server at `http://localhost:5173` (blank page is fine — no routes yet).

- [ ] **Step 8: Commit**

```bash
cd /home/dev/workspace/TenBerge-OS
git add web/
git commit -m "feat: SvelteKit frontend scaffold"
```

---

## Task 4: Global Design System (CSS)

**Files:**
- Create: `web/src/app.css`
- Create: `web/src/lib/components/AppTile.svelte` (stub)

- [ ] **Step 1: Write web/src/app.css**

```css
/* Mercedes AMG F1 2026 palette + TenBerge design system */
:root {
  --bg:     #080804;
  --accent: #00D7B6;
  --text:   #C8CCCE;
  --muted:  #565F64;
  --border: #1a1a16;

  --font-mono:  'Geist Mono', 'Courier New', monospace;
  --font-serif: 'Source Serif 4', Georgia, serif;

  --hud-h:   48px;
  --safe-top: env(safe-area-inset-top, 0px);
  --safe-bot: env(safe-area-inset-bottom, 0px);
}

*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

html, body {
  background: var(--bg);
  color: var(--text);
  font-family: var(--font-mono);
  font-size: 14px;
  line-height: 1.4;
  height: 100%;
  overflow: hidden; /* home screen never scrolls */
  -webkit-font-smoothing: antialiased;
}

/* Typography helpers */
.mono  { font-family: var(--font-mono); }
.serif { font-family: var(--font-serif); }
.label { font-size: 10px; letter-spacing: 0.12em; text-transform: uppercase; color: var(--muted); }
.accent { color: var(--accent); }
.dim    { color: var(--muted); }

/* Accent glow for interactive elements */
.glow {
  box-shadow: 0 0 8px 1px color-mix(in srgb, var(--accent) 40%, transparent);
}

/* HUD strip base */
.hud {
  height: var(--hud-h);
  background: var(--bg);
  border-color: var(--border);
  display: flex;
  align-items: center;
  padding: 0 16px;
  gap: 12px;
  flex-shrink: 0;
}
.hud-top  { border-bottom: 1px solid var(--border); padding-top: var(--safe-top); }
.hud-bot  { border-top: 1px solid var(--border); padding-bottom: var(--safe-bot); }

/* Pill badge */
.pill {
  display: inline-flex;
  align-items: center;
  padding: 2px 8px;
  border: 1px solid var(--accent);
  color: var(--accent);
  font-size: 10px;
  letter-spacing: 0.1em;
  font-family: var(--font-mono);
}
.pill.risk-off { border-color: #f87171; color: #f87171; }
.pill.neutral  { border-color: #facc15; color: #facc15; }
.pill.risk-on  { border-color: var(--accent); color: var(--accent); }

/* Module shell — scrollable inner content */
.module {
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
  padding: 16px;
}

/* Back button */
.back-btn {
  background: none;
  border: none;
  color: var(--accent);
  font-family: var(--font-mono);
  font-size: 14px;
  cursor: pointer;
  padding: 0;
  letter-spacing: 0.05em;
}
```

- [ ] **Step 2: Import app.css in the root layout (will create in Task 5)**

Note: `app.css` is imported in `+layout.svelte` (created in Task 5). No action needed here.

- [ ] **Step 3: Write AppTile stub**

`web/src/lib/components/AppTile.svelte`:
```svelte
<script>
  export let label = '';
  export let href = '/';
  export let icon = '';
</script>

<a {href} class="tile">
  <span class="tile-icon">{icon}</span>
  <span class="label">{label}</span>
</a>

<style>
  .tile {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 6px;
    aspect-ratio: 1;
    border: 1px solid var(--border);
    text-decoration: none;
    color: var(--text);
    cursor: pointer;
    transition: border-color 0.15s, box-shadow 0.15s;
  }
  .tile:active {
    border-color: var(--accent);
    box-shadow: 0 0 8px 1px color-mix(in srgb, var(--accent) 40%, transparent);
  }
  .tile-icon {
    font-size: 22px;
    line-height: 1;
  }
</style>
```

- [ ] **Step 4: Commit**

```bash
cd /home/dev/workspace/TenBerge-OS
git add web/src/app.css web/src/lib/components/AppTile.svelte
git commit -m "feat: design system CSS (Mercedes AMG palette, typography, HUD classes)"
```

---

## Task 5: Root Layout + HUD Strip Components

**Files:**
- Create: `web/src/routes/+layout.svelte`
- Create: `web/src/lib/components/HudTop.svelte`
- Create: `web/src/lib/components/HudBottom.svelte`
- Create: `web/src/lib/stores/hud.js`

- [ ] **Step 1: Write the HUD Svelte store (mock data for now)**

`web/src/lib/stores/hud.js`:
```js
import { writable } from 'svelte/store';

export const hudData = writable({
  posture: 'NEUTRAL',
  composite_score: 0,
  temperature: '--°F',
  conditions: '',
  next_event_title: null,
  next_event_eta_minutes: null,
  habit_streak: 0,
  unread_gmail: 0,
  ha_status: 'OK'
});

let _interval = null;

export async function startHudPolling() {
  await _fetchHud();
  _interval = setInterval(_fetchHud, 60_000);
}

export function stopHudPolling() {
  if (_interval) { clearInterval(_interval); _interval = null; }
}

async function _fetchHud() {
  try {
    const r = await fetch('/api/hud');
    if (r.ok) hudData.set(await r.json());
  } catch {
    // network error — keep stale data
  }
}
```

- [ ] **Step 2: Write HudTop.svelte**

`web/src/lib/components/HudTop.svelte`:
```svelte
<script>
  import { hudData } from '$lib/stores/hud.js';

  let now = new Date();
  setInterval(() => { now = new Date(); }, 1000);

  function fmt(d) {
    const days = ['SUN','MON','TUE','WED','THU','FRI','SAT'];
    const months = ['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC'];
    const hh = String(d.getHours()).padStart(2,'0');
    const mm = String(d.getMinutes()).padStart(2,'0');
    return {
      time: `${hh}:${mm}`,
      date: `${days[d.getDay()]} ${d.getDate()} ${months[d.getMonth()]}`
    };
  }

  $: ({ time, date } = fmt(now));
  $: postureClass = {
    'RISK-OFF': 'risk-off',
    'NEUTRAL': 'neutral',
    'RISK-ON': 'risk-on'
  }[$hudData.posture] ?? 'neutral';
</script>

<div class="hud hud-top">
  <span class="time">{time}</span>
  <span class="dim">·</span>
  <span class="date dim">{date}</span>
  <span class="spacer" />
  <span class="pill {postureClass}">{$hudData.posture}</span>
  <span class="score accent">{$hudData.composite_score}</span>
  <span class="dim">·</span>
  <span class="weather dim">{$hudData.temperature}</span>
</div>

<style>
  .hud { font-family: var(--font-mono); font-size: 12px; }
  .time { font-size: 16px; font-weight: 500; color: var(--text); letter-spacing: 0.04em; }
  .date { font-size: 11px; }
  .score { font-size: 13px; font-weight: 500; }
  .weather { font-size: 11px; }
  .spacer { flex: 1; }
</style>
```

- [ ] **Step 3: Write HudBottom.svelte**

`web/src/lib/components/HudBottom.svelte`:
```svelte
<script>
  import { hudData } from '$lib/stores/hud.js';

  $: nextEvent = $hudData.next_event_title
    ? `${$hudData.next_event_title} ▸ ${etaLabel($hudData.next_event_eta_minutes)}`
    : 'NO EVENTS';

  function etaLabel(mins) {
    if (mins == null) return '';
    if (mins < 60) return `${mins}m`;
    return `${Math.floor(mins / 60)}h ${mins % 60}m`;
  }
</script>

<div class="hud hud-bot">
  <span class="event">{nextEvent}</span>
  <span class="spacer" />
  {#if $hudData.habit_streak > 0}
    <span class="label accent">STREAK: {$hudData.habit_streak}d</span>
    <span class="dim">·</span>
  {/if}
  <span class="label dim">MAIL: {$hudData.unread_gmail}</span>
  <span class="dim">·</span>
  <span class="label" class:accent={$hudData.ha_status === 'OK'} class:risk={$hudData.ha_status !== 'OK'}>
    HA: {$hudData.ha_status}
  </span>
</div>

<style>
  .hud { font-family: var(--font-mono); font-size: 11px; }
  .event { color: var(--text); font-size: 11px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 55%; }
  .spacer { flex: 1; }
  .risk { color: #f87171; }
</style>
```

- [ ] **Step 4: Write +layout.svelte**

`web/src/routes/+layout.svelte`:
```svelte
<script>
  import '../app.css';
  import HudTop from '$lib/components/HudTop.svelte';
  import HudBottom from '$lib/components/HudBottom.svelte';
  import { startHudPolling, stopHudPolling } from '$lib/stores/hud.js';
  import { onMount, onDestroy } from 'svelte';

  onMount(startHudPolling);
  onDestroy(stopHudPolling);
</script>

<div class="os-shell">
  <HudTop />
  <main class="content">
    <slot />
  </main>
  <HudBottom />
</div>

<style>
  .os-shell {
    display: flex;
    flex-direction: column;
    height: 100dvh; /* dynamic viewport height — respects browser chrome on mobile */
  }
  .content {
    flex: 1;
    overflow: hidden;
    position: relative;
  }
</style>
```

- [ ] **Step 5: Create minimal home page so dev server shows something**

`web/src/routes/+page.svelte`:
```svelte
<div style="padding:20px; color:var(--accent); font-family:var(--font-mono);">
  TENBERGE OS — scaffold OK
</div>
```

- [ ] **Step 6: Run dev server and verify HUD strips appear**

```bash
cd /home/dev/workspace/TenBerge-OS/web
npm run dev
```

Open `http://localhost:5173`. Expected: dark background, top HUD strip showing time/date/NEUTRAL/0, bottom HUD strip. The text "TENBERGE OS — scaffold OK" should be between the strips.

- [ ] **Step 7: Commit**

```bash
cd /home/dev/workspace/TenBerge-OS
git add web/src/
git commit -m "feat: root layout with HUD strips and Svelte store"
```

---

## Task 6: Home Screen App Grid

**Files:**
- Create: `web/src/lib/apps.js`
- Modify: `web/src/routes/+page.svelte`
- Create: `web/src/routes/[module]/+page.svelte`

- [ ] **Step 1: Write web/src/lib/apps.js**

```js
// Inline SVG path data for each app icon (24×24 viewBox, stroke-based)
const ICONS = {
  chart:    'M3 18 L9 12 L13 16 L21 7 M18 7 L21 7 L21 10',
  calendar: 'M3 6h18M3 6v14h18V6M8 2v4M16 2v4M8 11h2M12 11h2M16 11h2M8 15h2M12 15h2',
  signal:   'M1 6l4 4 4-8 4 8 4-4 4 4',
  check:    'M9 11l3 3L22 4M21 12v7a2 2 0 01-2 2H5a2 2 0 01-2-2V5a2 2 0 012-2h11',
  mail:     'M3 8l9 6 9-6M3 8v10a1 1 0 001 1h16a1 1 0 001-1V8M3 8a1 1 0 011-1h16a1 1 0 011 1',
  pulse:    'M2 12h4l3-7 4 14 3-7h6',
  home:     'M3 12l9-9 9 9M5 10v9h5v-5h4v5h5v-9',
  brain:    'M12 5a7 7 0 00-7 7h2a5 5 0 015-5M12 5a7 7 0 017 7h-2a5 5 0 00-5-5M8 16h8M10 19h4',
  dollar:   'M12 2v20M17 5H9.5a3.5 3.5 0 000 7h5a3.5 3.5 0 010 7H6',
  streak:   'M13 2L3 14h9l-1 8 10-12h-9l1-8z',
  play:     'M5 3l14 9-14 9V3z',
  person:   'M20 21v-2a4 4 0 00-4-4H8a4 4 0 00-4 4v2M12 11a4 4 0 100-8 4 4 0 000 8z',
  card:     'M1 4h22v16H1V4zm0 6h22',
  plane:    'M21 16l-9-9-9 9M12 7v14',
  pin:      'M12 22s-8-7-8-12a8 8 0 1116 0c0 5-8 12-8 12zM12 10a2 2 0 100-4 2 2 0 000 4z',
};

export const APPS = [
  { key: 'markets',  label: 'MARKETS',  icon: 'chart',    href: '/markets'  },
  { key: 'calendar', label: 'CALENDAR', icon: 'calendar', href: '/calendar' },
  { key: 'feed',     label: 'FEED',     icon: 'signal',   href: '/feed'     },
  { key: 'tasks',    label: 'TASKS',    icon: 'check',    href: '/tasks'    },
  { key: 'mail',     label: 'MAIL',     icon: 'mail',     href: '/mail'     },
  { key: 'health',   label: 'HEALTH',   icon: 'pulse',    href: '/health'   },
  { key: 'ha',       label: 'HA',       icon: 'home',     href: '/ha'       },
  { key: 'notes',    label: 'NOTES',    icon: 'brain',    href: '/notes'    },
  { key: 'wealth',   label: 'WEALTH',   icon: 'dollar',   href: '/wealth'   },
  { key: 'habits',   label: 'HABITS',   icon: 'streak',   href: '/habits'   },
  { key: 'media',    label: 'MEDIA',    icon: 'play',     href: '/media'    },
  { key: 'people',   label: 'PEOPLE',   icon: 'person',   href: '/people'   },
  { key: 'subs',     label: 'SUBS',     icon: 'card',     href: '/subs'     },
  { key: 'travel',   label: 'TRAVEL',   icon: 'plane',    href: '/travel'   },
  { key: 'memory',   label: 'MEMORY',   icon: 'pin',      href: '/memory'   },
];

export function iconPath(key) {
  return ICONS[key] ?? '';
}
```

- [ ] **Step 2: Write the home screen +page.svelte**

`web/src/routes/+page.svelte`:
```svelte
<script>
  import { APPS, iconPath } from '$lib/apps.js';
</script>

<div class="grid-wrap">
  <div class="grid">
    {#each APPS as app}
      <a href={app.href} class="tile" data-key={app.key}>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d={iconPath(app.icon)} />
        </svg>
        <span class="label">{app.label}</span>
      </a>
    {/each}
  </div>
</div>

<style>
  .grid-wrap {
    height: 100%;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 8px 12px;
  }
  .grid {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 8px;
    width: 100%;
  }
  .tile {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 5px;
    padding: 12px 4px;
    border: 1px solid var(--border);
    text-decoration: none;
    color: var(--text);
    min-height: 72px;
    transition: border-color 0.12s, color 0.12s;
  }
  .tile:hover, .tile:focus-visible {
    border-color: var(--muted);
    outline: none;
  }
  .tile:active {
    border-color: var(--accent);
    color: var(--accent);
    box-shadow: 0 0 8px 1px color-mix(in srgb, var(--accent) 30%, transparent);
  }
  svg {
    width: 22px;
    height: 22px;
    flex-shrink: 0;
  }
  .label {
    font-family: var(--font-mono);
    font-size: 9px;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--muted);
  }
</style>
```

- [ ] **Step 3: Write the "Coming Soon" fallback for Phase 2-4 routes**

`web/src/routes/[module]/+page.svelte`:
```svelte
<script>
  import { page } from '$app/stores';
</script>

<div class="shell">
  <a href="/" class="back-btn">← BACK</a>
  <div class="body">
    <p class="label">MODULE</p>
    <h1 class="name">{$page.params.module.toUpperCase()}</h1>
    <p class="dim">Coming in a future phase.</p>
  </div>
</div>

<style>
  .shell { height: 100%; display: flex; flex-direction: column; padding: 16px; }
  .back-btn { color: var(--accent); font-family: var(--font-mono); font-size: 13px; text-decoration: none; margin-bottom: 32px; display: inline-block; }
  .body { display: flex; flex-direction: column; gap: 8px; }
  .name { font-family: var(--font-mono); font-size: 28px; font-weight: 500; color: var(--text); letter-spacing: 0.05em; }
</style>
```

- [ ] **Step 4: Verify in dev server — home screen grid renders 15 tiles, tapping any non-Phase-1 tile shows the Coming Soon screen**

```bash
cd /home/dev/workspace/TenBerge-OS/web && npm run dev
```

Open `http://localhost:5173`. Verify: 15 tiles in 3-column grid, all fit without scroll, tapping a tile navigates and shows module name, back button returns home.

- [ ] **Step 5: Commit**

```bash
cd /home/dev/workspace/TenBerge-OS
git add web/src/lib/apps.js web/src/routes/+page.svelte web/src/routes/
git commit -m "feat: home screen app grid with 15 tiles and coming-soon fallback"
```

---

## Task 7: /api/hud Endpoint — Top Strip (MI + Weather)

**Files:**
- Create: `api/services/mi.py`
- Create: `api/services/weather.py`
- Modify: `api/routers/hud.py`
- Create: `api/tests/test_hud.py`

- [ ] **Step 1: Write api/services/mi.py**

```python
import httpx
from config import settings

async def get_market_posture() -> dict:
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(f"{settings.mi_api_url}/api/market-posture")
        r.raise_for_status()
        return r.json()
```

- [ ] **Step 2: Write api/services/weather.py**

```python
import httpx
from config import settings

async def get_current_weather() -> dict:
    if not settings.openweather_api_key:
        return {"temperature": "--°F", "conditions": ""}
    url = "https://api.openweathermap.org/data/2.5/weather"
    params = {
        "lat": settings.openweather_lat,
        "lon": settings.openweather_lon,
        "appid": settings.openweather_api_key,
        "units": "imperial",
    }
    async with httpx.AsyncClient(timeout=8.0) as client:
        r = await client.get(url, params=params)
        r.raise_for_status()
        data = r.json()
    return {
        "temperature": f"{round(data['main']['temp'])}°F",
        "conditions": data["weather"][0]["description"].title(),
    }
```

- [ ] **Step 3: Write test_hud.py with failing test for missing posture**

`api/tests/test_hud.py`:
```python
import pytest
import respx
import httpx
from config import settings

@pytest.fixture(autouse=True)
def mock_mi(respx_mock):
    respx_mock.get(f"{settings.mi_api_url}/api/market-posture").mock(
        return_value=httpx.Response(200, json={
            "composite_score": 65,
            "posture": "NEUTRAL",
            "llm_summary": "Markets steady.",
            "signals": [],
        })
    )

async def test_hud_returns_posture(client):
    r = await client.get("/api/hud")
    assert r.status_code == 200
    body = r.json()
    assert body["posture"] == "NEUTRAL"
    assert body["composite_score"] == 65

async def test_hud_has_all_required_keys(client):
    r = await client.get("/api/hud")
    body = r.json()
    for key in ("posture", "composite_score", "temperature", "conditions",
                "next_event_title", "next_event_eta_minutes",
                "habit_streak", "unread_gmail", "ha_status"):
        assert key in body, f"Missing key: {key}"
```

Add `respx` to requirements.txt:
```
respx==0.21.1
```

- [ ] **Step 4: Run tests — expect failure**

```bash
cd /home/dev/workspace/TenBerge-OS/api && pytest tests/test_hud.py -v
```

Expected: `FAILED` — `/api/hud` returns 404 (router is empty stub).

- [ ] **Step 5: Implement api/routers/hud.py**

```python
import asyncio
from fastapi import APIRouter
from services.mi import get_market_posture
from services.weather import get_current_weather

router = APIRouter()

@router.get("/hud")
async def get_hud():
    posture_data, weather_data = await asyncio.gather(
        _safe(get_market_posture, {"composite_score": 0, "posture": "NEUTRAL"}),
        _safe(get_current_weather, {"temperature": "--°F", "conditions": ""}),
    )
    return {
        "posture": posture_data.get("posture", "NEUTRAL"),
        "composite_score": posture_data.get("composite_score", 0),
        "temperature": weather_data.get("temperature", "--°F"),
        "conditions": weather_data.get("conditions", ""),
        # Bottom strip — placeholders until Task 9
        "next_event_title": None,
        "next_event_eta_minutes": None,
        "habit_streak": 0,
        "unread_gmail": 0,
        "ha_status": "OK",
    }

async def _safe(coro_fn, default):
    try:
        return await coro_fn()
    except Exception:
        return default
```

- [ ] **Step 6: Run tests — expect pass**

```bash
cd /home/dev/workspace/TenBerge-OS/api && pytest tests/test_hud.py -v
```

Expected: both tests `PASSED`.

- [ ] **Step 7: Commit**

```bash
cd /home/dev/workspace/TenBerge-OS
git add api/
git commit -m "feat: /api/hud endpoint with MI posture + weather"
```

---

## Task 8: Google OAuth2 Setup (One-Time)

This task sets up Google OAuth2 for Calendar and Gmail. It runs once to authorize. Tokens persist in the Docker volume and auto-refresh.

**Files:**
- Create: `api/services/google_auth.py`
- Create: `api/routers/auth.py`

- [ ] **Step 1: Create a Google Cloud project and OAuth2 credentials**

1. Go to https://console.cloud.google.com
2. Create project `TenBerge-OS`
3. Enable APIs: **Google Calendar API**, **Gmail API**
4. Create OAuth2 credentials → Desktop app → download JSON
5. From the JSON, copy `client_id` and `client_secret` into `.env`
6. Add `https://os.austin10berge.com/api/auth/google/callback` as an authorized redirect URI

- [ ] **Step 2: Write api/services/google_auth.py**

```python
import json
import os
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request
from config import settings

SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/gmail.readonly",
]

def _client_config():
    return {
        "web": {
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "redirect_uris": [settings.google_redirect_uri],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }

def get_credentials() -> Credentials | None:
    if not os.path.exists(settings.token_path):
        return None
    with open(settings.token_path) as f:
        data = json.load(f)
    creds = Credentials.from_authorized_user_info(data, SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        _save(creds)
    return creds if creds.valid else None

def _save(creds: Credentials):
    os.makedirs(os.path.dirname(settings.token_path), exist_ok=True)
    with open(settings.token_path, "w") as f:
        f.write(creds.to_json())

def make_flow() -> Flow:
    return Flow.from_client_config(
        _client_config(),
        scopes=SCOPES,
        redirect_uri=settings.google_redirect_uri,
    )

def exchange_code(code: str) -> Credentials:
    flow = make_flow()
    flow.fetch_token(code=code)
    _save(flow.credentials)
    return flow.credentials
```

- [ ] **Step 3: Write api/routers/auth.py**

```python
from fastapi import APIRouter
from fastapi.responses import RedirectResponse
from services.google_auth import make_flow, exchange_code, get_credentials

router = APIRouter()

@router.get("/auth/google/initiate")
async def google_initiate():
    flow = make_flow()
    url, _ = flow.authorization_url(prompt="consent", access_type="offline")
    return {"auth_url": url}

@router.get("/auth/google/callback")
async def google_callback(code: str):
    exchange_code(code)
    return RedirectResponse(url="/")

@router.get("/auth/google/status")
async def google_status():
    creds = get_credentials()
    return {"authorized": creds is not None}
```

- [ ] **Step 4: Authorize (one-time)**

```bash
# Start the API locally:
cd /home/dev/workspace/TenBerge-OS/api
uvicorn main:app --port 8001 --reload

# In another terminal:
curl http://localhost:8001/api/auth/google/initiate
# Copy the auth_url from the response, open it in a browser
# Sign in with your Google account, grant Calendar + Gmail access
# You'll be redirected to the callback URL — that saves the token
```

Expected: `google_tokens.json` created at `/app/data/` (or locally at `./data/google_tokens.json` during dev). Check with:
```bash
curl http://localhost:8001/api/auth/google/status
# {"authorized": true}
```

- [ ] **Step 5: Commit**

```bash
cd /home/dev/workspace/TenBerge-OS
git add api/services/google_auth.py api/routers/auth.py
git commit -m "feat: Google OAuth2 flow for Calendar + Gmail"
```

---

## Task 9: /api/hud Bottom Strip (Calendar + HA + Gmail)

**Files:**
- Create: `api/services/google_calendar.py`
- Create: `api/services/google_gmail.py`
- Create: `api/services/home_assistant.py`
- Modify: `api/routers/hud.py`
- Modify: `api/tests/test_hud.py`

- [ ] **Step 1: Write api/services/google_calendar.py**

```python
from datetime import datetime, timezone, timedelta
from googleapiclient.discovery import build
from services.google_auth import get_credentials

def get_next_event() -> dict:
    creds = get_credentials()
    if not creds:
        return {"next_event_title": None, "next_event_eta_minutes": None}
    service = build("calendar", "v3", credentials=creds, cache_discovery=False)
    now = datetime.now(timezone.utc)
    result = service.events().list(
        calendarId="primary",
        timeMin=now.isoformat(),
        maxResults=1,
        singleEvents=True,
        orderBy="startTime",
    ).execute()
    events = result.get("items", [])
    if not events:
        return {"next_event_title": None, "next_event_eta_minutes": None}
    ev = events[0]
    start = ev["start"].get("dateTime", ev["start"].get("date"))
    try:
        start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
        eta_minutes = max(0, int((start_dt - now).total_seconds() / 60))
    except ValueError:
        eta_minutes = None
    return {
        "next_event_title": ev.get("summary", "Event"),
        "next_event_eta_minutes": eta_minutes,
    }
```

- [ ] **Step 2: Write api/services/google_gmail.py**

```python
from googleapiclient.discovery import build
from services.google_auth import get_credentials

def get_unread_count() -> int:
    creds = get_credentials()
    if not creds:
        return 0
    service = build("gmail", "v1", credentials=creds, cache_discovery=False)
    result = service.users().labels().get(userId="me", id="INBOX").execute()
    return result.get("messagesUnread", 0)
```

- [ ] **Step 3: Write api/services/home_assistant.py**

```python
import httpx
from config import settings

async def get_ha_status() -> str:
    if not settings.ha_url or not settings.ha_token:
        return "OK"
    headers = {"Authorization": f"Bearer {settings.ha_token}"}
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{settings.ha_url}/api/", headers=headers)
            return "OK" if r.status_code == 200 else "ERR"
    except Exception:
        return "ERR"
```

- [ ] **Step 4: Update api/routers/hud.py to include bottom strip**

```python
import asyncio
from concurrent.futures import ThreadPoolExecutor
from fastapi import APIRouter
from services.mi import get_market_posture
from services.weather import get_current_weather
from services.google_calendar import get_next_event
from services.google_gmail import get_unread_count
from services.home_assistant import get_ha_status

router = APIRouter()
_executor = ThreadPoolExecutor(max_workers=2)

@router.get("/hud")
async def get_hud():
    loop = asyncio.get_event_loop()
    posture_data, weather_data, ha = await asyncio.gather(
        _safe(get_market_posture, {"composite_score": 0, "posture": "NEUTRAL"}),
        _safe(get_current_weather, {"temperature": "--°F", "conditions": ""}),
        _safe(get_ha_status, "OK"),
    )
    # Google SDK calls are sync — run in thread pool
    cal = await loop.run_in_executor(_executor, _safe_sync, get_next_event,
                                     {"next_event_title": None, "next_event_eta_minutes": None})
    gmail_count = await loop.run_in_executor(_executor, _safe_sync, get_unread_count, 0)

    return {
        "posture": posture_data.get("posture", "NEUTRAL"),
        "composite_score": posture_data.get("composite_score", 0),
        "temperature": weather_data.get("temperature", "--°F"),
        "conditions": weather_data.get("conditions", ""),
        "next_event_title": cal.get("next_event_title"),
        "next_event_eta_minutes": cal.get("next_event_eta_minutes"),
        "habit_streak": 0,
        "unread_gmail": gmail_count if isinstance(gmail_count, int) else 0,
        "ha_status": ha if isinstance(ha, str) else "OK",
    }

async def _safe(coro_fn, default):
    try:
        return await coro_fn()
    except Exception:
        return default

def _safe_sync(fn, default):
    try:
        return fn()
    except Exception:
        return default
```

- [ ] **Step 5: Run full hud test suite**

```bash
cd /home/dev/workspace/TenBerge-OS/api && pytest tests/test_hud.py -v
```

Expected: both tests `PASSED`.

- [ ] **Step 6: Commit**

```bash
cd /home/dev/workspace/TenBerge-OS
git add api/services/ api/routers/hud.py
git commit -m "feat: /api/hud bottom strip — calendar, gmail, home assistant"
```

---

## Task 10: Markets Module

**Files:**
- Create: `api/services/mi.py` (extend — already exists)
- Create: `api/routers/markets.py`
- Create: `api/tests/test_markets.py`
- Create: `web/src/routes/markets/+page.svelte`

- [ ] **Step 1: Extend api/services/mi.py**

```python
import httpx
from config import settings

async def get_market_posture() -> dict:
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(f"{settings.mi_api_url}/api/market-posture")
        r.raise_for_status()
        return r.json()

async def get_market_overview() -> dict:
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(f"{settings.mi_api_url}/api/market-overview")
        r.raise_for_status()
        return r.json()

async def get_watchlist() -> dict:
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(f"{settings.mi_api_url}/api/watchlist")
        r.raise_for_status()
        return r.json()
```

- [ ] **Step 2: Write failing test for markets endpoint**

`api/tests/test_markets.py`:
```python
import pytest
import httpx
import respx
from config import settings

@pytest.fixture(autouse=True)
def mock_mi_markets(respx_mock):
    respx_mock.get(f"{settings.mi_api_url}/api/market-posture").mock(
        return_value=httpx.Response(200, json={
            "composite_score": 72, "posture": "NEUTRAL",
            "llm_summary": "Steady.", "signals": []
        })
    )
    respx_mock.get(f"{settings.mi_api_url}/api/market-overview").mock(
        return_value=httpx.Response(200, json={"vix": 14.5, "sectors": []})
    )
    respx_mock.get(f"{settings.mi_api_url}/api/watchlist").mock(
        return_value=httpx.Response(200, json={"tickers": ["AAPL", "MSFT"]})
    )

async def test_markets_returns_posture(client):
    r = await client.get("/api/markets")
    assert r.status_code == 200
    body = r.json()
    assert "posture" in body
    assert "composite_score" in body
    assert "overview" in body
    assert "watchlist" in body
```

- [ ] **Step 3: Run test — expect FAIL (router is stub)**

```bash
cd /home/dev/workspace/TenBerge-OS/api && pytest tests/test_markets.py -v
```

Expected: FAILED — 404.

- [ ] **Step 4: Implement api/routers/markets.py**

```python
import asyncio
from fastapi import APIRouter
from services.mi import get_market_posture, get_market_overview, get_watchlist

router = APIRouter()

@router.get("/markets")
async def get_markets():
    posture, overview, watchlist = await asyncio.gather(
        _safe(get_market_posture, {}),
        _safe(get_market_overview, {}),
        _safe(get_watchlist, {"tickers": []}),
    )
    return {
        "posture": posture.get("posture", "NEUTRAL"),
        "composite_score": posture.get("composite_score", 0),
        "llm_summary": posture.get("llm_summary", ""),
        "signals": posture.get("signals", []),
        "overview": overview,
        "watchlist": watchlist,
    }

async def _safe(coro_fn, default):
    try:
        return await coro_fn()
    except Exception:
        return default
```

- [ ] **Step 5: Run test — expect PASS**

```bash
cd /home/dev/workspace/TenBerge-OS/api && pytest tests/test_markets.py -v
```

- [ ] **Step 6: Write web/src/routes/markets/+page.svelte**

```svelte
<script>
  import { onMount } from 'svelte';

  let data = null;
  let loading = true;

  onMount(async () => {
    try {
      const r = await fetch('/api/markets');
      data = await r.json();
    } finally {
      loading = false;
    }
  });

  $: postureClass = { 'RISK-OFF': 'risk-off', 'NEUTRAL': 'neutral', 'RISK-ON': 'risk-on' }[data?.posture] ?? 'neutral';
</script>

<div class="module">
  <div class="module-header">
    <a href="/" class="back-btn">← BACK</a>
    <span class="label">MARKETS</span>
  </div>

  {#if loading}
    <p class="dim label">LOADING···</p>
  {:else if data}
    <div class="posture-row">
      <span class="pill {postureClass}">{data.posture}</span>
      <span class="score accent">{data.composite_score}</span>
      <span class="label dim">/ 100</span>
    </div>

    {#if data.llm_summary}
      <div class="summary serif">{data.llm_summary}</div>
    {/if}

    {#if data.signals?.length}
      <div class="section">
        <p class="label dim">SIGNALS</p>
        {#each data.signals as sig}
          <div class="signal-row">
            <span class="sig-source label">{sig.source}</span>
            <span class="sig-score" class:accent={sig.score > 0} class:dim={sig.score <= 0}>
              {sig.score > 0 ? '+' : ''}{sig.score}
            </span>
          </div>
        {/each}
      </div>
    {/if}

    <div class="section">
      <p class="label dim">WATCHLIST</p>
      <div class="tickers">
        {#each (data.watchlist?.tickers ?? []) as t}
          <span class="ticker">{t}</span>
        {/each}
      </div>
    </div>
  {/if}
</div>

<style>
  .module { height: 100%; overflow-y: auto; padding: 16px; display: flex; flex-direction: column; gap: 16px; }
  .module-header { display: flex; align-items: center; gap: 12px; }
  .back-btn { color: var(--accent); font-family: var(--font-mono); font-size: 13px; text-decoration: none; }
  .posture-row { display: flex; align-items: center; gap: 10px; }
  .score { font-family: var(--font-mono); font-size: 24px; font-weight: 500; }
  .summary { font-family: var(--font-serif); font-size: 14px; color: var(--text); line-height: 1.6; border-left: 2px solid var(--accent); padding-left: 12px; }
  .section { display: flex; flex-direction: column; gap: 6px; }
  .signal-row { display: flex; justify-content: space-between; align-items: center; padding: 6px 0; border-bottom: 1px solid var(--border); }
  .sig-source { font-size: 11px; }
  .sig-score { font-family: var(--font-mono); font-size: 13px; }
  .tickers { display: flex; flex-wrap: wrap; gap: 6px; }
  .ticker { font-family: var(--font-mono); font-size: 12px; padding: 3px 8px; border: 1px solid var(--border); color: var(--text); }
</style>
```

- [ ] **Step 7: Run full API tests + verify markets page in dev server**

```bash
cd /home/dev/workspace/TenBerge-OS/api && pytest tests/ -v
cd /home/dev/workspace/TenBerge-OS/web && npm run dev
```

Navigate to `http://localhost:5173/markets`. Expected: posture pill, composite score, LLM summary, signals list, watchlist tickers.

- [ ] **Step 8: Commit**

```bash
cd /home/dev/workspace/TenBerge-OS
git add api/ web/src/routes/markets/
git commit -m "feat: Markets module — MI posture, signals, watchlist"
```

---

## Task 11: Calendar Module

**Files:**
- Create: `api/routers/calendar.py`
- Create: `api/tests/test_calendar.py`
- Create: `web/src/routes/calendar/+page.svelte`

- [ ] **Step 1: Write failing test**

`api/tests/test_calendar.py`:
```python
import pytest
from unittest.mock import patch

@pytest.fixture(autouse=True)
def mock_calendar():
    with patch("services.google_calendar.get_next_event", return_value={
        "next_event_title": "Dentist", "next_event_eta_minutes": 90
    }), patch("services.google_calendar.get_upcoming_events", return_value=[
        {"title": "Dentist", "start": "2026-06-14T15:00:00", "all_day": False},
    ]):
        yield

async def test_calendar_endpoint(client):
    r = await client.get("/api/calendar/events")
    assert r.status_code == 200
    body = r.json()
    assert "events" in body
    assert isinstance(body["events"], list)
```

- [ ] **Step 2: Run test — expect FAIL**

```bash
cd /home/dev/workspace/TenBerge-OS/api && pytest tests/test_calendar.py -v
```

- [ ] **Step 3: Extend api/services/google_calendar.py with get_upcoming_events**

Add this function to the existing `google_calendar.py`:
```python
def get_upcoming_events(max_results: int = 20) -> list[dict]:
    creds = get_credentials()
    if not creds:
        return []
    from datetime import datetime, timezone
    service = build("calendar", "v3", credentials=creds, cache_discovery=False)
    now = datetime.now(timezone.utc)
    result = service.events().list(
        calendarId="primary",
        timeMin=now.isoformat(),
        maxResults=max_results,
        singleEvents=True,
        orderBy="startTime",
    ).execute()
    out = []
    for ev in result.get("items", []):
        start_raw = ev["start"].get("dateTime") or ev["start"].get("date")
        out.append({
            "title": ev.get("summary", "(No title)"),
            "start": start_raw,
            "all_day": "date" in ev["start"],
            "id": ev["id"],
        })
    return out
```

- [ ] **Step 4: Implement api/routers/calendar.py**

```python
import asyncio
from concurrent.futures import ThreadPoolExecutor
from fastapi import APIRouter
from services.google_calendar import get_upcoming_events

router = APIRouter()
_executor = ThreadPoolExecutor(max_workers=1)

@router.get("/calendar/events")
async def calendar_events():
    loop = asyncio.get_event_loop()
    events = await loop.run_in_executor(_executor, _safe_get_events)
    return {"events": events}

def _safe_get_events():
    try:
        return get_upcoming_events(max_results=20)
    except Exception:
        return []
```

- [ ] **Step 5: Run test — expect PASS**

```bash
cd /home/dev/workspace/TenBerge-OS/api && pytest tests/test_calendar.py -v
```

- [ ] **Step 6: Write web/src/routes/calendar/+page.svelte**

```svelte
<script>
  import { onMount } from 'svelte';

  let events = [];
  let loading = true;

  onMount(async () => {
    try {
      const r = await fetch('/api/calendar/events');
      const d = await r.json();
      events = d.events ?? [];
    } finally {
      loading = false;
    }
  });

  function fmtDate(raw) {
    if (!raw) return '';
    const d = new Date(raw);
    if (isNaN(d)) return raw;
    const days = ['SUN','MON','TUE','WED','THU','FRI','SAT'];
    const months = ['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC'];
    const hh = String(d.getHours()).padStart(2,'0');
    const mm = String(d.getMinutes()).padStart(2,'0');
    return `${days[d.getDay()]} ${d.getDate()} ${months[d.getMonth()]}  ${hh}:${mm}`;
  }

  function isToday(raw) {
    if (!raw) return false;
    const d = new Date(raw);
    const n = new Date();
    return d.getDate() === n.getDate() && d.getMonth() === n.getMonth();
  }
</script>

<div class="module">
  <div class="module-header">
    <a href="/" class="back-btn">← BACK</a>
    <span class="label">CALENDAR</span>
  </div>

  {#if loading}
    <p class="dim label">LOADING···</p>
  {:else if events.length === 0}
    <p class="dim label">NO UPCOMING EVENTS</p>
  {:else}
    <div class="event-list">
      {#each events as ev}
        <div class="event-row" class:today={isToday(ev.start)}>
          <div class="event-time label dim">{ev.all_day ? 'ALL DAY' : fmtDate(ev.start)}</div>
          <div class="event-title" class:accent={isToday(ev.start)}>{ev.title}</div>
        </div>
      {/each}
    </div>
  {/if}
</div>

<style>
  .module { height: 100%; overflow-y: auto; padding: 16px; display: flex; flex-direction: column; gap: 12px; }
  .module-header { display: flex; align-items: center; gap: 12px; margin-bottom: 4px; }
  .back-btn { color: var(--accent); font-family: var(--font-mono); font-size: 13px; text-decoration: none; }
  .event-list { display: flex; flex-direction: column; gap: 0; }
  .event-row { padding: 10px 0; border-bottom: 1px solid var(--border); }
  .event-row.today { border-left: 2px solid var(--accent); padding-left: 10px; }
  .event-time { font-size: 10px; margin-bottom: 3px; }
  .event-title { font-family: var(--font-mono); font-size: 13px; color: var(--text); }
  .event-title.accent { color: var(--accent); }
</style>
```

- [ ] **Step 7: Verify in dev server**

Open `http://localhost:5173/calendar`. Expected: list of upcoming events, today's events highlighted in teal, all in terminal aesthetic.

- [ ] **Step 8: Commit**

```bash
cd /home/dev/workspace/TenBerge-OS
git add api/routers/calendar.py api/services/google_calendar.py api/tests/test_calendar.py web/src/routes/calendar/
git commit -m "feat: Calendar module — Google Calendar events"
```

---

## Task 12: Feed Module

**Files:**
- Create: `api/services/rss.py`
- Create: `api/routers/feed.py`
- Create: `api/tests/test_feed.py`
- Create: `web/src/routes/feed/+page.svelte`

- [ ] **Step 1: Write api/services/rss.py**

```python
import feedparser
from datetime import datetime

DEFAULT_FEEDS = [
    ("Bloomberg Markets", "https://feeds.bloomberg.com/markets/news.rss"),
    ("Reuters Business", "https://feeds.reuters.com/reuters/businessNews"),
    ("WSJ Markets", "https://feeds.a.dj.com/rss/RSSMarketsMain.xml"),
]

def fetch_rss_items(feeds: list[tuple[str, str]] = DEFAULT_FEEDS, per_feed: int = 5) -> list[dict]:
    items = []
    for source, url in feeds:
        try:
            parsed = feedparser.parse(url)
            for entry in parsed.entries[:per_feed]:
                items.append({
                    "source": source,
                    "title": entry.get("title", ""),
                    "summary": (entry.get("summary") or "")[:200],
                    "url": entry.get("link", ""),
                    "published": entry.get("published", ""),
                })
        except Exception:
            continue
    items.sort(key=lambda x: x.get("published", ""), reverse=True)
    return items
```

- [ ] **Step 2: Write failing test**

`api/tests/test_feed.py`:
```python
import pytest
from unittest.mock import patch

MOCK_ITEMS = [
    {"source": "Bloomberg", "title": "Markets Rally", "summary": "Stocks up.", "url": "https://example.com/1", "published": ""},
    {"source": "Reuters", "title": "Fed Decision", "summary": "Rates held.", "url": "https://example.com/2", "published": ""},
]

@pytest.fixture(autouse=True)
def mock_rss():
    with patch("services.rss.fetch_rss_items", return_value=MOCK_ITEMS):
        yield

async def test_feed_returns_items(client):
    r = await client.get("/api/feed")
    assert r.status_code == 200
    body = r.json()
    assert "items" in body
    assert len(body["items"]) == 2
    assert body["items"][0]["title"] == "Markets Rally"
```

- [ ] **Step 3: Run test — expect FAIL**

```bash
cd /home/dev/workspace/TenBerge-OS/api && pytest tests/test_feed.py -v
```

- [ ] **Step 4: Implement api/routers/feed.py**

```python
import asyncio
from concurrent.futures import ThreadPoolExecutor
from fastapi import APIRouter
from services.rss import fetch_rss_items
from services.mi import get_market_posture

router = APIRouter()
_executor = ThreadPoolExecutor(max_workers=1)

@router.get("/feed")
async def get_feed():
    loop = asyncio.get_event_loop()
    rss_items, posture = await asyncio.gather(
        loop.run_in_executor(_executor, _safe_rss),
        _safe_posture(),
    )
    mi_items = _posture_to_feed_items(posture)
    return {"items": mi_items + rss_items}

def _safe_rss():
    try:
        return fetch_rss_items()
    except Exception:
        return []

async def _safe_posture():
    try:
        return await get_market_posture()
    except Exception:
        return {}

def _posture_to_feed_items(posture: dict) -> list[dict]:
    if not posture.get("llm_summary"):
        return []
    return [{
        "source": "MI DIGEST",
        "title": f"Market Posture: {posture.get('posture', 'NEUTRAL')} ({posture.get('composite_score', 0)})",
        "summary": posture.get("llm_summary", ""),
        "url": "",
        "published": posture.get("date", ""),
        "is_mi": True,
    }]
```

- [ ] **Step 5: Run test — expect PASS**

```bash
cd /home/dev/workspace/TenBerge-OS/api && pytest tests/test_feed.py -v
```

- [ ] **Step 6: Write web/src/routes/feed/+page.svelte**

```svelte
<script>
  import { onMount } from 'svelte';

  let items = [];
  let loading = true;

  onMount(async () => {
    try {
      const r = await fetch('/api/feed');
      const d = await r.json();
      items = d.items ?? [];
    } finally {
      loading = false;
    }
  });
</script>

<div class="module">
  <div class="module-header">
    <a href="/" class="back-btn">← BACK</a>
    <span class="label">FEED</span>
  </div>

  {#if loading}
    <p class="dim label">LOADING···</p>
  {:else if items.length === 0}
    <p class="dim label">NO ITEMS</p>
  {:else}
    {#each items as item}
      <div class="item" class:mi-item={item.is_mi}>
        <div class="item-meta">
          <span class="source label" class:accent={item.is_mi}>{item.source}</span>
        </div>
        {#if item.url}
          <a href={item.url} target="_blank" rel="noopener" class="item-title">{item.title}</a>
        {:else}
          <div class="item-title">{item.title}</div>
        {/if}
        {#if item.summary}
          <p class="item-summary serif dim">{item.summary}</p>
        {/if}
      </div>
    {/each}
  {/if}
</div>

<style>
  .module { height: 100%; overflow-y: auto; padding: 16px; display: flex; flex-direction: column; gap: 0; }
  .module-header { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }
  .back-btn { color: var(--accent); font-family: var(--font-mono); font-size: 13px; text-decoration: none; }
  .item { padding: 12px 0; border-bottom: 1px solid var(--border); display: flex; flex-direction: column; gap: 4px; }
  .mi-item { border-left: 2px solid var(--accent); padding-left: 12px; }
  .item-meta { display: flex; align-items: center; gap: 8px; }
  .source { font-size: 9px; }
  .item-title { font-family: var(--font-mono); font-size: 13px; color: var(--text); text-decoration: none; line-height: 1.4; }
  a.item-title:hover { color: var(--accent); }
  .item-summary { font-family: var(--font-serif); font-size: 12px; line-height: 1.5; margin-top: 2px; }
</style>
```

- [ ] **Step 7: Run all API tests**

```bash
cd /home/dev/workspace/TenBerge-OS/api && pytest tests/ -v
```

Expected: all tests PASSED.

- [ ] **Step 8: Commit**

```bash
cd /home/dev/workspace/TenBerge-OS
git add api/services/rss.py api/routers/feed.py api/tests/test_feed.py web/src/routes/feed/
git commit -m "feat: Feed module — MI digest + RSS news"
```

---

## Task 13: Docker Build + Deploy

**Files:**
- Modify: `docker-compose.yml` (if any adjustments needed)
- No new source files

- [ ] **Step 1: Build both containers**

```bash
cd /home/dev/workspace/TenBerge-OS
cp .env.example .env  # already done in Task 1 — verify values are set
docker compose build
```

Expected: both `tenberge-api` and `tenberge-web` build without errors.

- [ ] **Step 2: Start stack and verify health**

```bash
docker compose up -d
curl http://localhost:8090/api/health
```

Expected: `{"status":"ok"}`

- [ ] **Step 3: Verify home screen loads**

Open `http://10.0.1.51:8090` in browser. Expected: dark home screen with teal HUD strips and 15-app grid.

- [ ] **Step 4: Point os.austin10berge.com at port 8090**

On the server's reverse proxy (nginx or Caddy), add a vhost entry:
```nginx
server {
    server_name os.austin10berge.com;
    location / {
        proxy_pass http://localhost:8090;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

Then reload nginx: `sudo nginx -s reload`

- [ ] **Step 5: Re-run OAuth callback with production redirect URI**

Update `.env`: `GOOGLE_REDIRECT_URI=https://os.austin10berge.com/api/auth/google/callback`

Then re-authorize:
```bash
curl https://os.austin10berge.com/api/auth/google/initiate
# Open returned URL, re-authorize
```

- [ ] **Step 6: Smoke test on iPhone 12**

Open `https://os.austin10berge.com` in Safari on iPhone 12. Verify:
- Home screen fills viewport with no overflow/scroll
- Top HUD shows real MI posture + composite score + weather
- Bottom HUD shows next calendar event
- Markets module loads with live data
- Calendar module lists upcoming events
- Feed module shows MI digest + news headlines

- [ ] **Step 7: Final commit**

```bash
cd /home/dev/workspace/TenBerge-OS
git add .
git commit -m "feat: Phase 1 complete — home screen, Markets, Calendar, Feed"
```
