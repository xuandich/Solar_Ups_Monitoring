# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

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
- **`static/index.html`** — the only page (2s auto-refresh): aggregated totals (KPI row) plus a UPS power-flow diagram (Input → Rectifier → Inverter → Output, Battery branch, Bypass indicator, Solar→Battery charging branch) driven by the assets in `static/ups/`. Non-UPS devices (e.g. the MPPT charger) no longer get their own page/card — `server.py` only serves `/`, there is no per-device route.

### UPS (ViewPower) specifics

UPS load is the only ViewPower field persisted to history/charts (see the `store_data` subset built in `poller.py`'s `_run_viewpower`); everything else (battery charge, voltages, runtime, temperature) is live-only and excluded from solar power/energy totals. `nominal_watts` in `[[viewpower_devices]]` is required for load-percentage display since ViewPower itself doesn't report it.

## Config notes

`config.toml` is gitignored (contains a cloud API token and local network topology) — always edit it directly rather than assuming `config.example.toml` reflects the live setup. `mqsolar.db*`, `mqsolar.log`, `mqsolar.pid`, and `mqsolar.service` are also gitignored/host-local.
