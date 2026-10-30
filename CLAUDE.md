# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## HARD RULES — device safety: MPPT charger & Grid Inverter (critical)

- **NEVER call any reset-type endpoint or any state-changing request on the MPPT device** (e.g. `/api/reset`, `/api/wifi/config`, `/api/wifi/scan`, OTA/update, factory/restart-like paths), not even via GET and not even "just to probe". A GET to `/api/reset` once reset the device (2026-10-02): it dropped off WiFi, lost its daily counter and part of its total counter, and needed manual intervention.
- Firmware analysis (v2.3.3, 2026-10-02) shows the ESP exposes: `/api/status`, `/api/charger/data` (safe, read-only) and `/api/reset` (ties to "[WIFI] Resetting all configs" + restart), `/api/device/reset-id` (POST, generates a NEW random device ID), `/api/mqtt/config`, `/api/wifi/config`, `/api/wifi/scan`, `/api/stm32/ota/upload`, `/update` — **all of these except the first two are forbidden.** There is no history/log endpoint in the firmware.
- The ONLY allowed requests to the MPPT's local HTTP server are the two known read-only endpoints: `GET /api/status` and `GET /api/charger/data`. Do not guess or enumerate other paths on the device; it is a small ESP that can hang or reboot under burst requests.
- **Grid Inverter** (`GTIL…` device ID, the "Grid Inverter" entry in `config.toml`'s `[[local_devices]]`, ESP v2.3.7): the only allowed requests are `GET /api/status` and `GET /api/data` (read-only; returns the `inverter` block; first read approved by the user on 2026-10-30). It answers `/api/charger/data` with 404 — `MQSolarApiClient.fetch_data()` probes that path once while detecting the endpoint, which is fine. Its firmware hasn't been analysed: assume it exposes the same forbidden endpoints as the MPPT (reset, device ID, Wi-Fi, MQTT, OTA/update) and never call them. Its `/api/status` reports `device_type: 0` just like the MPPT, so the type can't be told from it — the `inverter` key in `/api/data` is what identifies it.
- Any new device endpoint must be explicitly approved by the user first, one request at a time, never in a loop.

## Repository overview

This repo contains **two separate, independently deployable implementations** that talk to the same physical hardware (Mạnh Quân Solar MPPT charger/inverter, plus an optional ViewPower/Vertiv-Liebert UPS):

1. **`app/` + `main.py`** — a standalone FastAPI service (no Home Assistant dependency). This is the active area of development.
2. **`custom_components/local_mqsolar/`** — a Home Assistant HACS integration (config flow, coordinator, sensors) for the same devices.

`app/mqsolar_client.py` intentionally mirrors the device-communication logic in `custom_components/local_mqsolar/api.py` (local HTTP + cloud WebSocket protocol) so behavior stays consistent between the two implementations. When fixing a protocol-level bug (parsing, auth, polling behavior), check whether it applies to both.

## Commands

```bash
uv sync                              # install deps (uses uv + pyproject.toml/uv.lock)
cp config.example.toml config.toml   # required before first run

./start.sh                           # run in background, PID in mqsolar.pid, logs to mqsolar.log
./stop.sh                            # graceful stop (SIGTERM, waits 10s, then SIGKILL)
uv run python main.py                # run in foreground

./scan.sh                            # scan local /24 subnet, auto-append found devices to config.toml
```

There is no test suite, linter, or formatter configured in this repo — don't assume `pytest`/`ruff`/etc. are available.

## Architecture (standalone service in `app/`)

Everything is asyncio/FastAPI. Data flows one direction: **Poller (in-memory) → Storage (SQLite, optional) → REST API → static HTML dashboards**.

- **`config.py`** — parses `config.toml` (tomllib) into `AppConfig`. Three independent device sources can be configured simultaneously: `[[local_devices]]` (LAN HTTP), `[cloud]` (single WebSocket connection, multiple `device_ids`), `[[viewpower_devices]]` (polls a local ViewPower/Tomcat server per UPS).
- **`poller.py`** (`Poller`) — one asyncio task per device (or one shared task for all cloud devices). Keeps the latest reading per `device_id` in `self.latest` (a plain dict), which is what `/api/status` serves directly — **live data never goes through the DB**, so `/api/status` stays fast/fresh even if storage is disabled or slow.
- **`storage.py`** (`Storage`) — SQLite via `aiosqlite`, WAL mode. Two independent throttling layers, both configurable in `[polling]`:
  1. `raw_persist_interval_seconds` (default 120s) — the Poller only calls `insert_reading()` once per device per interval, regardless of `poll_interval` (default 2s). Controls *how much* raw data accumulates.
  2. `db_flush_interval_seconds` (default 600s) — inserted rows are buffered in RAM (`self._buffer`) and committed to disk in batches by `_flush_loop`, not per-insert. Controls *how often* disk writes happen. `close()` (called on graceful shutdown) flushes the buffer first, so `./stop.sh` never loses buffered data — only a hard kill can lose up to one flush interval.
  - Separately, background rollup tasks (`_run_rollup` every 600s, `_run_cleanup` every 3600s in `poller.py`) aggregate raw `readings` into `hourly_readings` and `daily_readings` for chart queries (`/api/chart`), and enforce `retention_days` / `chart_retention_days`. Rollups recompute the current *and* previous bucket each run, making them self-healing after a downtime gap.
  - Setting `storage_enabled = false` in `[polling]` disables all of the above (no inserts, no rollups, no cleanup) — `/api/history` and `/api/chart` then always return empty, but `/api/status` and the dashboards keep working from RAM only. This is the current state of the checked-in `config.toml` (used while iterating on data sources).
- **`server.py`** (`create_app`) — thin FastAPI layer over `poller.latest` and `storage`; see the README's API table for the endpoint list. `/api/devices` merges DB history (`storage.known_devices()`) with in-memory-only devices, since the DB is empty when `storage_enabled = false`.
- **`discovery.py` / `autoconfig.py`** — LAN scanning; `autoconfig.py` (used by `scan.sh`) additionally rewrites `config.toml` in place to append newly found devices under a `# --- Tự động thêm bởi app/autoconfig.py ---` marker, skipping hosts already present.
- **`app/static/index.html`** — the only page (2s auto-refresh): aggregated totals (KPI column, MPPT only) plus the UPS card with the power-flow diagram (ViewPower image + Solar branch + Grid Inverter branch) and info panels, driven by the assets in `app/static/ups/`. Non-UPS devices (MPPT charger, Grid Inverter) don't get their own page/card — `server.py` only serves `/`, there is no per-device route. See "Dashboard power-flow diagram" below before editing it.

### UPS (ViewPower) specifics

UPS load is the only ViewPower field persisted to history/charts (see the `store_data` subset built in `poller.py`'s `_run_viewpower`); everything else (battery charge, voltages, runtime, temperature) is live-only and excluded from solar power/energy totals. `nominal_watts` in `[[viewpower_devices]]` is required for load-percentage display since ViewPower itself doesn't report it.

### Dashboard power-flow diagram (`app/static/index.html`)

Rendered by `renderUpsSection()` inside the UPS card. Rules for editing it:

- **Coordinates.** Everything in `.ups-diagram-img` is absolutely positioned in % of the native 541×344 `statusbg.png`: `left`/`width` = px/541, `top`/`height` = px/344 — compute them with a small Python snippet, don't eyeball. The Grid Inverter branch overflows below the image (`top` > 100%); its space is reserved by the `.area-extra` spacer in grid row 3 (`aspect-ratio: 541/186`, `margin-top: -14px` to cancel the grid gap). 186 keeps the UPS card exactly as tall as the KPI column on its left (bottoms aligned — the user wants this); anything added below must fit in that space, and the leftover empty band is reserved for future info.
- **Native positions** (measured from the png): main UPS line y 139–155 (centre 147); Input circle x 36–103, Output circle x 437–504, centre knob x 251–289; png knobs are 23×22 (main-line ones y 136–157). The png's own Battery circle (x 236–303, y 257–324) is hidden by `.bat-old-patch` and redrawn 25 px higher by `.bat-sprite` (a crop of the same png, so it follows the online/offline background); `ov-battery-*` were shortened to match. Solar (CSS circle) sits on the battery row, centred between the left inverter line and the battery line. Inverter branch: verticals at x 3–20 / 521–538, bottom line centred at y 384 = 2 × 265.5 (battery centre) − 147, so the Solar–Battery row is exactly midway between the UPS line and the inverter line; Inverter circle 67 px at (270.5, 384), "Inverter" label 4 px below it.
- **Line style must match ViewPower.** 17 px thick; idle track colour `#c0bbc1`; active = the original green GIFs (`downVline.gif`/`upVline.gif` 17×100 vertical, `solarline.gif` 100×17 horizontal, `transform: scaleX(-1)` for right→left) stretched over the whole segment (`background-size: 100% 100%`) — tiling leaves hard seams every 100 px. Never reuse `input.gif`/`output.gif` for new segments: they have transparent holes where the png's knobs/boxes are (shows up as white/green dashes). Corners/junctions get an `.inv-knob` (CSS copy of the png knob). DOM order = paint order: background → patches → inverter flows → UPS `ov-*` GIFs → solar → battery sprite → knobs and inverter circle → label patches (knobs/circles after the lines so they cover line ends; label patches have a `var(--bg)` background, so keep them off the icons).
- **State logic.** `online` only means ViewPower answered; `upsSignal = online && workMode != ""` is the real "UPS is talking" flag (a silent UPS gives empty `workMode`, zeros/nulls and a bogus `onBattery` from `inputVoltage = 0`). Every UPS path (input, output, bypass, battery charge/discharge) is gated on `upsSignal`. The inverter branch (lines + knobs) renders only when `inverterOnline`; otherwise only a dimmed icon/label (`.off`). With the inverter online, the Output panel's V/A/W come from it (`acVoltage`, `outputPower / acVoltage`, `outputPower`), and Input voltage falls back to its `acVoltage` when there's no UPS signal. Inverter power/energy is excluded from the KPI totals (`extractMetrics` sets them to null, like the UPS).
- **Side panels** are nudged with `position: relative; top: …px` to line up with their icons (`.area-input` 56 px, `.area-solar` 58 px — keep a visible gap between them). The "Inverter information" panel (`.area-inverter`, Grid power = `limiterPower` only) shares grid area `extra` and sits just under the label via `margin-top: calc(18.47% - 8px)` (label bottom is 99.9/541 of the image width below the image); it's compact on purpose — a taller panel makes row 3 taller than the spacer and breaks the KPI alignment.
- **Verify visually** before reporting a diagram change instead of guessing coordinates. `chromium-cli` isn't installed; use Playwright via `uv run --with playwright==1.64.1 python -I script.py` (browsers are cached in `~/.cache/ms-playwright`), screenshot `.ups-diagram` at ~1500 px and ~420 px viewport width, and fake states (inverter offline, UPS `Line Mode`/`Battery Mode`) with `page.route("**/api/status", …)` — never by touching the devices. Measure png/GIF pixels with `uv run --with pillow`.

### MPPT energy: PV side vs battery side

The MPPT's own kWh counters (`powerToday`/`powerTotal`, and cloud `kwh_today`/`kwh_total`) measure the **PV side**. Measured (2026-10-02, 1 Hz integration): real energy into the battery = **0.91 ×** the counter (≈9% MPPT conversion loss). The dashboard's kWh/savings use `batToday/batMonth/batYear/batTotal`, which `poller.py` integrates from `batVoltage × batCurrent` (persisted in gitignored `mqsolar_energy.json`). Any kWh imported from the cloud or the device counter (history backfill, seeding) must be multiplied by 0.91 to stay on the same basis; hourly/daily DB rows imported that way carry `"_scaled": 0.91`.

Gaps while the machine is off are patched in two ways (`app/energy.py`, `app/cloud_sync.py`): a pause within one day is filled from the device counter's increase × 0.91 (the MPPT keeps counting); multi-day pauses/days with no local data are filled from the cloud REST stats. The cloud sync runs at startup and `sync_per_day` times a day (default 4, every 6 h) when `config.toml` has `[cloud_api] token` (a login JWT, not the WebSocket token); it also fills missing hourly rows in the DB. `[drive_backup] url/key` additionally reads the Google Drive JSON backup written by `google_apps_script/mqsolar_backup.gs` (an Apps Script web app, `doGet`, guarded by a shared secret key) in the same sync loop: it cross-checks hourly rows against local data (warns on large mismatches), fills missing hours and refreshes per-day cloud values. Per-day energy is `live + gap`, or `max(live + gap, cloud)` for flagged days, so a fully-measured day is never overwritten by the cloud.

## Config notes

`config.toml` is gitignored (contains a cloud API token and local network topology) — always edit it directly rather than assuming `config.example.toml` reflects the live setup. `mqsolar.db*`, `mqsolar.log`, `mqsolar.pid`, and `mqsolar.service` are also gitignored/host-local.

## Commit messages

Keep commit messages to a short title line only (a handful of words, imperative mood, e.g. `Add estimated battery discharge current`) — no body, no bullet points, no trailing blank lines.
