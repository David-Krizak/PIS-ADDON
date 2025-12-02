# PIS Electricity Meter Home Assistant Add-on

This repository contains a Home Assistant add-on that logs into the PIS portal (`mojracun.pis.com.hr`), scrapes finance and consumption data, and exposes it via a small Flask API.

## Repository structure
- `repository.json`: metadata so Home Assistant can register this as a custom add-on repository.
- `pis_pis_meter/`: add-on folder with `config.json`, `Dockerfile`, and Python sources (`app.py`, `scraper.py`).

## Installation in Home Assistant
1. In **Settings → Add-ons → Add-on store**, open the menu (⋮) and choose **Repositories**.
2. Add this repository URL: `https://github.com/David-Krizak/PIS-ADDON`.
3. After the repository refreshes, install **PIS Electricity Meter** from the list.
4. Open the add-on and set the **username** and **password** options (required for logging into the PIS portal).
5. Start the add-on. The API is available at `http://<home-assistant-host>:8080/data`.
6. Optional: expose Prometheus metrics from `http://<home-assistant-host>:8080/metrics` or use the health check at `/health`.

## Current functionality
- Logs into the PIS portal with the provided credentials.
- Scrapes meter readings, invoices, and yearly finance data.
- Computes basic metrics like last-period usage, average daily usage, and balance status.
- Exposes all data as JSON at the `/data` endpoint, including cache metadata and optional force-refresh via `?refresh=true`.
- Serves Prometheus-friendly metrics at `/metrics` and a health check at `/health`.

## Suggestions for further improvement
- **Home Assistant sensors/entities:** Expose balance, overpayment, and usage as native sensors (or a utility meter entity) through the Supervisor API so automations can trigger without polling the JSON endpoint. Persist last-success timestamp to `/data` for a diagnostic sensor.
- **Ingress/UI:** Add an ingress-enabled dashboard that charts invoice history and meter readings, highlights due/overdue invoices, and shows the cache age and login status.
- **Login & fetch robustness:** Add bounded retries with jitter for login/fetch steps and return a 503/`error` payload when the portal is temporarily unavailable rather than a bare 500.
- **Scheduling clarity:** Log the next planned refresh time based on `cache_ttl_seconds` and track the number of forced refreshes to detect overly aggressive clients.
- **Testing & CI:** Add unit tests for the scraper parsers (date/number helpers, invoice parsing) and wire up linting (e.g., `ruff`/`black`) in a simple GitHub Actions workflow.

## New configuration options
- `cache_ttl_seconds` (default: 86400 — once per day, min 3600, max 604800): Cache scraped data to avoid hammering the portal; set `?refresh=true` on `/data` to bypass.
- `log_level` (default: `INFO`): Adjust logging verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`).

