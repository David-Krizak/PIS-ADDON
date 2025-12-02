import logging
import re
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

from bs4 import BeautifulSoup

logger = logging.getLogger("pis-addon.parser")

# ---------- primitive parsers ----------

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


def _parse_euro_amount(text: str):
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
    if value is None:
        return None
    cleaned = value.replace(".", "").replace(" ", "").strip()
    try:
        return int(cleaned)
    except ValueError:
        logger.debug("Failed to parse int reading from %r", value)
        return None


def _parse_hr_long_date(text: str):
    if not text:
        return None
    text = text.strip()
    match = re.match(r"(\d{1,2})\.\s+([A-Za-zčćšđžČĆŠĐŽ]+)\s+(\d{4})\.?", text)
    if not match:
        logger.debug("Failed to match long HR date pattern for %r", text)
        return None
    day = int(match.group(1))
    month_name = match.group(2).lower()
    year = int(match.group(3))
    month = HR_MONTHS.get(month_name)
    if not month:
        logger.debug("Unknown HR month name %r in %r", month_name, text)
        return None
    try:
        return date(year, month, day)
    except ValueError:
        logger.debug("Invalid date values %s-%s-%s from %r", year, month, day, text)
        return None


def _parse_hr_short_date(text: str):
    if not text:
        return None
    cleaned = text.strip().replace(" ", "")
    for fmt in ("%d.%m.%Y", "%d.%m.%y"):
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    logger.debug("Failed to parse short HR date from %r", text)
    return None


# ---------- HTML parsing ----------

def parse_root_readings(soup: BeautifulSoup):
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
        row_date = _parse_hr_long_date(row.get("Datum"))
        row_value = _parse_int_reading(row.get("Stanje brojila"))
        row["parsed_date"] = row_date.isoformat() if row_date else None
        row["parsed_value"] = row_value
        readings.append(row)

    logger.info("Parsed %s readings from root page", len(readings))
    return readings


def parse_promet_table(soup: BeautifulSoup):
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
        parsed_date = _parse_hr_long_date(row.get("Datum"))
        zaduzenje = _parse_euro_amount(row.get("Zaduženje"))
        uplata = _parse_euro_amount(row.get("Uplata"))
        row["parsed_date"] = parsed_date.isoformat() if parsed_date else None
        row["zaduzenje_value"] = zaduzenje
        row["uplata_value"] = uplata
        rows.append(row)

    logger.info("Parsed %s rows from promet table", len(rows))
    return rows


def parse_promet_summary(soup: BeautifulSoup):
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
                inv["issue_date_raw"] = value
                parsed = _parse_hr_short_date(value)
                inv["issue_date"] = parsed.isoformat() if parsed else None
            elif line.startswith("Datum valute:"):
                value = line.split(":", 1)[1].strip()
                inv["due_date_raw"] = value
                parsed = _parse_hr_short_date(value)
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
                amount_text = line.split(":", 1)[1].strip()
                inv["amount_raw"] = amount_text
                inv["amount"] = _parse_euro_amount(amount_text)

        if right:
            img = right.find("img")
            if img and img.has_attr("src"):
                inv["barcode_src"] = img["src"]

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


# ---------- data shaping ----------

def _maybe_prepend_latest_charge(promet_rows, invoices):
    latest_charge = None
    for row in promet_rows:
        amount = row.get("zaduzenje_value") or 0.0
        if amount <= 0:
            continue
        parsed = row.get("parsed_date")
        if not parsed:
            continue
        try:
            dt = datetime.fromisoformat(parsed).date()
        except Exception:
            continue
        if latest_charge is None or dt > latest_charge[0]:
            latest_charge = (dt, row, amount)

    if latest_charge is None:
        return invoices

    latest_date, row, amount = latest_charge
    newest_invoice_date = None
    if invoices:
        candidate = invoices[0].get("issue_date")
        if candidate:
            try:
                newest_invoice_date = datetime.fromisoformat(candidate).date()
            except Exception:
                newest_invoice_date = None

    if newest_invoice_date and latest_date <= newest_invoice_date:
        return invoices

    synthetic_invoice = {
        "number": row.get("Opis"),
        "description": row.get("Opis"),
        "issue_date": latest_date.isoformat(),
        "issue_date_raw": row.get("Datum"),
        "amount_raw": row.get("Zaduženje"),
        "amount": amount,
        "source": "promet",
        "synthetic": True,
    }
    logger.info(
        "Prepending latest charge from promet (%s, %s €) ahead of invoice list",
        synthetic_invoice.get("issue_date"),
        synthetic_invoice.get("amount"),
    )
    return [synthetic_invoice] + invoices


