import json
import re
import logging
from datetime import datetime, date

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

HR_MONTHS = {
    "siječnja": 1,
    "veljače": 2,
    "ožujka": 3,
    "ozujka": 3,
    "travnja": 4,
    "svibnja": 5,
    "lipnja": 6,
    "srpnja": 7,
    "kolovoza": 8,
    "rujna": 9,
    "listopada": 10,
    "studenog": 11,
    "prosinca": 12,
}

# module logger (inherits handlers from "pis-addon" logger in app.py)
logger = logging.getLogger("pis-addon.scraper")


# ---------- low-level helpers ----------

def _extract_verification_token(html, cookies):
    logger.debug("Trying to extract __RequestVerificationToken from HTML/cookies")
    soup = BeautifulSoup(html, "html.parser")
    token_input = soup.find("input", {"name": "__RequestVerificationToken"})
    if token_input and token_input.has_attr("value"):
        logger.debug("Found __RequestVerificationToken in HTML form")
        return token_input["value"]

    cookie_token = cookies.get("__RequestVerificationToken")
    if cookie_token:
        logger.debug("Found __RequestVerificationToken in cookies")
        return cookie_token

    logger.error("Cannot find __RequestVerificationToken in HTML or cookies")
    raise RuntimeError("Cannot find __RequestVerificationToken in HTML or cookies.")


def _login(session: requests.Session, username: str, password: str) -> None:
    logger.info("Starting login to PIS portal")
    r = session.get(LOGIN_URL, headers=HEADERS, allow_redirects=True)
    logger.debug("Login GET %s -> status %s, url %s", LOGIN_URL, r.status_code, r.url)
    if r.status_code != 200:
        raise RuntimeError(f"Login page GET failed: {r.status_code}")

    token = _extract_verification_token(r.text, session.cookies)
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

    resp = session.post(
        LOGIN_POST_URL,
        data=payload,
        headers=post_headers,
        allow_redirects=True,
    )
    logger.debug(
        "Login POST %s -> status %s, url %s",
        LOGIN_POST_URL,
        resp.status_code,
        resp.url,
    )

    cookies = session.cookies.get_dict()
    logger.debug("Cookies after login: keys=%s", list(cookies.keys()))
    if ".ASPXAUTH" not in cookies:
        logger.error("Login failed – .ASPXAUTH cookie missing")
        raise RuntimeError("Login failed – no .ASPXAUTH cookie, check credentials.")
    logger.info("Login successful, .ASPXAUTH cookie present")


def _fetch(session: requests.Session, url: str) -> BeautifulSoup:
    logger.info("Fetching URL: %s", url)
    r = session.get(url, headers=HEADERS, allow_redirects=True)
    logger.debug("GET %s -> status %s, final url %s", url, r.status_code, r.url)
    if r.status_code != 200:
        logger.error("GET %s failed with status %s", url, r.status_code)
        raise RuntimeError(f"GET {url} failed: {r.status_code}")
    return BeautifulSoup(r.text, "html.parser")


def _fetch_racuni_last_page(session: requests.Session) -> BeautifulSoup:

    logger.info("Fetching /Promet to detect last racuni page")
    soup = _fetch(session, PROMET_URL)

    tfoot = soup.select_one("#racuni tfoot td")
    if not tfoot:
        logger.warning("No racuni tfoot pagination found, using base /Promet page")
        return soup

    last_page = None
    for a in tfoot.select("a[data-swhglnk='true']"):
        text = a.get_text(strip=True)
        if text.isdigit():
            num = int(text)
            if last_page is None or num > last_page:
                last_page = num

    if not last_page:
        logger.warning("Could not detect last page number, using base /Promet page")
        return soup

    # Paginacija koristi query ?page=X na istom endpointu
    last_url = f"{PROMET_URL}?page={last_page}"
    logger.info("Detected last racuni page=%s, fetching %s", last_page, last_url)
    return _fetch(session, last_url)


