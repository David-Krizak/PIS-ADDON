import json
import re
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


# ---------- low-level helpers ----------

def _extract_verification_token(html, cookies):
    soup = BeautifulSoup(html, "html.parser")
    token_input = soup.find("input", {"name": "__RequestVerificationToken"})
    if token_input and token_input.has_attr("value"):
        return token_input["value"]

    cookie_token = cookies.get("__RequestVerificationToken")
    if cookie_token:
        return cookie_token

    raise RuntimeError("Cannot find __RequestVerificationToken in HTML or cookies.")


def _login(session: requests.Session, username: str, password: str) -> None:
    r = session.get(LOGIN_URL, headers=HEADERS, allow_redirects=True)
    if r.status_code != 200:
        raise RuntimeError(f"Login page GET failed: {r.status_code}")

    token = _extract_verification_token(r.text, session.cookies)

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

    resp = session.post(LOGIN_POST_URL, data=payload,
                        headers=post_headers, allow_redirects=True)

    cookies = session.cookies.get_dict()
    if ".ASPXAUTH" not in cookies:
        raise RuntimeError("Login failed – no .ASPXAUTH cookie, check credentials.")


def _fetch(session: requests.Session, url: str) -> BeautifulSoup:
    r = session.get(url, headers=HEADERS, allow_redirects=True)
    if r.status_code != 200:
        raise RuntimeError(f"GET {url} failed: {r.status_code}")
    return BeautifulSoup(r.text, "html.parser")


def _parse_euro_amount(text: str):
    """
    Convert '531,14 €' or '131,94' or '171,20EUR' to float.
    Returns None on failure.
    """
    if not text:
        return None
    t = text.replace("€", "").replace("EUR", "").strip()
    # remove thousands dots, replace comma with dot
    t = t.replace(" ", "").replace(".", "").replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return None


def _parse_int_reading(value: str):
    """
    '1.840' / '1840' / '1 840' -> 1840, returns None on failure.
    """
    if value is None:
        return None
    t = value.replace(".", "").replace(" ", "").strip()
    try:
        return int(t)
    except ValueError:
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
        return None
    day = int(m.group(1))
    month_name = m.group(2).lower()
    year = int(m.group(3))
    month = HR_MONTHS.get(month_name)
    if not month:
        return None
    try:
        return date(year, month, day)
    except ValueError:
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
    return None


# ---------- parsing HTML ----------

def _parse_root_readings(soup: BeautifulSoup):
    """
    / page: div#stranicenje > table.altrowstable
    Columns: Datum, Serijski broj, Vrsta, Stanje brojila
    """
    table = soup.select_one("#stranicenje table.altrowstable")
    if not table:
        return []

    headers = [th.get_text(strip=True) for th in table.select("thead th")]
    readings = []
    for tr in table.select("tbody tr"):
        cols = [td.get_text(strip=True) for td in tr.find_all("td")]
        if not cols:
            continue
        row = dict(zip(headers, cols))

        # add parsed fields
        row_date = _parse_hr_long_date(row.get("Datum"))
        row_value = _parse_int_reading(row.get("Stanje brojila"))
        row["parsed_date"] = row_date.isoformat() if row_date else None
        row["parsed_value"] = row_value

        readings.append(row)
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
        return []

    headers = [th.get_text(strip=True) for th in table.select("thead th")]
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
    return rows


def _parse_promet_summary(soup: BeautifulSoup):
    """
    div.summary – key/value pairs.
    """
    summary_div = soup.select_one("div.summary")
    if not summary_div:
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


def _parse_racuni(soup: BeautifulSoup):
    """
    /Promet: section #racuni – table with invoice details.
    Each <td class="racunLijevo"> contains textual blocks.

    We parse:
      - number
      - description
      - issue_date
      - due_date
      - place
      - partner
      - iban
      - amount
    """
    invoices = []
    for td in soup.select("#racuni table.altrowstable tbody td.racunLijevo"):
        text = td.get_text("\n", strip=True)
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
                inv["issue_date"] = _parse_hr_short_date(d).isoformat() if _parse_hr_short_date(d) else None
            elif line.startswith("Datum valute:"):
                d = line.split(":", 1)[1].strip()
                inv["due_date_raw"] = d
                inv["due_date"] = _parse_hr_short_date(d).isoformat() if _parse_hr_short_date(d) else None
            elif line.startswith("Prodajno mjesto:"):
                inv["place"] = line.split(":", 1)[1].strip()
            elif line.startswith("Poslovni partner:"):
                inv["partner"] = line.split(":", 1)[1].strip()
            elif line.startswith("Model i poziv na broj:"):
                inv["model_poziv"] = line.split(":", 1)[1].strip()
            elif line.startswith("IBAN:"):
                inv["iban"] = line.split(":", 1)[1].strip()
            elif "Iznos:" in line:
                # e.g. 'Iznos: 171,20EUR'
                amt = line.split(":", 1)[1].strip()
                inv["amount_raw"] = amt
                inv["amount"] = _parse_euro_amount(amt)

        if inv:
            invoices.append(inv)
    return invoices


# ---------- metrics ----------

