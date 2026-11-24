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
```

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
```

A newly watched symbol has no baseline until its first successful check. That check saves the latest completed regular-session close and exchange date without triggering an alert. Later checks compare against that baseline, using a strict threshold: exactly `+3%` or `-3%` does not flag, while values beyond those limits do. On a new comparable session, the baseline is updated to the latest close and session date.
