import logging
from typing import List, Tuple

import requests
from bs4 import BeautifulSoup

from .login import HEADERS, PROMET_URL, ROOT_URL

logger = logging.getLogger("pis-addon.downloader")


PageContent = Tuple[int, BeautifulSoup, str]


def _fetch_html(session: requests.Session, url: str) -> Tuple[BeautifulSoup, str]:
    logger.info("Fetching URL: %s", url)
    response = session.get(url, headers=HEADERS, allow_redirects=True)
    logger.debug("GET %s -> status %s, final url %s", url, response.status_code, response.url)
    if response.status_code != 200:
        logger.error("GET %s failed with status %s", url, response.status_code)
        raise RuntimeError(f"GET {url} failed: {response.status_code}")
    soup = BeautifulSoup(response.text, "html.parser")
    return soup, response.text


def fetch_root(session: requests.Session) -> Tuple[BeautifulSoup, str]:
    return _fetch_html(session, ROOT_URL)


def fetch_promet(session: requests.Session) -> Tuple[BeautifulSoup, str]:
    return _fetch_html(session, PROMET_URL)


def _detect_racuni_last_page(promet_soup: BeautifulSoup) -> int:
    logger.info("Detecting last racuni page from /Promet")
    tfoot = promet_soup.select_one("#racuni tfoot td")
    if not tfoot:
        logger.warning("No racuni tfoot pagination found, using page=1")
        return 1

    last_page = 1
    for anchor in tfoot.select("a[data-swhglnk='true']"):
        text = anchor.get_text(strip=True)
        if text.isdigit():
            number = int(text)
            if number > last_page:
                last_page = number

    logger.info("Detected last racuni page=%s", last_page)
    return last_page


def fetch_racuni_pages(
    session: requests.Session, promet_soup: BeautifulSoup, promet_html: str
) -> List[PageContent]:
    """Fetch all racuni pages, starting from the already-loaded /Promet soup."""

    pages: List[PageContent] = []
    pages.append((1, promet_soup, promet_html))

    last_page = _detect_racuni_last_page(promet_soup)
    for page in range(2, last_page + 1):
        url = f"{PROMET_URL}?page={page}"
        logger.info("Fetching racuni page %s: %s", page, url)
        soup, html = _fetch_html(session, url)
        pages.append((page, soup, html))

    return pages
