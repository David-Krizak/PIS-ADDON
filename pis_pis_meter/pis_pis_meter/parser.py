import logging
import re
from datetime import date, datetime
from typing import Dict, List, Optional

from bs4 import BeautifulSoup

logger = logging.getLogger("pis-addon.parser")

# ---------- primitive parsers ----------
def _classify_tx(desc: Optional[str]) -> str:
    """Rough classification of Promet row based on description."""
    if not desc:
        return "other"
    d = desc.lower()
    if "racun za" in d:
        return "bill"
    if "fiksna mjesecna naknada" in d:
        return "fixed_fee"
    if d.startswith("rn "):
        return "payment"
    return "other"
def parse_monthly_usage(soup: BeautifulSoup):
    """Parse hidden monthly usage table from ocitanja_brojila / columnchart_div.

    Vraća dict oblika:
    {
        "2023": [jan, feb, ..., dec],
        "2024": [...],
        "2025": [...]
    }
    """

    table = soup.select_one("#ocitanja_brojila table")
    if not table:
        logger.warning("Could not find monthly usage table in #ocitanja_brojila")
        return None

    # Header: Mjesec, 2023, 2024, 2025
    header_cells = [th.get_text(strip=True) for th in table.select("thead tr th")]
    if len(header_cells) < 2:
        logger.warning("Monthly usage table header too short: %r", header_cells)
        return None

    years: List[int] = []
    for h in header_cells[1:]:
        try:
            years.append(int(h))
        except ValueError:
            logger.debug("Skipping non-year header cell in monthly usage: %r", h)

    if not years:
        logger.warning("No valid years found in monthly usage header")
        return None

    # Pre-fill s 0 za 12 mjeseci po godini
    monthly: Dict[str, List[int]] = {str(y): [0] * 12 for y in years}

    month_map = {
        "sij": 1, "vlj": 2, "ožu": 3, "ožuj": 3,
        "tra": 4, "svi": 5, "lip": 6,
        "srp": 7, "kol": 8, "ruj": 9,
        "lis": 10, "stu": 11, "pro": 12,
    }

    tbody_rows = table.select("tbody tr")
    for tr in tbody_rows:
        tds = tr.find_all("td")
        if len(tds) < 2:
            continue

        month_label = tds[0].get_text(strip=True).lower()
        month_num = month_map.get(month_label)
        if not month_num:
            logger.debug("Unknown month label in monthly usage: %r", month_label)
            continue

        idx = month_num - 1  # 0-based index

        # Za svaki year, uzmi odgovarajući stupac
        for i, year in enumerate(years, start=1):
            if i >= len(tds):
                break
            val_text = tds[i].get_text(strip=True)
            val = _parse_int_reading(val_text)
            if val is None:
                val = 0
            monthly[str(year)][idx] = val

    logger.info("Parsed monthly usage for years: %s", list(monthly.keys()))
    return monthly


