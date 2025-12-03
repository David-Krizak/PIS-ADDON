import json
import logging
import logging.handlers
import os
import time
from datetime import datetime
from typing import Optional, Tuple

from flask import Flask, Response, jsonify, request

from .scraper import collect_pis_data

LOG_PATH = "/data/pis_pis_meter.log"
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
RETRY_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 3

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


def _configure_logging(log_level: str):
    level = getattr(logging, log_level, logging.INFO)
    logger = logging.getLogger("pis-addon")
    logger.handlers.clear()
    logger.setLevel(level)

    formatter = logging.Formatter(LOG_FORMAT)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            LOG_PATH, maxBytes=512_000, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except Exception:
        logger.warning("Failed to initialize file logging; falling back to stdout only.")

    return logger


USERNAME, PASSWORD, CACHE_TTL_SECONDS, LOG_LEVEL = _load_options()
logger = _configure_logging(LOG_LEVEL)

_cache_data = None
_cache_timestamp = 0.0


def _fetch_data(force_refresh: bool = False) -> Tuple[dict, float, bool, Optional[str]]:
    global _cache_data, _cache_timestamp
    now = time.time()

    if not force_refresh and _cache_data and (now - _cache_timestamp) < CACHE_TTL_SECONDS:
        logger.debug(
            "Serving cached data (age=%.1fs, ttl=%ss)", now - _cache_timestamp, CACHE_TTL_SECONDS
        )
        return _cache_data, _cache_timestamp, True, None

    last_error: Optional[Exception] = None
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        start = time.time()
        try:
            logger.info("Fetching fresh data from PIS portal (force=%s, attempt=%s)", force_refresh, attempt)
            data = collect_pis_data(USERNAME, PASSWORD)
            _cache_data = data
            _cache_timestamp = time.time()
            logger.info(
                "Fetch attempt %s successful in %.2fs (cached_at=%s)",
                attempt,
                _cache_timestamp - start,
                datetime.utcfromtimestamp(_cache_timestamp).isoformat() + "Z",
            )
            return data, _cache_timestamp, False, None
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            logger.warning("Attempt %s/%s failed: %s", attempt, RETRY_ATTEMPTS, exc, exc_info=True)
            if attempt < RETRY_ATTEMPTS:
                time.sleep(RETRY_DELAY_SECONDS)

    if _cache_data:
        logger.warning("Serving cached data after failures: %s", last_error)
        return _cache_data, _cache_timestamp, True, str(last_error)

    raise last_error  # type: ignore[misc]


def _read_logs(max_lines: int = 400) -> str:
    if not os.path.exists(LOG_PATH):
        return "Log file not created yet. Trigger a fetch or wait for startup logs."

    try:
        with open(LOG_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to read log file: %s", exc)
        return "Unable to read log file. Check container permissions."

    return "".join(lines[-max_lines:])


@app.route("/data", methods=["GET"])
def get_data():
    force_refresh = request.args.get("refresh") == "true"
    try:
        data, ts, from_cache, error = _fetch_data(force_refresh=force_refresh)
        response = {
            **data,
            "cache": {
                "cached": from_cache,
                "cached_at": datetime.utcfromtimestamp(ts).isoformat() + "Z",
                "cache_ttl_seconds": CACHE_TTL_SECONDS,
            },
            "status": {
                "state": "degraded" if error else "ok",
                "error": error,
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
    try:
        _, ts, from_cache, error = _fetch_data()
        return jsonify({
            "status": "degraded" if error else "ok",
            "cached": from_cache,
            "cached_at": datetime.utcfromtimestamp(ts).isoformat() + "Z",
            "error": error,
        })
    except Exception as exc:  # noqa: BLE001
        logger.exception("Health check failed")
        return jsonify({"status": "error", "error": str(exc)}), 500


@app.route("/logs", methods=["GET"])
def logs():
    content = _read_logs()
    return Response(content, mimetype="text/plain")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
