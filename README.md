# Stock Market Watcher

This skill handles explicit requests about a named company's public stock price or performance. It identifies the requested company or ticker, records timestamped stock snapshots in the database, and can create a concise Markdown report. A company or brand mention by itself does not activate the skill.

## Why this Skill Exists

This skill is a workflow, not just a one-step prompt. A single AI chat can give a current price, but it does not automatically store the data, compare it with earlier snapshots, or build a reusable market summary. This skill handles those intermediate steps: identifying the brand and ticker, fetching the right sources, parsing the data, saving snapshots, and comparing changes over time.

That makes it useful for ongoing market tracking rather than a one-off answer.

## Who it is for

Students can learn how a Codex-compatible skill works by following its workflow and seeing how persistent state, public JSON endpoints, and Markdown report generation contribute to the final result.

It is also for individuals interested in stock prices, researchers, and investors who want lightweight, automated market tracking, historical price comparisons, and summarized brand-related events from the command line or an agent chat.

It is especially useful for people who want to:

- Keep up with stock prices and relevant news without gathering information from multiple sources themselves
- Review historical closing prices and a clear report of a brand’s stock performance over a date range

## Key Features

- Extracts brand or company names and optional date ranges from natural-language prompts. If no date range is specified, it defaults to the last month.
- Resolves brand names to ticker symbols with Yahoo Finance and fetches historical prices from its public JSON endpoints.
- Reports historical closing prices, trends, and related events for a requested date range.
- Computes net price change, percentage change, mean, extrema, and daily return volatility.
- Stores point-in-time stock price observations in a local SQLite database.
- Compares newly fetched prices with previous snapshots and highlights significant daily moves.
- Compiles findings into structured Markdown summary reports.
- Provides runnable Python scripts that turn parsed parameters into repeatable execution steps.

## Prerequisites & Setup

### Requirements

- Python 3.10 or newer.
- Internet access to retrieve daily closing-price history from Yahoo Finance.
- Write access to the project directory for the SQLite database.

The current command-line script uses only the Python standard library; no package installation is required.

### Setup

From the repository root, initialize the local SQLite database:

```bash
python3 scripts/stock_watcher.py init
```

The database is created at `.state/stock_market_watcher.db`; runtime data is excluded from git.

### Fetch and store a price snapshot

Fetch Yahoo Finance daily closing prices for the last calendar month ending today (the default) and append them to the database:

```bash
python3 scripts/stock_watcher.py price --brand "Microsoft"
```

Each successful `price` command stores completed Yahoo Finance regular-session daily closes in `snapshots`, linked to the company and source. `exchange_session_date` records the exchange-local date of the close, while `captured_at` records when the script retrieved it; for a weekend run, Friday's session date is stored separately from Saturday's or Sunday's capture timestamp. The script omits an in-progress current-session bar. Previous observations are retained. This is a historical daily-close series only: the command does not combine it with a separate live quote or after-hours price feed. It prints the net price and percentage change between the first and last available closes, significant moves, and any related news headlines, source-provided summaries, publication timing, and cautious relevance context. It writes an initial Markdown report in `reports/` with date coverage, price metrics, every retrieved close, and available details for each related article. When run through the Codex skill, the report is then enriched with roughly two to three paragraphs of source-verified detail for each news item presented in the conversation; the standalone script only has access to source-provided article summaries. For each of the top three close-to-close moves, the script searches **Yahoo Finance only** for ticker-and-date news, then retries by ticker and company name if needed. It prefers articles published within three days of a move and otherwise reports the closest Yahoo Finance result. Yahoo Finance is the sole news-discovery source; its results may link to articles from other publishers, whose publisher attribution is retained. Returned articles and their source links are included in the report and stored in `news_items`. The report distinguishes possible context from proven causation and explicitly notes when no article is returned or news lookup fails. An internet connection is required. See [references/yahoo-finance-prices.md](references/yahoo-finance-prices.md) for price-source, retrieval, and coverage details, and [references/yahoo-finance-news.md](references/yahoo-finance-news.md) for news-source details.