def _parse_euro_amount(text: str):
    """Convert a localized currency string to float or return None."""

    if not text:
        return None
    cleaned = text.replace("€", "").replace("EUR", "").strip()
    cleaned = cleaned.replace(" ", "").replace(".", "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        logger.debug("Failed to parse euro amount from %r", text)
        return None


def _parse_int_reading(value: str):
    """Parse an integer meter reading."""

    if value is None:
        return None
    cleaned = value.replace(".", "").replace(" ", "").strip()
    try:
        return int(cleaned)
    except ValueError:
        logger.debug("Failed to parse int reading from %r", value)
        return None


def _parse_hr_date(text: str):
    """Parse Croatian dates with either numeric month or month name.

    Examples:
      - '3.12.2025.'
      - '27. studenog 2025.'
      - '30. listopada 2025.'
    """

    if not text:
        return None

    cleaned = text.strip()
    # Remove double spaces, non-breaking, trailing dots
    cleaned = cleaned.replace("\xa0", " ")
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = cleaned.rstrip(" .")

    # 1) Try pure numeric first: 3.12.2025
    m = re.match(r"^(\d{1,2})\.(\d{1,2})\.(\d{2,4})$", cleaned)
    if m:
        day = int(m.group(1))
        month = int(m.group(2))
        year = int(m.group(3))
        if year < 100:
            year += 2000
        try:
            return date(year, month, day)
        except ValueError:
            logger.debug("Invalid numeric date components from %r", text)
            return None

    # 2) Month-name format: 27. studenog 2025
    #    capture: day, month word, year
    m = re.match(r"^(\d{1,2})\.\s*([A-Za-zčćšđžČĆŠĐŽ]+)\s+(\d{2,4})$", cleaned)
    if not m:
        logger.debug("Failed to parse HR date from %r", text)
        return None

    day = int(m.group(1))
    month_word = m.group(2).lower()
    year = int(m.group(3))
    if year < 100:
        year += 2000

    # Genitive + nominative forms
    MONTHS = {
        "siječanj": 1, "siječnja": 1,
        "veljača": 2, "veljače": 2,
        "ožujak": 3, "ožujka": 3,
        "travanj": 4, "travnja": 4,
        "svibanj": 5, "svibnja": 5,
        "lipanj": 6, "lipnja": 6,
        "srpanj": 7, "srpnja": 7,
        "kolovoz": 8, "kolovoza": 8,
        "rujan": 9, "rujna": 9,
        "listopad": 10, "listopada": 10,
        "studeni": 11, "studenog": 11,
        "prosinac": 12, "prosinca": 12,
    }

    month = MONTHS.get(month_word)
    if not month:
        logger.debug("Unknown HR month name %r in %r", month_word, text)
        return None

    try:
        return date(year, month, day)
    except ValueError:
        logger.debug("Invalid date components from %r", text)
        return None


# ---------- HTML parsing ----------


def parse_root_readings(soup: BeautifulSoup):
    """Read the last few meter values from the landing page."""

    table = soup.select_one("#stranicenje table.altrowstable")
    if not table:
        logger.warning("Could not find readings table on root page")
        return []

    headers = [th.get_text(strip=True) for th in table.select("thead th")]
    readings = []
    for tr in table.select("tbody tr"):
        cols = [td.get_text(strip=True) for td in tr.find_all("td")]
        if not cols:
            continue
        row = dict(zip(headers, cols))
        parsed_date = _parse_hr_date(row.get("Datum"))
        parsed_value = _parse_int_reading(row.get("Stanje brojila"))
        readings.append(
            {
                "date_raw": row.get("Datum"),
                "value_raw": row.get("Stanje brojila"),
                "date": parsed_date.isoformat() if parsed_date else None,
                "value": parsed_value,
            }
        )

    logger.info("Parsed %s readings from root page", len(readings))
    return readings

def parse_promet_table(soup: BeautifulSoup):
    """Extract charges/payments table to gauge current balance."""

    table = soup.select_one("#tabularniPodaci #stranicenje table.altrowstable")
    if not table:
        table = soup.select_one("#stranicenje table.altrowstable")
    if not table:
        logger.warning("Could not find promet table on /Promet page")
        return []

    headers = [th.get_text(strip=True) for th in table.select("thead th")]
    rows = []
    for tr in table.select("tbody tr"):
        cols = [td.get_text(strip=True) for td in tr.find_all("td")]
        if not cols:
            continue
        row = dict(zip(headers, cols))

        parsed_date = _parse_hr_date(row.get("Datum"))
        charge_raw = row.get("Zaduženje")
        payment_raw = row.get("Uplata")

        zaduzenje = _parse_euro_amount(charge_raw)
        uplata = _parse_euro_amount(payment_raw)

        rows.append(
            {
                "date_raw": row.get("Datum"),
                "date": parsed_date.isoformat() if parsed_date else None,
                "description": row.get("Opis"),
                "charge_raw": charge_raw,
                "charge": zaduzenje,
                "payment_raw": payment_raw,
                "payment": uplata,
            }
        )

    logger.info("Parsed %s rows from promet table", len(rows))
    return rows


def parse_promet_summary(soup: BeautifulSoup):
    """Pull summarized totals for quick outstanding calculation."""

    summary_div = soup.select_one("div.summary")
    if not summary_div:
        logger.warning("Could not find summary div on /Promet page")
        return {}

    summary: Dict[str, Dict[str, Optional[float]]] = {}
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
        summary[label] = {"raw": raw_value, "value": num_value}

    logger.info("Parsed %s items from promet summary", len(summary))
    return summary


def parse_racuni(soup: BeautifulSoup):
    """Collect minimal invoice details (number, dates, amount) from racuni section.

    NOTE: For latest invoice we now primarily trust the Promet table. This parser
    is kept for compatibility / future use.
    """

    invoices = []
    table = soup.select_one("#racuni table.altrowstable")
    if not table:
        logger.warning("Could not find racuni table on /Promet page")
        return invoices

    for tr in table.select("tbody tr"):
        left = tr.select_one("td.racunLijevo")
        if not left:
            continue

        text = left.get_text("\n", strip=True)
        inv: Dict[str, Optional[str]] = {}
        for line in text.split("\n"):
            line = line.strip()
            if not line:
                continue
            if line.startswith("Broj računa:"):
                inv["number"] = line.split(":", 1)[1].strip()
            elif line.startswith("Opis računa:"):
                inv["description"] = line.split(":", 1)[1].strip()
            elif line.startswith("Datum računa:"):
                value = line.split(":", 1)[1].strip()
                parsed = _parse_hr_date(value)
                inv["issue_date_raw"] = value
                inv["issue_date"] = parsed.isoformat() if parsed else None
            elif line.startswith("Datum valute:"):
                value = line.split(":", 1)[1].strip()
                parsed = _parse_hr_date(value)
                inv["due_date_raw"] = value
                inv["due_date"] = parsed.isoformat() if parsed else None
            elif "Iznos:" in line:
                amount_text = line.split(":", 1)[1].strip()
                inv["amount_raw"] = amount_text
                inv["amount"] = _parse_euro_amount(amount_text)

        if inv:
            invoices.append(inv)

    logger.info("Parsed %s invoices from racuni section", len(invoices))
    return invoices


def parse_racuni_period(soup: BeautifulSoup):
    container = soup.select_one("#racuni > div")
    if not container:
        logger.debug("No racuni period header found")
        return None

    text = container.get_text(" ", strip=True)
    match = re.search(r"period:\s*(.+?)\s*-\s*(.+)$", text)
    if not match:
        logger.debug("Failed to parse racuni period from text: %r", text)
        return None

    start_raw = match.group(1).strip()
    end_raw = match.group(2).strip()

    start_date = _parse_hr_date(start_raw)
    end_date = _parse_hr_date(end_raw)

    result = {
        "raw": text,
        "start_raw": start_raw,
        "end_raw": end_raw,
        "start": start_date.isoformat() if start_date else None,
        "end": end_date.isoformat() if end_date else None,
    }
    logger.info("Parsed racuni period: %s", result)
    return result


# ---------- data shaping ----------

def _compute_finance(readings, promet_rows, promet_summary, invoices):
    """Return what the UI needs: balance, last bill, last payment."""

    previous_debt = promet_summary.get("Dug iz prethodnog razdoblja", {}).get("value") or 0.0
    charges_total = promet_summary.get("Ukupno zaduženje", {}).get("value") or 0.0
    payments_total = promet_summary.get("Ukupna uplata", {}).get("value") or 0.0
    overpayment = promet_summary.get("U preplati ste u iznosu od", {}).get("value") or 0.0

    outstanding = previous_debt + charges_total - payments_total

    EPS = 0.005
    if abs(outstanding) < EPS:
        outstanding = 0.0
        status = "settled"
    elif outstanding > 0:
        status = "unpaid"
    else:
        status = "credit"

    # nice clean number
    outstanding = round(outstanding, 2)

    # --- latest bill (racun) from Promet rows ---
    latest_bill_row = None
    for row in sorted(promet_rows, key=lambda r: r.get("date") or "", reverse=True):
        if _classify_tx(row.get("description")) == "bill":
            latest_bill_row = row
            break

    latest_invoice = None
    if latest_bill_row:
        # for bills, the amount sits in "payment" column (your HTML)
        amount_val = latest_bill_row.get("payment") or latest_bill_row.get("charge")
        amount_raw = latest_bill_row.get("payment_raw") or latest_bill_row.get("charge_raw")
        if amount_val is not None:
            amount_val = round(float(amount_val), 2)

        latest_invoice = {
            "description": latest_bill_row.get("description"),
            "issue_date_raw": latest_bill_row.get("date_raw"),
            "issue_date": latest_bill_row.get("date"),
            "amount_raw": amount_raw,
            "amount": amount_val,
            "due_date_raw": None,
            "due_date": None,
            "number": None,
        }

    # --- last payment (any positive payment row) ---
    last_payment = None
    for row in sorted(promet_rows, key=lambda r: r.get("date") or "", reverse=True):
        if row.get("payment"):
            amt = round(float(row["payment"]), 2)
            last_payment = {
                "date_raw": row.get("date_raw"),
                "date": row.get("date"),
                "amount_raw": row.get("payment_raw"),
                "amount": amt,
                "description": row.get("description"),
            }
            break

    finance = {
        "status": status,
        "outstanding": outstanding,
        "previous_debt": previous_debt,
        "charges_total": charges_total,
        "payments_total": payments_total,
        "overpayment": overpayment,
        "unpaid": {
            "count": 1 if outstanding > 0 else 0,
            "total": outstanding if outstanding > 0 else 0.0,
        },
        "latest_invoice": latest_invoice,
        "last_payment": last_payment,
        "has_unpaid": outstanding > 0,
        "is_in_credit": outstanding < 0 or overpayment > 0,
    }

    return finance


def _compute_consumption(readings, monthly_usage=None):
    """Use readings to estimate last period usage, price and yearly total."""

    today = date.today()

    # zadnje očitanje i prethodno
    current = readings[0] if len(readings) >= 1 else None
    previous = readings[1] if len(readings) >= 2 else None

    current_value = current.get("value") if current else None
    previous_value = previous.get("value") if previous else None

    current_date = (
        datetime.fromisoformat(current["date"]).date()
        if current and current.get("date")
        else None
    )
    previous_date = (
        datetime.fromisoformat(previous["date"]).date()
        if previous and previous.get("date")
        else None
    )

    usage = None
    days_between = None
    avg_daily = None

    # osnovna logika za zadnji period
    if (
        current_value is not None
        and previous_value is not None
        and current_date
        and previous_date
    ):
        usage = current_value - previous_value
        if usage < 0:
            logger.debug(
                "Current reading (%s) lower than previous (%s); ignoring usage",
                current_value,
                previous_value,
            )
            usage = None
        else:
            days_between = (current_date - previous_date).days
            if days_between > 0:
                avg_daily = usage / days_between
            elif days_between == 0:
                avg_daily = 0.0

    days_since_last = None
    if current_date:
        days_since_last = (today - current_date).days

    PRICE_PER_KWH = 0.55
    approx_cost = usage * PRICE_PER_KWH if usage is not None else None

    # --- godišnja potrošnja iz očitanja iste godine kao zadnje ---
    year_usage = None
    year_start = None
    year_end = None

    dated_readings = []
    for r in readings:
        d_iso = r.get("date")
        v = r.get("value")
        if not d_iso or v is None:
            continue
        try:
            d_obj = datetime.fromisoformat(d_iso).date()
        except ValueError:
            continue
        dated_readings.append((d_obj, v))

    if dated_readings:
        dated_readings.sort(key=lambda x: x[0])
        target_year = dated_readings[-1][0].year
        year_readings = [(d, v) for (d, v) in dated_readings if d.year == target_year]

        if len(year_readings) >= 2:
            year_start = year_readings[0][0]
            year_end = year_readings[-1][0]
            total = 0
            last_v = year_readings[0][1]
            for (d, v) in year_readings[1:]:
                diff = v - last_v
                if diff > 0:
                    total += diff
                last_v = v
            year_usage = total

    # zaokruživanje / čišćenje
    if approx_cost is not None:
        approx_cost = round(approx_cost, 2)
    if avg_daily is not None:
        avg_daily = round(avg_daily, 3)
    if year_usage is not None:
        year_usage = int(round(year_usage))

    return {
        "last_reading": current,
        "previous_reading": previous,
        "usage": usage,
        "days_between_readings": days_between,
        "avg_daily_usage": avg_daily,
        "days_since_last_reading": days_since_last,
        "approx_last_period_cost": approx_cost,
        "year_usage": year_usage,
        "year_period_start": year_start.isoformat() if year_start else None,
        "year_period_end": year_end.isoformat() if year_end else None,
        "monthly_usage": monthly_usage,  # novo polje
    }


# ---------- public API ----------

monthly_usage = parse_monthly_usage(root_soup)

def build_portal_payload(
    readings,
    promet_rows,
    summary,
    invoices,
    racuni_period,
    monthly_usage=monthly_usage,
):
    finance = _compute_finance(readings, promet_rows, summary, invoices)
    consumption = _compute_consumption(readings, monthly_usage)

    return {
        "finance": finance,
        "consumption": consumption,
        "period": racuni_period,
    }
