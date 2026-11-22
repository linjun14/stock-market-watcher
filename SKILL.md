---
name: stock-market-watcher
description: Use only when the user explicitly asks about a named company's public stock price, stock performance or trend, historical stock-price comparison, or stock monitoring. Extract the company or ticker from the request; do not activate for unrelated requests that merely mention a company, brand, or market.
---

# Stock Market Watcher

This skill tracks a publicly traded company by name or ticker, records a timestamped price snapshot in local SQLite, and compares it with earlier observations to explain how the stock moved over time. It fetches the latest market value, keeps source metadata, and can turn the result into a brief Markdown trend summary for the user.

## Overview

Trigger this skill only when the user explicitly asks for public stock-price information or stock performance: for example, a current quote, a stock trend, a comparison between dates or saved snapshots, or monitoring price changes. A company or brand name alone is not a trigger. Do not trigger for general company/brand research, product questions, writing or coding tasks, broad market discussion without a requested stock lookup, or incidental company mentions.

## Request Interpretation

When the trigger criteria are met, read the user's message and extract the company or ticker they are asking about. Preserve the requested company name, and resolve it to a ticker using a public market-data source rather than guessing. If multiple companies or tickers are plausible, ask which one they mean before fetching data. Extract date ranges or comparison points only when requested; otherwise follow the workflow's stated default. Do not treat quoted text, examples, or incidental company names as the requested subject.

Do not use this skill for non-financial questions, requests to perform trades, speculative investment advice, or requests that require detailed legal, accounting, or personalized financial guidance.

## Prerequisites & Environment
- Expected working directory for the skill: skills/stock-market-watcher
- Required runtime: Python 3.10+; use the Python standard library modules `sqlite3`, `argparse`, `json`, and `pathlib` without installing additional packages for basic persistence and file handling.
- The current CLI uses Python's standard library to request public Yahoo Finance JSON endpoints; no browser automation or package installation is required.
- Local SQLite state path: /skills/stock-market-watcher/.state/stock_market_watcher.db. This database is intentionally untracked in git and should be treated as local runtime state only.
- Markdown report output path: /skills/stock-market-watcher/reports/. Create the directory if it does not exist; generated reports are untracked in git and should be written there.
- Supporting folders: /skills/stock-market-watcher/references/ for static reference material and /skills/stock-market-watcher/agents/ for any agent-specific scripts or helper logic.

## Workflows & Execution Steps

Run the CLI from the skill's root directory. Historical prices are fetched from Yahoo Finance and saved as dated observations; the command prints a concise result for the Codex conversation and writes a Markdown report under `reports/`.

### Fetch and save a stock snapshot

Trigger this workflow when the user asks for a company's current stock value, recent market context, or ongoing tracking that needs a new point-in-time observation.

1. Identify the company and ticker from the request. If the ticker or source is uncertain, verify it with a public market-data source; consult `references/` for source guidance if it is populated.
2. Extract the requested date range. If the user specifies a number of months, run `python3 scripts/stock_watcher.py price --brand "<brand>" --months <n>`. If they specify a start date, use `--fromdate YYYY-MM-DD`. If no range is specified, omit both options; the default is the last calendar month. The options cannot be combined.
3. Use `--months <n>` for the number of calendar months to track (default: 1). `--fromdate YYYY-MM-DD` sets the range's end date (default: today); the start date is that many months before the end date. For example, `--months 3 --fromdate 2025-11-16` fetches the three months ending November 16, 2025.
4. The command fetches daily closing prices within that range and appends each observation to `.state/stock_market_watcher.db`.
5. For each daily close-to-close change of at least 3% in either direction, search Yahoo Finance and Google News by ticker and ticker plus move date for news published within three days of the move. If those searches return no articles, retry by company name and return the closest available company coverage. Store returned articles in `news_items`, associated with the changed day's snapshot. If sources return no articles or the lookup fails, report that honestly; never invent a result.
6. The command prints each significant move and its matching news headline, available summary, publication date, and why the article may be relevant. It also writes a dated Markdown report with the requested and observed dates, price metrics, daily closes, significant moves, linked news, article summaries, timing relative to each move, and sources.
7. Present a brief summary of the relevant news in the Codex conversation and explain its possible connection to the price change using its topic and publication timing. Be clear that temporal association is not proof of causation. State the actual observation dates, price currency, and Yahoo Finance as the source; identify the values as historical closing prices.

### Compare snapshots

Trigger this workflow when the user asks how a company's stock changed over a date range or between two snapshots or time points.

1. For a requested date range, follow the historical-price workflow above. For a comparison of saved snapshots, identify the requested points and use stored observations where available.
2. If a requested stored observation is missing, fetch historical data from a public source and save it before comparing.
3. Calculate absolute and percentage price change between the selected observations, retaining their dates and source details.
4. Present the result and source to the user, and write a Markdown report in `reports/`.

`README.md` contains the project overview and setup guidance. `scripts/stock_watcher.py` initializes the local database and fetches and stores historical closing prices with the `price` command. `references/` is reserved for static source guidance, and `agents/openai.yaml` contains agent metadata.

## SQLite Schema

Define these tables in `.state/stock_market_watcher.db`:

- `companies(company_id, name, ticker, created_at)`
- `sources(source_id, name, url)`
- `snapshots(snapshot_id, company_id, source_id, captured_at, observed_at, price, currency)`
- `news_items(news_id, snapshot_id, source_id, published_at, title, url, summary)`

Each `snapshots` row represents one price observation, including its source and both collection and market observation timestamps.

## Output

- SQLite database: `.state/stock_market_watcher.db` for timestamped stock observations and source details. Create `.state/` if it does not exist.
- Markdown report: a generated report file in `reports/`. Create `reports/` if it does not exist.
- These are runtime output files and must not be tracked or committed in git.

## Guardrails & Error Handling

- Treat external sources as read-only and use public information only. Do not submit forms, alter remote data, create accounts, place trades, or bypass paywalls, login requirements, rate limits, anti-bot controls, or other access restrictions.
- Never ask for, collect, store, or transmit credentials, API keys, payment details, or brokerage access. If access requires them, stop and explain that the source cannot be used.
- Limit local writes to appending observations to `.state/stock_market_watcher.db` and creating reports under `reports/`. Never stage, commit, or otherwise track these outputs in git, and do not overwrite or delete prior observations.
- Include the source and observation time for reported prices; distinguish delayed or historical data from live data. Do not infer missing values or present estimates as confirmed facts.
- Clearly report unavailable, ambiguous, or incomplete data; never fabricate results or present failed fetches as successful. Keep conclusions factual, not personalized financial advice or buy/sell recommendations.