### Price data source, retrieval, and coverage

Yahoo Finance is the sole source for the script's price series. The script resolves the requested name through Yahoo Finance's search JSON endpoint, then requests daily chart JSON for the returned equity ticker over the requested date range. It reads the ticker symbol, company name, currency and price precision metadata, chart timestamps, and daily `close` values. Those daily closes are the values stored and compared; the script does not use a separate live quote, adjusted close, or after-hours price series. See [references/yahoo-finance-prices.md](references/yahoo-finance-prices.md) for endpoint details and coverage limitations.

The script does not restrict ticker results to a named exchange or independently verify listing venue. NYSE- and Nasdaq-listed equities are within the intended U.S. use case when Yahoo Finance returns usable symbols and chart history. Availability for Canadian and other international listings, OTC securities, and less-liquid tickers is not guaranteed; symbols may require Yahoo Finance's venue suffix, and price availability, currency metadata, or timing may vary. Confirm the resolved ticker and observation dates before relying on a result. Yahoo Finance data can be delayed and is not a real-time quote.

Choose a different lookback in calendar months; `--fromdate` can also set the range's end date:

```bash
python3 scripts/stock_watcher.py price --brand "Microsoft" --months 6
```

For example, fetch the six months ending on the specified date:

```bash
python3 scripts/stock_watcher.py price --brand "Microsoft" --months 6 --fromdate 2025-11-16
```

`--fromdate` defaults to the current date and sets the range end; `--months` defaults to `1` and determines how far back to fetch. The month count must be a positive whole number. Reported net change uses the first and last available daily closing prices within the requested interval, which can differ from the exact boundary dates when markets are closed.

## End-to-End Workflows

The general workflow is as follows:

1. Parse the natural-language request to identify the brand, requested date range, and whether a comparison with previous snapshots is needed. If no date range is provided, use the last month.
2. Map the brand name to its standardized stock ticker symbol (for example, Google to GOOG).
3. Use Yahoo Finance to resolve the ticker and retrieve its daily closing-price history.
4. Fetch historical daily closing prices for the requested period.
5. Calculate trend metrics such as net price change, percentage change, and daily volatility.
6. Store the brand, ticker, source information, and point-in-time price observations in the local SQLite database. Append new observations to the existing history rather than replacing earlier snapshots.
7. When previous snapshots are available or the request asks for a review, compare the latest prices and calculated changes with prior observations and rank the largest daily movements.
8. For the top 2-3 close-to-close moves by absolute percentage change, search Yahoo Finance news around each date and associate matching articles with that day's snapshot. Yahoo Finance is the only news-discovery source; see [references/yahoo-finance-news.md](references/yahoo-finance-news.md).
9. Write a Markdown report with the sources, price trends, top daily moves, related news (or an explicit unavailable/no-match note), and comparison findings.

## What the package actually does

This skill package mainly does a few core things:

- Stores stock sources and market snapshots in a local database.
- Runs the scripts needed to fetch and process market data.
- Parses prices and related information from public sources and the web.
- Compares new data with previous snapshots and highlights meaningful changes.
- Writes a concise Markdown report with the findings.

## What this Skill does not do

This skill package does not:

- Place trades or stock orders, or connect to live brokerage accounts.
- Send private search queries, financial histories, or tracking profiles to third-party services. Public-source requests should include only the information needed to retrieve the requested market data or news.
- Scrape websites or make unauthorized writes to external services. Prefer public market-data sources and use only access methods permitted by the source.
- Bypass paywalls, rate limits, authentication, or other access controls on restricted financial services.

### Summarize versus watch

Use `summarize` for a requested historical summary or stock-performance question:

```bash
python3 scripts/stock_watcher.py summarize --brand "Apple" --months 1
python3 scripts/stock_watcher.py summarize --brand "Apple" --brand "Microsoft"
```

