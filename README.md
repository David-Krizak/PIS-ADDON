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
5. Start the add-on. The API is available at `http://<home-assistant-host>:8080/data` and the log viewer at `/logs`.
6. A lightweight health check is available at `/health`.

## Current functionality
- Logs into the PIS portal with the provided credentials.
- Scrapes meter readings, invoices, and yearly finance data.
- Computes basic metrics like last-period usage, average daily usage, and balance status.
- Exposes all data as JSON at the `/data` endpoint, including cache metadata, optional force-refresh via `?refresh=true`, and status indicators for degraded responses when the scraper falls back to cached data.
- Includes an add-on log viewer at `/logs` and a health check at `/health`.

## Suggestions for further improvement
- **Home Assistant sensors:** Publish balance/usage as native sensors via the Supervisor API instead of polling JSON directly.
- **Ingress/UI:** Add an ingress-enabled dashboard to visualize invoices, trends, and errors inside Home Assistant.
- **Testing & CI:** Add unit tests around the scraper parsing functions and a lint pipeline to catch regressions.

## New configuration options
- `cache_ttl_seconds` (default: 86400 — once per day, min 3600, max 604800): Cache scraped data to avoid hammering the portal; set `?refresh=true` on `/data` to bypass.
- `log_level` (default: `INFO`): Adjust logging verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`).

