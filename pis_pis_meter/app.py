import json
import logging
import time
from datetime import datetime

from flask import Flask, Response, jsonify, request

from scraper import collect_pis_data

app = Flask(__name__)


def _load_options():
    with open("/data/options.json", "r", encoding="utf-8") as f:
        opts = json.load(f)

    username = opts.get("username")
    password = opts.get("password")
    cache_ttl = opts.get("cache_ttl_seconds", 86400)
    log_level = (opts.get("log_level") or "INFO").upper()

    if not username or not password:
        raise RuntimeError("username/password not set in add-on options.")

    if cache_ttl < 3600:
        raise RuntimeError(
            "cache_ttl_seconds must be at least 3600 seconds (1 hour) to avoid excessive logins."
        )

    return username, password, cache_ttl, log_level


USERNAME, PASSWORD, CACHE_TTL_SECONDS, LOG_LEVEL = _load_options()

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("pis-addon")

_cache_data = None
_cache_timestamp = 0.0


def _fetch_data(force_refresh: bool = False):
    global _cache_data, _cache_timestamp
    now = time.time()

    if not force_refresh and _cache_data and (now - _cache_timestamp) < CACHE_TTL_SECONDS:
        return _cache_data, _cache_timestamp, True

    logger.info("Fetching fresh data from PIS portal (force=%s)", force_refresh)
    data = collect_pis_data(USERNAME, PASSWORD)
    _cache_data = data
    _cache_timestamp = time.time()
    return data, _cache_timestamp, False


def _prometheus_metrics(data: dict, cached_at: float, from_cache: bool) -> str:
    lines = [
        "# HELP pis_cache_age_seconds Age of cached data in seconds.",
        "# TYPE pis_cache_age_seconds gauge",
        f"pis_cache_age_seconds {{cached=\"{str(from_cache).lower()}\"}} {max(0, int(time.time() - cached_at))}",
    ]

    finance = data.get("finance", {})
    summary = finance.get("summary", {})
    metrics = {
        "balance": summary.get("balance"),
        "overpayment": summary.get("overpayment"),
        "last_invoice_days_until_due": finance.get("last_invoice_days_until_due"),
        "year_charges": finance.get("year_stats", {}).get("charges"),
        "year_payments": finance.get("year_stats", {}).get("payments"),
    }

    for key, value in metrics.items():
        if value is None:
            continue
        lines.append(f"pis_{key} {float(value)}")

    return "\n".join(lines) + "\n"


@app.route("/data", methods=["GET"])
def get_data():
    force_refresh = request.args.get("refresh") == "true"
    try:
        data, ts, from_cache = _fetch_data(force_refresh=force_refresh)
        response = {
            **data,
            "cache": {
                "cached": from_cache,
                "cached_at": datetime.utcfromtimestamp(ts).isoformat() + "Z",
                "cache_ttl_seconds": CACHE_TTL_SECONDS,
            },
        }
        return jsonify(response)
    except Exception:
        logger.exception("Failed to collect PIS data")
        return jsonify({
            "error": "Failed to collect data from PIS portal. Check credentials and connectivity.",
        }), 500


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@app.route("/metrics", methods=["GET"])
def metrics():
    try:
        data, ts, from_cache = _fetch_data()
        body = _prometheus_metrics(data, ts, from_cache)
        return Response(body, mimetype="text/plain; version=0.0.4")
    except Exception:
        logger.exception("Failed to render metrics")
        return Response("", status=500, mimetype="text/plain")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