Pass `--brand` once per company to summarize multiple brands in one invocation. A failed symbol gets its own `reports/[ticker]_[YYYYMMDD]_error.md` warning report; successful symbols are still fetched and reported.

Summaries fetch and save daily close snapshots, store matching news, and generate a Markdown report. They do not add the symbol to the watch registry. The existing `price` command remains a backward-compatible summarize alias.

Use `watch` only for an explicit request to track or monitor a brand. Resolve the symbol through Yahoo Finance, ask the user to confirm the resolved ticker, and then add it:

```bash
python3 scripts/stock_watcher.py watch --brand "Apple" --confirmed-ticker "AAPL"
```

Watch mode validates that the confirmation exactly matches Yahoo Finance’s resolved ticker. It writes only to `watched_symbols`; it does not fetch chart history, create snapshots, retrieve news, or write a report.

### Check a watchlist baseline

```bash
python3 scripts/stock_watcher.py watch-check --brand "Apple"
python3 scripts/stock_watcher.py watch-check --brand "Apple" --threshold-percent 5
python3 scripts/stock_watcher.py watch-check --brand "Apple" --brand "MSFT"
```

A newly watched symbol has no baseline until its first successful check. That check saves the latest completed regular-session close and exchange date without triggering an alert. Later checks compare against that baseline, using a strict threshold: exactly `+3%` or `-3%` does not flag, while values beyond those limits do. On a new comparable session, the baseline is updated to the latest close and session date. Pass `--brand` once per symbol to check multiple brands in one run. Each brand is handled independently: a failed fetch is reported without changing that symbol's `baseline_price` or `baseline_session_date`, and the remaining brands are still checked. Each failure produces a separate `reports/[ticker]_[YYYYMMDD]_error.md` report with the reason and an explicit not-checked warning.

### Offline fixture walkthrough

`--fixture` is an optional deterministic mode. It does not replace Yahoo Finance in normal runs; it only bypasses Yahoo for local testing and documentation. The sample fixture is at [`fixtures/watchlist_cases.json`](fixtures/watchlist_cases.json).

It contains Apple (`AAPL`), Microsoft (`MSFT`), a repeated-session symbol (`REPEAT`), an unavailable symbol (`UNAVAILABLE`), and a trend-report symbol (`TREND`). The fixture can demonstrate:

- a `100.00 → 104.00` move at the default 3% threshold;
- a `100.00 → 103.00` exact boundary;
- a repeated session with no duplicate snapshot or baseline change;
- a partial batch where an unavailable symbol preserves its baseline while a successful symbol proceeds;
- a local trend report with no network/news dependency.

Before the watchlist commands, initialize a test database and seed the watchlist baselines. The SQLite database is local runtime state; use a disposable database by setting `PYTHON...` only in a test harness, or manually execute the setup below in `.state/stock_market_watcher.db`.

#### Setup / State

```sql
INSERT INTO companies (name, ticker, created_at)
VALUES ('Apple Inc.', 'AAPL', '2024-01-12T20:00:00+00:00');
INSERT INTO watched_symbols
(company_id, ticker, added_at, active, baseline_price, baseline_session_date)
VALUES (
  (SELECT company_id FROM companies WHERE ticker = 'AAPL'),
  'AAPL', '2024-01-12T20:00:00+00:00', 1, 100.00, '2024-01-10'
);

INSERT INTO companies (name, ticker, created_at)
VALUES ('Microsoft Corporation', 'MSFT', '2024-01-12T20:00:00+00:00');
INSERT INTO watched_symbols
(company_id, ticker, added_at, active, baseline_price, baseline_session_date)
VALUES (
  (SELECT company_id FROM companies WHERE ticker = 'MSFT'),
  'MSFT', '2024-01-12T20:00:00+00:00', 1, 100.00, '2024-01-11'
);

INSERT INTO companies (name, ticker, created_at)
VALUES ('Repeated Session Example', 'REPEAT', '2024-01-12T20:00:00+00:00');
INSERT INTO watched_symbols
(company_id, ticker, added_at, active, baseline_price, baseline_session_date)
VALUES (
  (SELECT company_id FROM companies WHERE ticker = 'REPEAT'),
  'REPEAT', '2024-01-12T20:00:00+00:00', 1, 103.00, '2024-01-12'
);

INSERT INTO companies (name, ticker, created_at)
VALUES ('Unavailable Example Inc.', 'UNAVAILABLE', '2024-01-12T20:00:00+00:00');
INSERT INTO watched_symbols
(company_id, ticker, added_at, active, baseline_price, baseline_session_date)
VALUES (
  (SELECT company_id FROM companies WHERE ticker = 'UNAVAILABLE'),
  'UNAVAILABLE', '2024-01-12T20:00:00+00:00', 1, 100.00, '2024-01-10'
);
```