def _enrich_invoices_with_payments(invoices, promet_rows):
    logger.info("Matching %s invoices with %s promet rows", len(invoices), len(promet_rows))

    EPS = 0.01
    charge_rows: List[Tuple[dict, Optional[date], float]] = []
    payment_rows: List[Tuple[dict, Optional[date], float]] = []

    for row in promet_rows:
        z = row.get("zaduzenje_value") or 0.0
        u = row.get("uplata_value") or 0.0
        d_str = row.get("parsed_date")
        parsed_date = datetime.fromisoformat(d_str).date() if d_str else None

        if z > 0:
            charge_rows.append((row, parsed_date, z))
        if u > 0:
            payment_rows.append((row, parsed_date, u))

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

        if amount is None or amount <= 0:
            inv["paid"] = True
            continue

        best_charge = None
        best_charge_score = None
        for row, parsed_date, z in charge_rows:
            diff = abs(z - amount)
            if diff > EPS:
                continue
            date_penalty = 0
            if issue_date and parsed_date:
                date_penalty = abs((parsed_date - issue_date).days)
            score = diff + date_penalty / 100.0
            if best_charge is None or score < best_charge_score:
                best_charge = (row, parsed_date, z)
                best_charge_score = score

        if not best_charge:
            continue

        _, charge_date, _ = best_charge

        best_payment = None
        best_payment_score = None
        for row, parsed_date, u in payment_rows:
            if abs(u - amount) > EPS:
                continue
            score = 0
            if parsed_date and charge_date:
                score = abs((parsed_date - charge_date).days)
            if best_payment is None or score < best_payment_score:
                best_payment = (row, parsed_date, u)
                best_payment_score = score

        if best_payment:
            _, parsed_date, u = best_payment
            inv["paid"] = True
            inv["payment_date"] = parsed_date.isoformat() if parsed_date else None
            inv["payment_amount"] = u


def _compute_finance_metrics(promet_rows, summary, invoices):
    today = date.today()
    logger.info(
        "Computing finance metrics for %s promet rows and %s invoices",
        len(promet_rows),
        len(invoices),
    )

    dug_prev = summary.get("Dug iz prethodnog razdoblja", {}).get("value") or 0.0
    ukupno_zaduzenje = summary.get("Ukupno zaduženje", {}).get("value") or 0.0
    ukupna_uplata = summary.get("Ukupna uplata", {}).get("value") or 0.0
    balance = dug_prev + ukupno_zaduzenje - ukupna_uplata

    EPS = 0.005
    if abs(balance) < EPS:
        balance = 0.0
        status = "podmireno"
    elif balance > 0:
        status = "dug"
    else:
        status = "preplata"

    current_year = today.year
    year_charges = 0.0
    year_payments = 0.0

    for row in promet_rows:
        parsed_date = row.get("parsed_date")
        if not parsed_date:
            continue
        d = datetime.fromisoformat(parsed_date).date()
        if d.year != current_year:
            continue
        year_charges += row.get("zaduzenje_value") or 0.0
        year_payments += row.get("uplata_value") or 0.0

    unpaid_invoices = [
        inv
        for inv in invoices
        if not inv.get("paid") and (inv.get("amount") or 0.0) > 0
    ]
    unpaid_total = sum((inv.get("amount") or 0.0) for inv in unpaid_invoices)

    latest_invoice = invoices[0] if invoices else None
    finance = {
        "summary": {
            "balance": balance,
            "status": status,
            "previous_debt": dug_prev,
            "charges_total": ukupno_zaduzenje,
            "payments_total": ukupna_uplata,
        },
        "latest_invoice": latest_invoice,
        "unpaid": {
            "count": len(unpaid_invoices),
            "total": unpaid_total,
            "invoices": unpaid_invoices[:5],
        },
        "year": {
            "year": current_year,
            "charges": year_charges,
            "payments": year_payments,
        },
    }
    return finance


def _compute_consumption_metrics(readings):
    today = date.today()
    logger.info("Computing consumption metrics from %s readings", len(readings))

    current = readings[0] if len(readings) >= 1 else None
    previous = readings[1] if len(readings) >= 2 else None

    current_value = current.get("parsed_value") if current else None
    previous_value = previous.get("parsed_value") if previous else None

    current_date = (
        datetime.fromisoformat(current["parsed_date"]).date()
        if current and current.get("parsed_date")
        else None
    )
    previous_date = (
        datetime.fromisoformat(previous["parsed_date"]).date()
        if previous and previous.get("parsed_date")
        else None
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
        elif days_between == 0:
            avg_daily_last_period = 0

    days_since_last_reading = None
    if current_date:
        days_since_last_reading = (today - current_date).days

    PRICE_PER_KWH = 0.55
    approx_last_period_cost = None
    if last_period_usage is not None:
        approx_last_period_cost = last_period_usage * PRICE_PER_KWH

    consumption = {
        "last_reading": current,
        "previous_reading": previous,
        "last_period_usage": last_period_usage,
        "days_between_last_two_readings": days_between,
        "avg_daily_usage_last_period": avg_daily_last_period,
        "days_since_last_reading": days_since_last_reading,
        "approx_last_period_cost": approx_last_period_cost,
    }
    return consumption


# ---------- public API ----------

def build_portal_payload(readings, promet_rows, summary, invoices, racuni_period):
    invoices_sorted = sorted(
        invoices,
        key=lambda inv: inv.get("issue_date") or inv.get("due_date") or "",
        reverse=True,
    )

    invoices_sorted = _maybe_prepend_latest_charge(promet_rows, invoices_sorted)
    _enrich_invoices_with_payments(invoices_sorted, promet_rows)

    finance = _compute_finance_metrics(promet_rows, summary, invoices_sorted)
    consumption = _compute_consumption_metrics(readings)

    payload = {
        "finance": finance,
        "consumption": consumption,
        "period": racuni_period,
        "recent_invoices": invoices_sorted[:5],
    }
    return payload
