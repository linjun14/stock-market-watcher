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
- Browser/data-fetch dependency: install `playwright` and a Chromium runtime for public market-page and news lookups (`python -m pip install playwright` and `python -m playwright install chromium`).
- Local SQLite state path: /skills/stock-market-watcher/.state/stock_market_watcher.db. This database is intentionally untracked in git and should be treated as local runtime state only.
- Markdown report output path: /skills/stock-market-watcher/reports/. Create the directory if it does not exist; generated reports are untracked in git and should be written there.
- Supporting folders: /skills/stock-market-watcher/references/ for static reference material and /skills/stock-market-watcher/agents/ for any agent-specific scripts or helper logic.

## Workflows & Execution Steps

Run these workflows from the skill's working directory (skills/stock-market-watcher). The steps below describe the intended process; the files under `scripts/` are placeholders, not runnable implementations.

### Fetch and save a stock snapshot

Trigger this workflow when the user asks for a company's current stock value, recent market context, or ongoing tracking that needs a new point-in-time observation.

1. Identify the company and ticker from the request. If the ticker or source is uncertain, verify it with a public market-data source; consult `references/` for source guidance if it is populated.
2. Use the browser/data-fetch capability described in the prerequisites to retrieve the latest available price, timestamp, and relevant market news. Record the sources used.
3. Create `.state/` if needed and append the timestamped observation, company/ticker, and source details to `.state/stock_market_watcher.db`; do not replace earlier observations.
4. Compare the new observation with the previous snapshot, if one exists, and note any significant movement.
5. Write a concise Markdown summary to `reports/`, creating that directory if needed, and present the result and sources to the user.

### Compare snapshots

Trigger this workflow when the user asks how a company's stock changed between two snapshots or time points, or requests a comparison with an earlier observation.

1. Identify the company/ticker and the two requested time points; if the user does not specify both, use the latest observation and the nearest earlier stored observation, and state which were selected.
2. Read matching observations from `.state/stock_market_watcher.db`. If a requested observation is missing, fetch the needed historical data from a public source and save it before comparing.
3. Calculate the absolute and percentage price change between the observations, retaining their timestamps and source details.
4. Write the comparison and any relevant market context to a Markdown report in `reports/`, then present the result to the user.

`README.md` contains the project overview and broader setup guidance. `references/` is reserved for static source guidance, `scripts/` for future runnable helpers, and `agents/openai.yaml` contains agent metadata; none of those placeholder/support files currently implements the workflows above.

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