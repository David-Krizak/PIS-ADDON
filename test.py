import os
import sys
import json
import requests
from bs4 import BeautifulSoup

BASE_URL = "https://mojracun.pis.com.hr"
# ReturnUrl can be / or /Promet, cookies are what matter
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


def get_credentials():
    username = os.getenv("PIS_USERNAME")
    password = os.getenv("PIS_PASSWORD")

    if not username:
        username = input("Korisničko ime: ").strip()
    if not password:
        password = input("Lozinka (vidljiva dok tipkaš): ")

    if not username or not password:
        print("Username or password missing.")
        sys.exit(1)

    return username, password


def extract_verification_token(html, cookies):
    soup = BeautifulSoup(html, "html.parser")
    token_input = soup.find("input", {"name": "__RequestVerificationToken"})
    if token_input and token_input.has_attr("value"):
        return token_input["value"]

    cookie_token = cookies.get("__RequestVerificationToken")
    if cookie_token:
        return cookie_token

    raise RuntimeError("Cannot find __RequestVerificationToken in HTML or cookies.")


def login(session: requests.Session, username: str, password: str) -> bool:
    print("[*] Fetching login page...")
    r = session.get(LOGIN_URL, headers=HEADERS, allow_redirects=True)
    if r.status_code != 200:
        raise RuntimeError(f"Login page GET failed: {r.status_code}")

    token = extract_verification_token(r.text, session.cookies)

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

    print("[*] Sending login POST...")
    resp = session.post(LOGIN_POST_URL, data=payload,
                        headers=post_headers, allow_redirects=True)

    cookies = session.cookies.get_dict()
    print("[*] Cookies after login:", cookies)

    if ".ASPXAUTH" not in cookies:
        if "Korisničko ime" in resp.text or "Prijava" in resp.text:
            print("[!] Probably still on login page. Check credentials.")
            return False

    return True


def fetch_page(session: requests.Session, url: str) -> BeautifulSoup:
    print(f"[*] Fetching {url} ...")
    r = session.get(url, headers=HEADERS, allow_redirects=False)

    print("[*] Initial status:", r.status_code)
    print("[*] Initial URL:", r.url)
    print("[*] Location header:", r.headers.get("Location"))

    if r.status_code in (301, 302, 303, 307, 308):
        loc = r.headers.get("Location", "")
        if "/Account/Login" in loc:
            raise RuntimeError(f"Redirected back to login when fetching {url} -> auth failed.")

        next_url = loc if loc.startswith("http") else BASE_URL + loc
        print("[*] Following redirect to:", next_url)
        r = session.get(next_url, headers=HEADERS, allow_redirects=True)
        print("[*] Final status after redirect:", r.status_code)
        print("[*] Final URL:", r.url)

    if r.status_code != 200:
        raise RuntimeError(f"{url} final GET failed: {r.status_code}")

    return BeautifulSoup(r.text, "html.parser")


# ---------------- Root / (očitanja brojila) ----------------

def parse_readings_table(soup: BeautifulSoup):
    """
    / page: div#stranicenje > table.altrowstable
    Columns: Datum, Serijski broj, Vrsta, Stanje brojila
    """
    table = soup.select_one("#stranicenje table.altrowstable")

    if not table:
        print("[!] Root: #stranicenje table.altrowstable not found.")
        print("----- ROOT PAGE PREVIEW -----")
        print(soup.text[:1000])
        print("----- END PREVIEW -----")
        return []

    headers = [th.get_text(strip=True) for th in table.select("thead th")]

    readings = []
    for tr in table.select("tbody tr"):
        cols = [td.get_text(strip=True) for td in tr.find_all("td")]
        if not cols:
            continue
        row = dict(zip(headers, cols))
        readings.append(row)

    return readings


# ---------------- /Promet: table + summary ----------------

def parse_promet_table(soup: BeautifulSoup):
    """
    /Promet: div#stranicenje > table.altrowstable
    Columns: Datum, Opis, Zaduženje, Uplata
    """
    table = soup.select_one("#tabularniPodaci #stranicenje table.altrowstable")
    if not table:
        # some layouts might not have tabularniPodaci wrapper, try fallback
        table = soup.select_one("#stranicenje table.altrowstable")

    if not table:
        print("[!] Promet: #stranicenje table.altrowstable not found.")
        print("----- PROMET PAGE PREVIEW -----")
        print(soup.text[:1000])
        print("----- END PREVIEW -----")
        return []

    headers = [th.get_text(strip=True) for th in table.select("thead th")]

    rows = []
    for tr in table.select("tbody tr"):
        cols = [td.get_text(strip=True) for td in tr.find_all("td")]
        if not cols:
            continue
        row = dict(zip(headers, cols))
        rows.append(row)

    return rows


def _parse_euro_amount(text: str):
    """
    Convert '531,14 €' or '131,94' to float 531.14 / 131.94.
    Returns None on failure.
    """
    if not text:
        return None
    t = text.replace("€", "").replace("EUR", "").strip()
    # remove thousands dots, replace comma with dot
    t = t.replace(".", "").replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return None


def parse_promet_summary(soup: BeautifulSoup):
    """
    /Promet: div.summary – key/value pairs.
    Example labels:
      - Dug iz prethodnog razdoblja
      - Ukupno zaduženje
      - Ukupno akontacije
      - Ukupna uplata
      - U preplati ste u iznosu od
    """
    summary_div = soup.select_one("div.summary")
    if not summary_div:
        print("[!] Promet: div.summary not found.")
        return {}

    summary = {}
    for tr in summary_div.select("table tr"):
        tds = tr.select("td")
        if len(tds) < 2:
            continue
        label_el = tds[0].select_one(".summary-item label")
        value_el = tds[1].select_one(".summary-item")
        if not label_el or not value_el:
            continue

        label = label_el.get_text(strip=True)
        raw_value = value_el.get_text(strip=True)
        num_value = _parse_euro_amount(raw_value)

        summary[label] = {
            "raw": raw_value,
            "value": num_value,
        }

    return summary


# ---------------- main ----------------

def main():
    username, password = get_credentials()

    with requests.Session() as session:
        session.headers.update(HEADERS)

        if not login(session, username, password):
            print("Login failed. Check credentials.")
            sys.exit(1)

        # 1) Root readings
        root_soup = fetch_page(session, ROOT_URL)
        readings = parse_readings_table(root_soup)

        # 2) Promet page
        promet_soup = fetch_page(session, PROMET_URL)
        promet_rows = parse_promet_table(promet_soup)
        promet_summary = parse_promet_summary(promet_soup)

        # Compose final result
        result = {
            "readings": readings,           # from /
            "readings_latest": readings[0] if readings else None,
            "promet": promet_rows,          # from /Promet
            "promet_latest": promet_rows[0] if promet_rows else None,
            "promet_summary": promet_summary,
        }

        # Print as JSON for now (ideal for HA command_line sensor later)
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