def _compute_finance_metrics(promet_rows, summary, invoices):
    today = date.today()

    dug_prev = summary.get("Dug iz prethodnog razdoblja", {}).get("value") or 0.0
    ukupno_zaduzenje = summary.get("Ukupno zaduženje", {}).get("value") or 0.0
    ukupna_uplata = summary.get("Ukupna uplata", {}).get("value") or 0.0

    balance = dug_prev + ukupno_zaduzenje - ukupna_uplata  # + = dug, - = preplata
    if balance > 0:
        status = "dug"
    elif balance < 0:
        status = "preplata"
    else:
        status = "podmireno"

    # overpayment from computed balance (should match summary)
    overpayment_value = -balance if balance < 0 else 0.0

    # pick last invoice from racuni if available, otherwise infer from promet
    last_invoice = invoices[0] if invoices else None

    if not last_invoice:
        # fallback: last row with positive zaduženje (invoice)
        for row in promet_rows:
            if (row.get("zaduzenje_value") or 0) > 0:
                last_invoice = {
                    "number": row.get("Opis"),
                    "description": row.get("Opis"),
                    "issue_date": row.get("parsed_date"),
                    "due_date": None,
                    "amount": row.get("zaduzenje_value"),
                    "amount_raw": row.get("Zaduženje"),
                }
                break

    # calculate days_until_due / overdue
    days_until_due = None
    is_overdue = None
    if last_invoice and last_invoice.get("due_date"):
        due = datetime.fromisoformat(last_invoice["due_date"]).date()
        days_until_due = (due - today).days
        is_overdue = days_until_due < 0

    # year stats from promet (current year)
    current_year = today.year
    year_charges = 0.0
    year_payments = 0.0
    charge_months = {}  # yyyy-mm -> amount

    for row in promet_rows:
        d_str = row.get("parsed_date")
        if not d_str:
            continue
        d = datetime.fromisoformat(d_str).date()
        if d.year != current_year:
            continue
        z = row.get("zaduzenje_value") or 0.0
        u = row.get("uplata_value") or 0.0
        year_charges += z
        year_payments += u

        # treat "RN" rows (zaduženje) as bill
        if z > 0:
            key = f"{d.year}-{d.month:02d}"
            charge_months[key] = charge_months.get(key, 0.0) + z

    # average monthly bill from last 6 months (if any)
    sorted_months = sorted(charge_months.items(), key=lambda x: x[0], reverse=True)
    recent = sorted_months[:6]
    avg_recent_bill = None
    if recent:
        avg_recent_bill = sum(v for _, v in recent) / len(recent)

    finance = {
        "summary": {
            "dug_prethodno": dug_prev,
            "ukupno_zaduzenje": ukupno_zaduzenje,
            "ukupna_uplata": ukupna_uplata,
            "balance": balance,  # + = dug, - = preplata
            "status": status,    # 'dug' / 'preplata' / 'podmireno'
            "overpayment": overpayment_value,
            "raw_summary": summary,
        },
        "last_invoice": last_invoice,
        "last_invoice_days_until_due": days_until_due,
        "last_invoice_is_overdue": is_overdue,
        "year_stats": {
            "year": current_year,
            "charges": year_charges,
            "payments": year_payments,
            "avg_recent_bill": avg_recent_bill,
        },
    }
    return finance


def _compute_consumption_metrics(readings):
    """
    readings: list from _parse_root_readings (already has parsed_date/parsed_value)
    """
    today = date.today()

    current = readings[0] if len(readings) >= 1 else None
    previous = readings[1] if len(readings) >= 2 else None

    current_value = current.get("parsed_value") if current else None
    previous_value = previous.get("parsed_value") if previous else None

    current_date = datetime.fromisoformat(current["parsed_date"]).date() if current and current.get("parsed_date") else None
    previous_date = datetime.fromisoformat(previous["parsed_date"]).date() if previous and previous.get("parsed_date") else None

    last_period_usage = None
    days_between = None
    avg_daily_last_period = None

    if current_value is not None and previous_value is not None and current_date and previous_date:
        last_period_usage = current_value - previous_value
        days_between = (current_date - previous_date).days
        if days_between > 0:
            avg_daily_last_period = last_period_usage / days_between

    days_since_last_reading = None
    if current_date:
        days_since_last_reading = (today - current_date).days

    consumption = {
        "last_reading": current,
        "previous_reading": previous,
        "last_period_usage": last_period_usage,
        "days_between_last_two_readings": days_between,
        "avg_daily_usage_last_period": avg_daily_last_period,
        "days_since_last_reading": days_since_last_reading,
        "current_value": current_value,
        "previous_value": previous_value,
    }
    return consumption


# ---------- main entry ----------

def collect_pis_data(username: str, password: str) -> dict:
    """
    Main entry: logs in, fetches / and /Promet, returns structured dict.
    """
    with requests.Session() as session:
        session.headers.update(HEADERS)
        _login(session, username, password)

        root_soup = _fetch(session, ROOT_URL)
        promet_soup = _fetch(session, PROMET_URL)

        readings = _parse_root_readings(root_soup)
        promet = _parse_promet_table(promet_soup)
        summary = _parse_promet_summary(promet_soup)
        invoices = _parse_racuni(promet_soup)

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
            },
        }

        return result


# local debug
if __name__ == "__main__":
    import os
    u = os.getenv("PIS_USERNAME")
    p = os.getenv("PIS_PASSWORD")
    if not u or not p:
        raise SystemExit("Set PIS_USERNAME and PIS_PASSWORD")
    data = collect_pis_data(u, p)
    print(json.dumps(data, ensure_ascii=False, indent=2))
