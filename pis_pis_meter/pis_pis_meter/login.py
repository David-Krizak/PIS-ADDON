import logging
from typing import Optional

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://mojracun.pis.com.hr"
LOGIN_URL = f"{BASE_URL}/Account/Login?ReturnUrl=%2fPromet"
LOGIN_POST_URL = f"{BASE_URL}/Account/Login"
ROOT_URL = f"{BASE_URL}/"
PROMET_URL = f"{BASE_URL}/Promet"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "hr-HR,hr;q=0.9,en-US;q=0.8,en;q=0.7",
}

logger = logging.getLogger("pis-addon.login")


def _extract_verification_token(html: str, cookies: requests.cookies.RequestsCookieJar) -> str:
    logger.debug("Trying to extract __RequestVerificationToken from HTML/cookies")
    soup = BeautifulSoup(html, "html.parser")
    token_input = soup.find("input", {"name": "__RequestVerificationToken"})
    if token_input and token_input.has_attr("value"):
        logger.debug("Found __RequestVerificationToken in HTML form")
        return token_input["value"]

    cookie_token: Optional[str] = cookies.get("__RequestVerificationToken")
    if cookie_token:
        logger.debug("Found __RequestVerificationToken in cookies")
        return cookie_token

    logger.error("Cannot find __RequestVerificationToken in HTML or cookies")
    raise RuntimeError("Cannot find __RequestVerificationToken in HTML or cookies.")


def _perform_login(session: requests.Session, username: str, password: str) -> None:
    logger.info("Starting login to PIS portal")
    response = session.get(LOGIN_URL, headers=HEADERS, allow_redirects=True)
    logger.debug("Login GET %s -> status %s, url %s", LOGIN_URL, response.status_code, response.url)
    logger.debug("Login GET response headers: %s", dict(response.headers))
    if response.status_code != 200:
        raise RuntimeError(f"Login page GET failed: {response.status_code}")

    token = _extract_verification_token(response.text, session.cookies)
    logger.debug("Got verification token, length=%s", len(token))

    payload = {
        "KorisnickoIme": username,
        "Zaporka": password,
        "__RequestVerificationToken": token,
    }

    post_headers = {
        **HEADERS,
        "Origin": BASE_URL,
        "Referer": LOGIN_URL,
        "Content-Type": "application/x-www-form-urlencoded",
    }

    login_response = session.post(
        LOGIN_POST_URL,
        data=payload,
        headers=post_headers,
        allow_redirects=True,
    )
    logger.debug(
        "Login POST %s -> status %s, url %s",
        LOGIN_POST_URL,
        login_response.status_code,
        login_response.url,
    )
    logger.debug("Login POST response headers: %s", dict(login_response.headers))

    cookies = session.cookies.get_dict()
    logger.debug("Cookies after login: keys=%s", list(cookies.keys()))
    if ".ASPXAUTH" not in cookies:
        logger.error("Login failed – .ASPXAUTH cookie missing")
        raise RuntimeError("Login failed – no .ASPXAUTH cookie, check credentials.")

    logger.info("Login successful, .ASPXAUTH cookie present")


def create_authenticated_session(username: str, password: str) -> requests.Session:
    """Return a requests session logged into the PIS portal."""

    session = requests.Session()
    session.headers.update(HEADERS)
    _perform_login(session, username, password)
    return session
