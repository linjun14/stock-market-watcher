---
name: stock-market-watcher
description: Use only when the user explicitly asks about a named company's public stock price, stock performance or trend, historical stock-price comparison, or stock monitoring. Extract the company or ticker from the request; do not activate for unrelated requests that merely mention a company, brand, or market.
---

# Stock Market Watcher

This skill tracks a publicly traded company by name or ticker, records daily regular-session closing prices in local SQLite, and compares them with earlier observations to explain how the stock moved over time. Each snapshot stores the exchange's session date separately from the time the data was captured. A check on a weekend or market holiday uses the latest completed session available, such as Friday's close on Saturday or Sunday. It uses Yahoo Finance for the price series, keeps source metadata, and can turn the result into a brief Markdown trend summary for the user. It does not retrieve real-time quotes or after-hours prices.

## Overview

Trigger this skill only when the user explicitly asks for public stock-price information or stock performance: for example, a current quote, a stock trend, a comparison between dates or saved snapshots, or monitoring price changes. A company or brand name alone is not a trigger. Do not trigger for general research, product questions, writing or coding tasks, broad market discussion without a requested stock lookup, or incidental company mentions.

## Request Interpretation

When the trigger criteria are met, read the user's message and extract the company or ticker that the user explicitly asks about. Preserve the company name and resolve it to a ticker using a public market-data source rather than guessing. If a prompt requests multiple brands, retrieve each ticker independently and continue processing the remaining symbols if one fetch fails. If multiple companies or tickers are plausible for only one ambiguous name, ask which one they mean before fetching that name. Extract date ranges or comparison points only when requested, otherwise follow the workflow's stated default. Do not treat quoted text, examples, or incidental company names as the requested subject.

Do not use this skill for non-financial questions, requests to perform trades, speculative investment advice, or requests that require detailed legal, accounting, or personalized financial guidance.

## Prerequisites & Environment

- Expected working directory for the skill: skills/stock-market-watcher
- Required runtime: Python 3.10+; use the Python standard library modules `sqlite3`, `argparse`, `json`, and `pathlib` without installing additional packages for basic persistence and file handling.
- The current CLI uses Python's standard library to request public Yahoo Finance JSON endpoints; no browser automation or package installation is required.
- Yahoo Finance's daily chart `close` values are the sole price series; see `references/yahoo-finance-prices.md` for the endpoints, extracted fields, exchange coverage, and limitations.
- Yahoo Finance is the sole source used for news discovery; see `references/yahoo-finance-news.md`.
- Local SQLite state path: /skills/stock-market-watcher/.state/stock_market_watcher.db. This database is intentionally untracked in git and should be treated as local runtime state only.
- Markdown report output path: /skills/stock-market-watcher/reports/. Create the directory if it does not exist; generated reports are untracked in git and should be written there.
- Supporting folders: /skills/stock-market-watcher/references/ for static reference material and /skills/stock-market-watcher/agents/ for any agent-specific scripts or helper logic.

## Workflows & Execution Steps

Run the CLI from the skill's root directory. Historical prices are fetched from Yahoo Finance and saved as dated observations; the command prints a concise result for the Codex conversation and writes a Markdown report under `reports/`.

### Fetch and save a stock snapshot

Trigger this workflow when the user asks for a company's stock value, recent market context, or ongoing tracking that needs a new historical daily-close observation. If the user asks for a live quote, clarify that this workflow returns historical daily closes, not a real-time price.

1. Identify the company and ticker from the request. If the ticker or source is uncertain, verify it with a public market-data source.
2. Extract the requested date range and choose the matching CLI options: `--months <n>` tracks a number of calendar months (default: 1), while `--fromdate YYYY-MM-DD` sets the range's end date (default: today). The month count determines the start date by counting backward from the end date. If no range is specified, use the default last calendar month. Run `python3 scripts/stock_watcher.py price --brand "<brand>" --months <n>` or add `--fromdate YYYY-MM-DD`; for example, `--months 3 --fromdate 2025-11-16` fetches the three months ending November 16, 2025.
3. The command fetches completed regular-session daily closes within that range and appends each observation to `.state/stock_market_watcher.db`. It excludes the current session's daily bar until the regular session is complete. A weekend or holiday check therefore ends at the latest earlier exchange session returned by Yahoo Finance.
4. Rank all daily close-to-close changes by absolute percentage change and select the top 2-3 moves. Search Yahoo Finance only, using the ticker and move date, and retry by ticker and company name when needed. Prefer news published within three days of each selected move; otherwise return the closest available Yahoo Finance result. Store returned articles in `news_items`, associated with the changed day's snapshot. If Yahoo Finance returns no articles or the lookup fails, report that honestly; never invent a result. See `references/yahoo-finance-news.md` for source details.
5. The command prints each selected top move and its matching news headline, available source-provided summary, publication date, and why the article may be relevant. It also writes an initial dated Markdown report with the requested and observed dates, price metrics, daily closes, top moves, linked news, available article summaries, timing relative to each move, and sources.
6. Before presenting the result, add a brief, fact-grounded summary for each linked news item in the report. Explain the verified development and any relevant context, then cautiously note how it may relate to the price move without claiming causation. Preserve the link and source summary; if access or detail is limited, state the limitation rather than inventing facts.
7. Present a concise summary of the relevant news in the Codex conversation, consistent with the detailed report summaries.State the actual observation dates, price currency, and Yahoo Finance as the source; identify the values as historical closing prices.

