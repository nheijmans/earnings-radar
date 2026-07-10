# AGENTS.md

Notes for future sessions. Keep this project simple — it's a homelab tool, not
a service. Resist adding layers, config frameworks, or speculative structure.

## Layout

Everything is in one file: `earnings_radar.py` (~100 lines). Tests in
`test_earnings_radar.py`. Container in `Dockerfile`. Watch list in
`follow_list.json`. That's the whole repo. It runs once and exits; scheduling
is host cron firing `docker run` (see README).

## The pipeline (`main`)

`ticker_to_cik()` (one SEC request → ticker→CIK map) → for each ticker,
`recent_earnings_8ks()` reads its EDGAR submissions feed and yields new 8-Ks
with `is_earnings_8k()` true → `exhibit_links()` pulls the EX-99.x document URLs
→ `notify()` sends them as ntfy tap actions. A `seen` SQLite table dedupes.

## Things worth knowing

- **The earnings signal is 8-K Item 2.02** ("Results of Operations and Financial
  Condition"). EDGAR's submissions feed lists each filing's form and 8-K item
  numbers, so one request per ticker is enough. This replaced an earlier
  approach that tried to read exhibit types (`EX-99.x`) from EDGAR's
  `index.json` — that `type` field is icon filenames, not exhibit types, so it
  never matched. Item 2.02 is both simpler and the canonical marker.
- **Single data source: EDGAR, no API key.** Finnhub was dropped — it could
  discover 8-Ks but not their items, so it added a dependency without removing
  the EDGAR call.
- **Exhibit links come from the filing's HTML index page, regex-parsed**
  (`parse_exhibits`). The `index.json` directory listing's `type` field is icon
  filenames, not exhibit types — the HTML table is the only place EDGAR exposes
  "EX-99.1" mapped to a document. `exhibit_links()` degrades to `{}` on any
  failure so a filing still alerts (with just the SEC-filing link) if parsing
  breaks. EX-99.1 = press release, EX-99.2/99.3 = presentation / CFO commentary.
- **Mark-seen happens only after a successful `notify()`.** If the push fails,
  the filing isn't recorded, so the next run retries it. (Emoji can't go in the
  ntfy Title — HTTP headers are latin-1; the `chart_with_upwards_trend` tag
  renders 📈 instead.)
- **Config is read from `os.environ` where it's used**, not at import — that
  keeps the module importable in tests without env vars set.
- **The ntfy topic is the only secret protecting alerts.** Never log or commit it.
- **US-only** is inherent to EDGAR (US SEC). Supporting European tickers means a
  different data source, not a bug fix.

## Conventions

- Plain functions, standard library + `requests`. No classes, no dataclasses
  unless something genuinely needs them.
- Per-ticker work is wrapped in try/except so one bad ticker doesn't kill the run.
- Before finishing: `pytest` green. Keep tests to the pure logic
  (`is_earnings_8k`, `symbol_and_name`); don't mock the network.

## Planned Phase 2 (not built)

After a filing is detected: fetch the press release, extract numbers (revenue,
EPS, guidance) with a **local LLM**, store one row per ticker/quarter in SQLite,
then use a **frontier Claude model** to diff against prior quarters and write a
short markdown digest; the push links to it. Split rationale: local model for
cheap bulk extraction, frontier model only for the synthesis that needs quality.
When wiring the Claude call, check current model ids against the `claude-api`
skill. Add it as a second file/function — don't bloat the alerter.
