# Aplus Player — Web UI

Unofficial interoperability client for **apluseducation.lk**, exposed as a
Flask web app. Built for **personal use with your own paid account** — it
authenticates as you, requests only what your token authorizes, and enforces
every server-side rule the official app does.

## How it works

The official app is an Electron shell whose remote renderer talks to a
GraphQL backend and plays AES-protected HLS video. This project reimplements
that flow as two Python packages: `player/` (pure site/API interaction and
lesson processing — no Flask) and `webapp/` (the Flask web UI, including
streaming/proxy routes):

```
loginStudent (mobile+password) ──> JWT
MyCourses / GetCourseDetails   ──> pick course + lesson
GetLessonDetails               ──> link_params
GetLessonContent               ──> {key, hash}  (encrypted lesson metadata)
       │
       ▼  local decrypt chain (recovered from the official app)
AES-256-CBC unwrap ──> playback URL, per-lesson Bearer token,
                       playback_key/playback_sec/playback_hash
OTP byte-subtraction  ──> intermediate key material
AES-256-CBC unwrap    ──> 16-byte HLS video key (verified vs real segment)
       │
       ▼
either:
  browser HLS playback (via the built-in proxy routes)     or   MP4/TS download
    - decrypts playlists (CDN serves them hex-wrapped)          - fetches every segment
    - serves the reconstructed key at /api/proxy/video.key       - decrypts each with the
    - injects Authorization: Bearer on every upstream request      verified AES key
    - rewrites playlist URLs to point back at the proxy          - streams a single file to
                                                                     the browser, remuxing to
                                                                     .mp4 via ffmpeg if available
```

## Project layout

```
AplusPlayer.py      entrypoint — python3 AplusPlayer.py [--host] [--port] [--debug]
requirements.txt

player/                 lesson processing + site/API interaction — no Flask
├── __init__.py
├── config.py            endpoints, device model, crypto key ids, UA
├── crypto.py             AES-CBC + OTP decrypt routines
├── api.py                 GraphQL client (login, courses, lessons)
├── session.py              PlaybackSession — resolves + verifies the video key
├── streaming.py             playlist decrypt/rewrite logic for the HLS proxy
└── download_engine.py        segment fetch/decrypt/remux logic for downloads

webapp/                 everything HTTP-facing — pages, JSON API, streaming
├── __init__.py           Flask application factory; registers all blueprints
├── auth.py                login page + login/logout + auth-status API
├── courses.py              home redirect, course listing, course details
├── lessons.py               lesson page, lesson details, playback prepare
├── downloads.py              streams a download, using player/download_engine.py
├── proxy.py                   HLS proxy routes, using player/streaming.py
├── store.py                    shared SQLite KV store (user-scoped, worker-safe)
├── decorators.py                login_required_page / login_required_api guards
├── errors.py                     404/500 error handlers
├── static/
│   ├── css/style.css
│   └── js/main.js
└── templates/
    ├── base.html
    ├── login.html
    ├── courses.html
    ├── course.html
    ├── lesson.html
    └── error.html
```

The split is by what the code touches, down to the function level:
`player/` never imports Flask and never sees an HTTP request — it only does
GraphQL calls, decryption, playlist processing, and segment/remux work.
`webapp/` is every Flask blueprint; `downloads.py` and `proxy.py` in
particular are now thin HTTP wrappers — they handle routing, auth, request/
response objects, and progress polling, but delegate all the actual
CDN-fetching/decrypting/remuxing work to `player/download_engine.py` and
`player/streaming.py`.

## Requirements

- Python 3.10+
- `pip install -r requirements.txt`
- `ffmpeg` (optional) — only used to remux downloaded lessons from `.ts` to
  `.mp4`; without it, downloads are served as raw `.ts` files (playable in
  mpv/VLC and most players)

## Setup

Keep `AplusPlayer.py`, the `webapp/` package, and the `player/` package
together in the same directory.

Credentials are entered directly on the web login page — every user logs in
with their own Aplus account.

## Usage

```bash
python3 AplusPlayer.py                          # http://127.0.0.1:5000
python3 AplusPlayer.py --host 0.0.0.0 --port 8080 --debug
```

After logging in you're taken straight to your courses page, where you can:
- Browse all your enrolled courses
- Watch lessons in-browser (auto-prepared, HLS via `webapp/proxy.py`)
- Download lessons as `.mp4`/`.ts` (via `webapp/downloads.py`), with a live
  progress bar and cancel support

## Running as a multi-user web service

The app is built to be shared: **every user logs in with their own Aplus
account**, and their sessions, playback state, and download progress are
isolated per user and shared safely across gunicorn workers via a SQLite
store (`webapp/store.py`, default file `aplus_state.db`).

### Quick start — Docker (recommended)

```bash
# 1. Set a strong session-signing key
export FLASK_SECRET_KEY="$(openssl rand -hex 32)"

# 2. Build and run (SQLite state persists in the aplus_state volume)
docker compose up --build -d
# → http://<host>:5000
```

See `docker-compose.yml` and `.env.example` for the supported environment
variables (`FLASK_SECRET_KEY`, `APLUS_STATE_DB`, optional `GUNICORN_*`).

### Bare server — gunicorn + systemd

```bash
pip install -r requirements.txt
export FLASK_SECRET_KEY="$(openssl rand -hex 32)"
gunicorn -c gunicorn.conf.py wsgi:app
```

A minimal `systemd` unit:

```ini
[Unit]
Description=Aplus Player web service
After=network.target

[Service]
WorkingDirectory=/opt/aplus-player
Environment=FLASK_SECRET_KEY=change-me
ExecStart=/opt/aplus-player/.venv/bin/gunicorn -c gunicorn.conf.py wsgi:app
Restart=always
User=www-data
Group=www-data

[Install]
WantedBy=multi-user.target
```

Put an nginx/Caddy reverse proxy in front for TLS and to shield the app from
the public network. The app binds `0.0.0.0:5000` by default (`GUNICORN_BIND`).

### Multi-user notes

- **Login is per-account.** The form is always blank — every user enters
  their own credentials.
- **State is user-scoped.** The store keys everything by
  `<namespace>:<user>:<lesson>`, so two users watching the *same* lesson never
  clobber each other's session or download progress.
- **State is worker-safe & durable.** SQLite in WAL mode with a busy timeout is
  shared by every gunicorn worker, and the DB file persists across restarts
  (mount it on a volume in Docker).
- **Workers + threads.** `gunicorn.conf.py` runs a few worker processes with
  threaded workers so long-running downloads and the HLS proxy don't serialize
  behind one another.

## Security notes

- Video decryption keys are never exposed to the browser directly — only
  through the authenticated `/api/proxy/*` routes.
- Sessions are stored server-side via Flask's session mechanism; set
  `FLASK_SECRET_KEY` in your environment for anything beyond local/dev use.