def _parse_euro_amount(text: str):
    """
    Convert '531,14 €' or '131,94' or '171,20EUR' to float.
    Returns None on failure.
    """
    if not text:
        return None
    t = text.replace("€", "").replace("EUR", "").strip()
    t = t.replace(" ", "").replace(".", "").replace(",", ".")
    try:
        val = float(t)
        return val
    except ValueError:
        logger.debug("Failed to parse euro amount from %r", text)
        return None


def _parse_int_reading(value: str):
    """
    '1.840' / '1840' / '1 840' -> 1840, returns None on failure.
    """
    if value is None:
        return None
    t = value.replace(".", "").replace(" ", "").strip()
    try:
        val = int(t)
        return val
    except ValueError:
        logger.debug("Failed to parse int reading from %r", value)
        return None


def _parse_hr_long_date(s: str):
    """
    Parse dates like '27. studenog 2025.' or '30. siječnja 2025.' -> date
    """
    if not s:
        return None
    s = s.strip()
    m = re.match(r"(\d{1,2})\.\s+([A-Za-zčćšđžČĆŠĐŽ]+)\s+(\d{4})\.?", s)
    if not m:
        logger.debug("Failed to match long HR date pattern for %r", s)
        return None
    day = int(m.group(1))
    month_name = m.group(2).lower()
    year = int(m.group(3))
    month = HR_MONTHS.get(month_name)
    if not month:
        logger.debug("Unknown HR month name %r in %r", month_name, s)
        return None
    try:
        return date(year, month, day)
    except ValueError:
        logger.debug("Invalid date values %s-%s-%s from %r", year, month, day, s)
        return None