### Summarize a stock period

Trigger this workflow for phrases such as “summarize Apple’s stock over the last month,” “show Microsoft’s price trend,” or “compare the recent closes for GOOG.” This is a read-and-report operation; it does not add the symbol to the watch registry.

1. Resolve the requested brand or ticker through Yahoo Finance.
2. Fetch completed regular-session daily closes for the requested range.
3. Append every observation to `snapshots` and generate the normal Markdown report.
4. Search and associate Yahoo Finance news for the largest moves, if requested by the normal workflow.
5. Do not create or update a `watched_symbols` row for summarization requests.

Run this directly with:

```bash
python3 scripts/stock_watcher.py summarize --brand "Apple" --months 1
```

For multiple companies, pass `--brand` once per company in the same invocation. If one price fetch fails, record its reason in a separate dated error report and continue fetching the remaining companies.

The legacy `price` command remains available as an alias for `summarize`; it also does not modify the watch registry.

### Watch or track a brand

Trigger this workflow only when the user explicitly requests ongoing tracking, such as “watch Apple,” “track MSFT,” or “add Nvidia to my watched stocks.”

1. Resolve the brand through Yahoo Finance’s public search response.
2. Present the resolved company name and ticker to the user and require explicit confirmation before adding it.
3. Call the watch command with the confirmed ticker. The symbol is written only if the confirmed ticker exactly matches Yahoo Finance’s resolved ticker.
4. Watch mode updates `watched_symbols` only. It does not fetch a chart, create price snapshots, fetch news, or generate a report.

```bash
python3 scripts/stock_watcher.py watch --brand "Apple" --confirmed-ticker "AAPL"
```

Summaries and snapshot fetches must never imply that a stock is being watched. Conversely, a watch request must not silently generate or append historical snapshots.

### Watchlist checks

Use `watch-check --brand "Apple"` to check an active watched symbol. The default threshold is 3%; pass `--threshold-percent` to override it.
For a request covering multiple brands, pass `--brand` once per brand in a single invocation (for example, `watch-check --brand "Apple" --brand "MSFT"`). Process every brand independently so one failed fetch does not prevent the others from being checked.

- A watched symbol begins with `baseline_price` and `baseline_session_date` set to NULL.
- The first successful check saves the latest completed regular-session close and its exchange date as the baseline. It never triggers an alert.
- If the first fetch fails, both baseline fields remain NULL so the next check may establish them.
- If a later fetch fails, leave that symbol's existing `baseline_price` and `baseline_session_date` untouched, report that it was not checked, and continue checking the other requested symbols.
- Write one error report per failed brand under `reports/`, named `[ticker]_[YYYYMMDD]_error.md` (use the brand name if ticker resolution itself failed). Include the brand/ticker, failure reason, and a warning that it was not checked.
- For later checks, calculate signed net change and percentage change from the baseline.
- Trigger a flag only when the percentage is strictly greater than `+threshold` or strictly less than `-threshold`; exactly matching the threshold does not flag.
- Update the baseline only when the latest session date differs from `baseline_session_date` and the prices are comparable.
- Unadjusted comparisons across a stock split are skipped rather than used for an alert or baseline update.

## SQLite Schema

Define these tables in `.state/stock_market_watcher.db`:

- `companies(company_id, name, ticker, created_at)`
- `snapshots(snapshot_id, company_id, captured_at, observed_at, exchange_session_date, price, currency, price_basis)`
- `news_items(news_id, snapshot_id, published_at, title, url, summary)`
- `watched_symbols(watch_id, company_id, added_at, active, baseline_price, baseline_session_date)`

## Output

- SQLite database: `.state/stock_market_watcher.db` for timestamped stock observations and watch registrations. Create `.state/` if it does not exist.
- Markdown report: a generated report file in `reports/`, named `[ticker-name]_[current date in YYYYMMDD].md` (for example, `AAPL_20261122.md`). Create `reports/` if it does not exist.
- Partial-failure report: `reports/[ticker]_[YYYYMMDD]_error.md`, with the failure reason and not-checked warning.
- These are runtime output files and must not be tracked or committed in git.

## Guardrails & Error Handling

- Treat external sources as read-only and use public information only. Do not submit forms, alter remote data, create accounts, place trades, or bypass paywalls, login requirements, rate limits, anti-bot controls, or other access restrictions.
- Never ask for, collect, store, or transmit credentials, API keys, payment details, or brokerage access. If access requires them, stop and explain that the source cannot be used.
- Limit local writes to appending observations to `.state/stock_market_watcher.db` and creating reports under `reports/`. Never stage, commit, or otherwise track these outputs in git, and do not overwrite or delete prior observations.
- Include the source, exchange session date, and capture time for reported prices; distinguish delayed or historical data from live data. Do not infer missing values or present estimates as confirmed facts.
- Clearly report unavailable, ambiguous, or incomplete data; never fabricate results or present failed fetches as successful. Keep conclusions factual, not personalized financial advice or buy/sell recommendations.

`price_basis` has exactly two values: `split-adjusted` or `unadjusted`. Yahoo Finance chart `close` values are stored as `unadjusted` raw closes. For unadjusted data, net or percentage comparisons spanning an identified stock split are omitted and explicitly reported as incomparable. A split-adjusted series may be compared consistently and must state that basis in the report.
