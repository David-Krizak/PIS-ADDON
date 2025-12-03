import json
import logging
from typing import Dict, List, Optional

import requests

from .downloader import fetch_promet, fetch_racuni_pages, fetch_root
from .login import create_authenticated_session
from .parser import (
    build_portal_payload,
    parse_promet_summary,
    parse_promet_table,
    parse_racuni,
    parse_racuni_period,
    parse_root_readings,
)

logger = logging.getLogger("pis-addon.scraper")


def _close_session(session: requests.Session) -> None:
    try:
        session.close()
    except Exception:
        logger.debug("Session close failed", exc_info=True)


def collect_pis_data(username: str, password: str) -> dict:
    """Login, download relevant pages, parse them and return a simplified payload."""

    logger.info("collect_pis_data: starting scrape for PIS portal")
    session = create_authenticated_session(username, password)
    try:
        root_soup, _ = fetch_root(session)
        promet_soup, promet_html = fetch_promet(session)
        racuni_pages = fetch_racuni_pages(session, promet_soup, promet_html)

        logger.debug(
            "Downloaded pages: root_ok=%s promet_len=%s racuni_pages=%s",
            bool(root_soup),
            len(promet_html),
            len(racuni_pages),
        )

        readings = parse_root_readings(root_soup)
        promet_rows = parse_promet_table(promet_soup)
        summary = parse_promet_summary(promet_soup)

        racuni_period: Optional[Dict] = None
        invoices: List[Dict] = []
        for page, soup, html in racuni_pages:
            logger.debug("Parsing racuni page %s (len=%s)", page, len(html))
            if racuni_period is None:
                racuni_period = parse_racuni_period(soup)
            invoices.extend(parse_racuni(soup))

        result = build_portal_payload(readings, promet_rows, summary, invoices, racuni_period)
        logger.info(
            "collect_pis_data: done. readings=%s, promet_rows=%s, invoices=%s",
            len(readings),
            len(promet_rows),
            len(invoices),
        )
        return result
    finally:
        _close_session(session)


if __name__ == "__main__":
    import os

    logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    username = os.getenv("PIS_USERNAME")
    password = os.getenv("PIS_PASSWORD")
    if not username or not password:
        raise SystemExit("Set PIS_USERNAME and PIS_PASSWORD")

    data = collect_pis_data(username, password)
    print(json.dumps(data, ensure_ascii=False, indent=2))