The fixture setup and expected results are:

| Case | Baseline | Fixture close/session | Command condition | Expected result |
|---|---:|---:|---|---|
| Flagged move | `100.00` / `2024-01-10` | `104.00` / `2024-01-12` | `AAPL`, default threshold 3% | Alert `yes`, signed `+4.00%`, baseline becomes `104.00` / `2024-01-12` |
| Exact boundary | `100.00` / `2024-01-11` | `103.00` / `2024-01-12` | `MSFT`, default threshold 3% | Alert `no`, signed `+3.00%`, baseline becomes `103.00` / `2024-01-12` |
| Repeated session | `103.00` / `2024-01-12` | `103.00` / `2024-01-12` | `REPEAT` | No alert, baseline remains `103.00` / `2024-01-12`, no duplicate snapshot |
| Partial failure | `100.00` / `2024-01-10` | unavailable | `UNAVAILABLE` + `AAPL` | UNAVAILABLE is warned/reported, its baseline is preserved, AAPL still proceeds |

#### Commands and Expected Outcomes

Flagged move:

```bash
python scripts/stock_watcher.py watch-check --brand "AAPL" --fixture fixtures/watchlist_cases.json
```

Expected: `AAPL net change: +4.00; percent change: +4.00%; threshold: 3.00%; session: 2024-01-12; flag: yes`.

Exact threshold boundary:

```bash
python scripts/stock_watcher.py watch-check --brand "MSFT" --fixture fixtures/watchlist_cases.json
```

Expected: `MSFT net change: +3.00; percent change: +3.00%; threshold: 3.00%; session: 2024-01-12; flag: no`. The baseline is updated because the session is new.

Repeated weekend/session check:

```bash
python scripts/stock_watcher.py watch-check --brand "REPEAT" --fixture fixtures/watchlist_cases.json
```

Expected: `No changes for REPEAT: session 2024-01-12 matches the baseline session`. It prints no signed movement and does not update the baseline or create duplicate snapshot state.

Partial batch failure:

```bash
python scripts/stock_watcher.py watch-check --brand "UNAVAILABLE" --brand "AAPL" --fixture fixtures/watchlist_cases.json
```

Expected: `UNAVAILABLE` prints a warning and writes `reports/UNAVAILABLE_YYYYMMDD_error.md`; it preserves `baseline_price = 100.00` and `baseline_session_date = 2024-01-10`. `AAPL` still prints `flag: yes` and updates to `104.00` / `2024-01-12`. The command returns exit status `1` because at least one brand failed.

Offline trend report:

```bash
python scripts/stock_watcher.py summarize --brand "TREND" --fixture fixtures/watchlist_cases.json
```

Expected: no Yahoo request; a dated `TREND_YYYYMMDD.md` report is written, with the available close series and its trend metrics. Fixture mode also works with `price` and `watch-check`.

Verify watchlist state with SQLite:

```bash
python -c "import sqlite3; c=sqlite3.connect('.state/stock_market_watcher.db'); print(*c.execute('SELECT ticker, baseline_price, baseline_session_date FROM watched_symbols ORDER BY ticker'), sep='\\n')"
```