def _parse_hr_short_date(s: str):
    """
    Parse '31.01.2025' or '31.1.2025' -> date
    """
    if not s:
        return None
    s = s.strip().replace(" ", "")
    for fmt in ("%d.%m.%Y", "%d.%m.%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    logger.debug("Failed to parse short HR date from %r", s)
    return None


# ---------- parsing HTML ----------

def _parse_root_readings(soup: BeautifulSoup):
    """
    / page: div#stranicenje > table.altrowstable
    Columns: Datum, Serijski broj, Vrsta, Stanje brojila
    """
    table = soup.select_one("#stranicenje table.altrowstable")
    if not table:
        logger.warning("Could not find readings table on root page")
        return []

    headers = [th.get_text(strip=True) for th in table.select("thead th")]
    logger.debug("Root readings table headers: %s", headers)
    readings = []
    for tr in table.select("tbody tr"):
        cols = [td.get_text(strip=True) for td in tr.find_all("td")]
        if not cols:
            continue
        row = dict(zip(headers, cols))

        row_date = _parse_hr_long_date(row.get("Datum"))
        row_value = _parse_int_reading(row.get("Stanje brojila"))
        row["parsed_date"] = row_date.isoformat() if row_date else None
        row["parsed_value"] = row_value

        readings.append(row)

    logger.info("Parsed %s readings from root page", len(readings))
    return readings


def _parse_promet_table(soup: BeautifulSoup):
    """
    /Promet: div#tabularniPodaci #stranicenje table.altrowstable
    Columns: Datum, Opis, Zaduženje, Uplata
    """
    table = soup.select_one("#tabularniPodaci #stranicenje table.altrowstable")
    if not table:
        table = soup.select_one("#stranicenje table.altrowstable")
    if not table:
        logger.warning("Could not find promet table on /Promet page")
        return []

    headers = [th.get_text(strip=True) for th in table.select("thead th")]
    logger.debug("Promet table headers: %s", headers)
    rows = []
    for tr in table.select("tbody tr"):
        cols = [td.get_text(strip=True) for td in tr.find_all("td")]
        if not cols:
            continue
        row = dict(zip(headers, cols))

        d = _parse_hr_long_date(row.get("Datum"))
        z = _parse_euro_amount(row.get("Zaduženje"))
        u = _parse_euro_amount(row.get("Uplata"))

        row["parsed_date"] = d.isoformat() if d else None
        row["zaduzenje_value"] = z
        row["uplata_value"] = u

        rows.append(row)

    logger.info("Parsed %s rows from promet table", len(rows))
    return rows


def _parse_promet_summary(soup: BeautifulSoup):
    """
    div.summary – key/value pairs.
    """
    summary_div = soup.select_one("div.summary")
    if not summary_div:
        logger.warning("Could not find summary div on /Promet page")
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
    logger.info("Parsed %s items from promet summary", len(summary))
    return summary


def _parse_racuni(soup: BeautifulSoup):
    """
    /Promet?page=X: section #racuni – table with invoice details + barcode.
    Each row has:
      - <td class="racunLijevo"> ... tekst ... </td>
      - <td class="barcodeCentar"><img src="data:image/png;base64,..."></td>
    """
    invoices = []
    table = soup.select_one("#racuni table.altrowstable")
    if not table:
        logger.warning("Could not find racuni table on /Promet page")
        return invoices

    for tr in table.select("tbody tr"):
        left = tr.select_one("td.racunLijevo")
        right = tr.select_one("td.barcodeCentar")
        if not left:
            continue

        text = left.get_text("\n", strip=True)
        inv = {}
        for line in text.split("\n"):
            line = line.strip()
            if not line:
                continue
            if line.startswith("Broj računa:"):
                inv["number"] = line.split(":", 1)[1].strip()
            elif line.startswith("Opis računa:"):
                inv["description"] = line.split(":", 1)[1].strip()
            elif line.startswith("Datum računa:"):
                d = line.split(":", 1)[1].strip()
                inv["issue_date_raw"] = d
                parsed = _parse_hr_short_date(d)
                inv["issue_date"] = parsed.isoformat() if parsed else None
            elif line.startswith("Datum valute:"):
                d = line.split(":", 1)[1].strip()
                inv["due_date_raw"] = d
                parsed = _parse_hr_short_date(d)
                inv["due_date"] = parsed.isoformat() if parsed else None
            elif line.startswith("Prodajno mjesto:"):
                inv["place"] = line.split(":", 1)[1].strip()
            elif line.startswith("Poslovni partner:"):
                inv["partner"] = line.split(":", 1)[1].strip()
            elif line.startswith("Model i poziv na broj:"):
                inv["model_poziv"] = line.split(":", 1)[1].strip()
            elif line.startswith("IBAN:"):
                inv["iban"] = line.split(":", 1)[1].strip()
            elif "Iznos:" in line:
                amt = line.split(":", 1)[1].strip()
                inv["amount_raw"] = amt
                inv["amount"] = _parse_euro_amount(amt)

        # barcode slika
        if right:
            img = right.find("img")
            if img and img.has_attr("src"):
                inv["barcode_src"] = img["src"]  # data:image/png;base64,...

        if inv:
            invoices.append(inv)

    logger.info("Parsed %s invoices from racuni section", len(invoices))
    return invoices


def _parse_racuni_period(soup: BeautifulSoup):
    """
    Parse 'Prikazuju se računi za period: 31.1.2025. - 2.12.2025.'
    from <div id="racuni"><div style>...</div>...
    """
    container = soup.select_one("#racuni > div")
    if not container:
        logger.debug("No racuni period header found")
        return None

    text = container.get_text(" ", strip=True)
    m = re.search(r"period:\s*(.+?)\s*-\s*(.+)$", text)
    if not m:
        logger.debug("Failed to parse racuni period from text: %r", text)
        return None

    start_raw = m.group(1).strip()
    end_raw = m.group(2).strip()

    start_date = _parse_hr_short_date(start_raw)
    end_date = _parse_hr_short_date(end_raw)

    result = {
        "raw": text,
        "start_raw": start_raw,
        "end_raw": end_raw,
        "start": start_date.isoformat() if start_date else None,
        "end": end_date.isoformat() if end_date else None,
    }
    logger.info("Parsed racuni period: %s", result)
    return result


def _enrich_invoices_with_payments(invoices, promet_rows):
    """
    Pokuša povezati racune (iz #racuni) s prometom (zaduženja/uplate)
    na osnovi iznosa i datuma.

    Heuristika:
      - nađi zaduženje (zaduzenje_value > 0) ≈ amount racuna
      - nađi uplatu (uplata_value > 0) ≈ amount nakon tog zaduženja
      - ako postoji uplata, invoice.paid=True, payment_date=...
    """
    logger.info("Matching %s invoices with %s promet rows", len(invoices), len(promet_rows))

    EPS = 0.01  # tolerance za float usporedbu

    charge_rows = []
    payment_rows = []
    for row in promet_rows:
        z = row.get("zaduzenje_value") or 0.0
        u = row.get("uplata_value") or 0.0
        d_str = row.get("parsed_date")
        d = datetime.fromisoformat(d_str).date() if d_str else None

        if z > 0:
            charge_rows.append((row, d, z))
        if u > 0:
            payment_rows.append((row, d, u))

    for inv in invoices:
        amount = inv.get("amount")
        issue_date = None
        if inv.get("issue_date"):
            try:
                issue_date = datetime.fromisoformat(inv["issue_date"]).date()
            except Exception:
                issue_date = None

        inv["paid"] = False
        inv["payment_date"] = None
        inv["payment_amount"] = None

        if amount is None:
            continue

        # nađi najbliže zaduženje po iznosu (+blizina datuma)
        best_charge = None
        best_charge_score = None
        for row, d, z in charge_rows:
            diff = abs(z - amount)
            if diff > EPS:
                continue
            if issue_date and d:
                date_penalty = abs((d - issue_date).days)
            else:
                date_penalty = 0
            score = diff + date_penalty / 100.0
            if best_charge is None or score < best_charge_score:
                best_charge = (row, d, z)
                best_charge_score = score

        if not best_charge:
            logger.debug(
                "No matching charge row found for invoice %s amount %.2f",
                inv.get("number"), amount
            )
            continue

        _, charge_date, _ = best_charge

        # nađi uplatu za isti iznos nakon (ili istog dana) zaduženja
        best_payment = None
        for row, d, u in payment_rows:
            if d and charge_date and d < charge_date:
                continue
            if abs(u - amount) > EPS:
                continue
            best_payment = (row, d, u)
            break

        if best_payment:
            row, d, u = best_payment
            inv["paid"] = True
            inv["payment_date"] = d.isoformat() if d else None
            inv["payment_amount"] = u
            logger.debug(
                "Invoice %s matched as PAID on %s amount=%.2f",
                inv.get("number"),
                inv["payment_date"],
                u,
            )
        else:
            logger.debug(
                "Invoice %s appears UNPAID (no matching payment for amount %.2f)",
                inv.get("number"),
                amount,
            )


# ---------- metrics ----------

def _compute_finance_metrics(promet_rows, summary, invoices):
    today = date.today()
    logger.info("Computing finance metrics for %s promet rows and %s invoices",
                len(promet_rows), len(invoices))

    dug_prev = summary.get("Dug iz prethodnog razdoblja", {}).get("value") or 0.0
    ukupno_zaduzenje = summary.get("Ukupno zaduženje", {}).get("value") or 0.0
    ukupna_uplata = summary.get("Ukupna uplata", {}).get("value") or 0.0

    # + = dug, - = preplata (float tolerancija)
    balance = dug_prev + ukupno_zaduzenje - ukupna_uplata
    EPS = 0.005
    if abs(balance) < EPS:
        balance = 0.0
        status = "podmireno"
    elif balance > 0:
        status = "dug"
    else:
        status = "preplata"

    overpayment_value = -balance if balance < 0 else 0.0

    logger.debug(
        "Finance summary: dug_prev=%.2f, ukupno_zaduzenje=%.2f, ukupna_uplata=%.2f, "
        "balance=%.2f, status=%s, overpayment=%.2f",
        dug_prev, ukupno_zaduzenje, ukupna_uplata,
        balance, status, overpayment_value,
    )

    # ---------------- GODIŠNJA STATISTIKA ----------------
    current_year = today.year
    year_charges = 0.0
    year_payments = 0.0
    charge_months = {}  # yyyy-mm -> amount

    for row in promet_rows:
        d_str = row.get("parsed_date")
        if not d_str:
            continue
        d = datetime.fromisoformat(d_str).date()
        z = row.get("zaduzenje_value") or 0.0
        u = row.get("uplata_value") or 0.0

        if d.year == current_year:
            year_charges += z
            year_payments += u
            if z > 0:
                key = f"{d.year}-{d.month:02d}"
                charge_months[key] = charge_months.get(key, 0.0) + z

    sorted_months = sorted(charge_months.items(), key=lambda x: x[0], reverse=True)
    recent = sorted_months[:6]
    avg_recent_bill = None
    if recent:
        avg_recent_bill = sum(v for _, v in recent) / len(recent)

    # ---------------- ZADNJI RAČUN ----------------
    # Sortiramo račune po issue_date (datum računa), fallback na payment_date.
    def _inv_sort_key(inv):
        issue = inv.get("issue_date") or ""
        payment = inv.get("payment_date") or ""
        return issue or payment

    sorted_invoices = sorted(
        invoices,
        key=_inv_sort_key,
        reverse=True,
    )

    last_invoice = sorted_invoices[0] if sorted_invoices else None

    # Fallback: ako uopće nemamo racune, koristi prvo zaduženje iz promet
    if not last_invoice:
        for row in promet_rows:
            z = row.get("zaduzenje_value") or 0.0
            if z <= 0:
                continue
            d_str = row.get("parsed_date")
            d = datetime.fromisoformat(d_str).date() if d_str else None
            last_invoice = {
                "number": row.get("Opis"),
                "description": row.get("Opis"),
                "issue_date": d.isoformat() if d else None,
                "due_date": None,
                "amount": z,
                "amount_raw": row.get("Zaduženje"),
            }
            logger.debug("Fallback last_invoice inferred from promet: %s", last_invoice)
            break

    # ---------------- NEPLAĆENI RAČUNI -------------
    unpaid_invoices = [inv for inv in invoices if not inv.get("paid")]
    unpaid_total = sum((inv.get("amount") or 0.0) for inv in unpaid_invoices)
    paid_invoices = [inv for inv in invoices if inv.get("paid")]

    logger.debug(
        "Year stats: year=%s, charges=%.2f, payments=%.2f, avg_recent_bill=%s, "
        "unpaid_count=%s, unpaid_total=%.2f",
        current_year, year_charges, year_payments,
        f"{avg_recent_bill:.2f}" if avg_recent_bill is not None else "None",
        len(unpaid_invoices), unpaid_total,
    )

    finance = {
        "summary": {
            "dug_prethodno": dug_prev,
            "ukupno_zaduzenje": ukupno_zaduzenje,
            "ukupna_uplata": ukupna_uplata,
            "balance": balance,          # + = dug, - = preplata
            "status": status,            # 'dug' / 'preplata' / 'podmireno'
            "overpayment": overpayment_value,
            "raw_summary": summary,
        },
        "last_invoice": last_invoice,          # koristi se za sensor.pis_zadnji_racun_iznos
        "year_stats": {
            "year": current_year,
            "charges": year_charges,
            "payments": year_payments,
            "avg_recent_bill": avg_recent_bill,
        },
        "invoices": invoices,
        "unpaid_invoices_count": len(unpaid_invoices),
        "unpaid_invoices_total": unpaid_total,
        "paid_invoices_count": len(paid_invoices),
    }
    return finance


def _compute_consumption_metrics(readings):
    """
    readings: list from _parse_root_readings (already has parsed_date/parsed_value)
    """
    today = date.today()
    logger.info("Computing consumption metrics from %s readings", len(readings))

    current = readings[0] if len(readings) >= 1 else None
    previous = readings[1] if len(readings) >= 2 else None

    current_value = current.get("parsed_value") if current else None
    previous_value = previous.get("parsed_value") if previous else None

    current_date = (
        datetime.fromisoformat(current["parsed_date"]).date()
        if current and current.get("parsed_date") else None
    )
    previous_date = (
        datetime.fromisoformat(previous["parsed_date"]).date()
        if previous and previous.get("parsed_date") else None
    )

    last_period_usage = None
    days_between = None
    avg_daily_last_period = None

    if (
        current_value is not None
        and previous_value is not None
        and current_date
        and previous_date
    ):
        last_period_usage = current_value - previous_value
        days_between = (current_date - previous_date).days
        if days_between > 0:
            avg_daily_last_period = last_period_usage / days_between

    days_since_last_reading = None
    if current_date:
        days_since_last_reading = (today - current_date).days

    # APROKS CIJENE ZADNJEG PERIODA (€/kWh)
    PRICE_PER_KWH = 0.53
    approx_last_period_cost = None
    if last_period_usage is not None:
        approx_last_period_cost = last_period_usage * PRICE_PER_KWH

    logger.debug(
        "Consumption metrics: current_value=%s, previous_value=%s, "
        "last_period_usage=%s, days_between=%s, avg_daily_last_period=%s, "
        "days_since_last_reading=%s, approx_last_period_cost=%s",
        current_value, previous_value,
        last_period_usage, days_between,
        avg_daily_last_period, days_since_last_reading,
        approx_last_period_cost,
    )

    consumption = {
        "last_reading": current,
        "previous_reading": previous,
        "last_period_usage": last_period_usage,
        "days_between_last_two_readings": days_between,
        "avg_daily_usage_last_period": avg_daily_last_period,
        "days_since_last_reading": days_since_last_reading,
        "current_value": current_value,
        "previous_value": previous_value,
        "approx_last_period_cost": approx_last_period_cost,
    }
    return consumption


# ---------- main entry ----------

def collect_pis_data(username: str, password: str) -> dict:
    """
    Main entry: logs in, fetches / and /Promet, returns structured dict.
    """
    logger.info("collect_pis_data: starting scrape for PIS portal")
    with requests.Session() as session:
        session.headers.update(HEADERS)
        _login(session, username, password)

        root_soup = _fetch(session, ROOT_URL)
        promet_soup = _fetch(session, PROMET_URL)
        racuni_last_soup = _fetch_racuni_last_page(session)

        readings = _parse_root_readings(root_soup)
        promet = _parse_promet_table(promet_soup)
        summary = _parse_promet_summary(promet_soup)

        # računi i period s ZADNJE stranice
        invoices = _parse_racuni(racuni_last_soup)
        racuni_period = _parse_racuni_period(racuni_last_soup)

        # poveži račune s plaćanjima
        _enrich_invoices_with_payments(invoices, promet)

        finance = _compute_finance_metrics(promet, summary, invoices)
        consumption = _compute_consumption_metrics(readings)

        result = {
            "finance": finance,
            "consumption": consumption,
            "raw": {
                "readings": readings,
                "promet": promet,
                "promet_summary": summary,
                "invoices": invoices,
                "racuni_period": racuni_period,
            },
        }

        logger.info(
            "collect_pis_data: done. readings=%s, promet_rows=%s, invoices=%s",
            len(readings), len(promet), len(invoices),
        )
        return result


# local debug
if __name__ == "__main__":
    import os
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    u = os.getenv("PIS_USERNAME")
    p = os.getenv("PIS_PASSWORD")
    if not u or not p:
        raise SystemExit("Set PIS_USERNAME and PIS_PASSWORD")
    data = collect_pis_data(u, p)
    print(json.dumps(data, ensure_ascii=False, indent=2))
