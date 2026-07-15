#!/usr/bin/env python3
"""Poll SEC EDGAR for new quarterly earnings filings and push an alert via ntfy.

Earnings signal: an 8-K reporting Item 2.02, "Results of Operations and
Financial Condition" — the item a company files when it releases quarterly
results. EDGAR's per-company submissions feed lists every recent filing with
its form and 8-K item numbers, so one request per ticker tells us everything.
New matching filings are recorded in SQLite so nothing alerts twice.

The alert links to the actual documents: the press release (EX-99.1) and, when
present, the presentation / CFO commentary (EX-99.2/99.3), pulled from the
filing's index page.

EDGAR is US-only, so non-US tickers (ASML, Adyen) aren't covered.
Config comes from environment variables; see .env.example.
"""
import json
import logging
import os
import re
import sqlite3
from datetime import date, timedelta
from urllib.parse import urljoin

import requests

EARNINGS_ITEM = "2.02"  # 8-K "Results of Operations and Financial Condition"

log = logging.getLogger("earnings_radar")


def _headers():
    # SEC requires a descriptive User-Agent with contact info on every request.
    return {"User-Agent": os.environ["SEC_USER_AGENT"]}


def is_earnings_8k(form, items):
    """True for an 8-K that reports Item 2.02 (quarterly results)."""
    return form == "8-K" and EARNINGS_ITEM in [i.strip() for i in items.split(",")]


def symbol_and_name(entry):
    """A follow_list entry may be a bare "MSFT" or {"symbol", "name"}."""
    if isinstance(entry, dict):
        return entry["symbol"], entry.get("name", entry["symbol"])
    return entry, entry


def ticker_to_cik():
    """Map every US ticker to its zero-padded 10-digit CIK (one SEC request)."""
    r = requests.get("https://www.sec.gov/files/company_tickers.json",
                     headers=_headers(), timeout=20)
    r.raise_for_status()
    return {row["ticker"].upper(): f"{row['cik_str']:010d}" for row in r.json().values()}


def filing_index_url(cik, access):
    acc = access.replace("-", "")
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc}/{access}-index.htm"


def recent_earnings_8ks(cik, since):
    """Yield (access_number, filed_date, filing_url) for earnings 8-Ks filed on/after `since`."""
    r = requests.get(f"https://data.sec.gov/submissions/CIK{cik}.json",
                     headers=_headers(), timeout=20)
    r.raise_for_status()
    f = r.json()["filings"]["recent"]
    for access, form, items, filed in zip(
            f["accessionNumber"], f["form"], f["items"], f["filingDate"]):
        if filed < since or not is_earnings_8k(form, items):
            continue
        yield access, filed, filing_index_url(cik, access)


def parse_exhibits(html, base_url):
    """{'EX-99.1': url, ...} from a filing index page's document table."""
    out = {}
    for href, typ in re.findall(
            r'<a href="([^"]+)"[^>]*>[^<]*</a></td>\s*<td[^>]*>(EX-99\.\d+)</td>', html):
        out.setdefault(typ, urljoin(base_url + "/", href))
    return out


def exhibit_links(cik, access):
    """Fetch a filing's EX-99.x document links. Returns {} if unavailable."""
    index_url = filing_index_url(cik, access)
    base = index_url.rsplit("/", 1)[0]
    try:
        html = requests.get(index_url, headers=_headers(), timeout=20).text
        return parse_exhibits(html, base)
    except Exception:
        log.exception("could not read exhibits for %s", access)
        return {}


def emit_event(events_dir, *, symbol, name, access, filed, report_url, presentation_url, filing_url):
    """Write a JSON event so a downstream tool can react to a detected filing.

    Written atomically (temp file + rename) so a reader never sees a partial
    file. Opt-in: only called when EVENTS_DIR is set.
    """
    os.makedirs(events_dir, exist_ok=True)
    path = os.path.join(events_dir, f"{access}.json")
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({
            "ticker": symbol,
            "name": name,
            "accession": access,
            "filed": filed,
            "report_url": report_url,
            "presentation_url": presentation_url,
            "filing_url": filing_url,
        }, fh, indent=2)
    os.replace(tmp, path)


def notify(title, message, click_url, actions=()):
    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh")
    headers = {"Title": title, "Priority": "4",
               "Tags": "chart_with_upwards_trend", "Click": click_url}
    if actions:
        # ntfy tap actions: "view, <label>, <url>" joined by ";".
        headers["Actions"] = "; ".join(f"view, {label}, {url}" for label, url in actions)
    r = requests.post(f"{server}/{os.environ['NTFY_TOPIC']}",
                      data=message.encode(), headers=headers, timeout=15)
    r.raise_for_status()


def main():
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(message)s")

    follow_list = os.environ.get("FOLLOW_LIST_PATH", "follow_list.json")
    db_path = os.environ.get("STATE_DB_PATH", "earnings-radar.db")
    lookback = int(os.environ.get("LOOKBACK_DAYS", "3"))
    events_dir = os.environ.get("EVENTS_DIR", "")  # opt-in filing events for downstream tools

    with open(follow_list) as fh:
        tickers = json.load(fh)["tickers"]
    since = (date.today() - timedelta(days=lookback)).isoformat()

    cik_map = ticker_to_cik()

    db = sqlite3.connect(db_path)
    db.execute("CREATE TABLE IF NOT EXISTS seen (access_number TEXT PRIMARY KEY)")

    for entry in tickers:
        symbol, name = symbol_and_name(entry)
        cik = cik_map.get(symbol.upper())
        if not cik:
            log.warning("no CIK for %s (US-listed only?) — skipping", symbol)
            continue
        try:
            for access, filed, filing_url in recent_earnings_8ks(cik, since):
                if db.execute("SELECT 1 FROM seen WHERE access_number = ?",
                              (access,)).fetchone():
                    continue
                log.info("earnings filing for %s: %s", symbol, access)
                ex = exhibit_links(cik, access)
                report = ex.get("EX-99.1")
                presentation = ex.get("EX-99.2") or ex.get("EX-99.3")
                actions = []
                if report:
                    actions.append(("Report", report))
                if presentation:
                    actions.append(("Presentation", presentation))
                actions.append(("SEC filing", filing_url))  # always available
                # No emoji in the Title: HTTP headers are latin-1 only. The
                # "chart_with_upwards_trend" tag renders as 📈 on the phone.
                notify(f"{name} filed earnings",
                       f"{name} filed an 8-K earnings release on {filed}.",
                       click_url=report or filing_url,
                       actions=actions[:3])  # ntfy allows up to 3 tap actions
                db.execute("INSERT OR IGNORE INTO seen VALUES (?)", (access,))
                db.commit()
                if events_dir:
                    try:
                        emit_event(events_dir, symbol=symbol, name=name, access=access,
                                   filed=filed, report_url=report,
                                   presentation_url=presentation, filing_url=filing_url)
                    except Exception:
                        log.exception("failed to emit event for %s", access)
        except Exception:
            log.exception("failed on %s", symbol)

    db.close()


if __name__ == "__main__":
    main()
