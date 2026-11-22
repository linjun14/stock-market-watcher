# Stock Market Watcher

This skill handles explicit requests about a named company's public stock price or performance. It identifies the requested company or ticker, records timestamped stock snapshots in the database, and can create a concise Markdown report. A company or brand mention by itself does not activate the skill.

## Why this Skill Exists

This skill is a workflow, not just a one-step prompt. A single AI chat can give a current price, but it does not automatically store the data, compare it with earlier snapshots, or build a reusable market summary. This skill handles those intermediate steps: identifying the brand and ticker, fetching the right sources, parsing the data, saving snapshots, and comparing changes over time.

That makes it useful for ongoing market tracking rather than a one-off answer.

## Who it is for

Students can learn how a Codex-compatible skill works by following this skill’s workflow and seeing how persistent state, browser lookups, and Markdown report generation each contribute to the final result.

It is also for individuals interested in stock prices, researchers, and investors who want lightweight, automated market tracking, historical price comparisons, and summarized brand-related events from the command line or an agent chat.

It is especially useful for people who want to:

- Keep up with stock prices and relevant news without gathering information from multiple sources themselves
- Monitor live prices and be alerted when a watched stock moves beyond a chosen threshold (eg: a 3% change in stock value)
- Review a clear report of a brand’s stock value at a specific point in time

## Key Features

- Extracts brand or company names and optional date ranges from natural-language prompts. If no date range is specified, it defaults to the last month.
- Resolves brand names to public financial data sources and uses a browser to fetch live market pages.
- Reports stock prices at a specific point in time, and fetches historical prices, trends, and related events.
- Reports historical closing prices and quantitative trends for a requested date range.
- Computes net price change, percentage change, mean, extrema, and daily return volatility.
- Stores point-in-time stock price observations in a local SQLite database.
- Compares newly fetched prices with previous snapshots for the brand and flags brands with significant changes.
- Compiles findings into structured Markdown summary reports.
- Provides runnable Python scripts that turn parsed parameters into repeatable execution steps.

## Prerequisites & Setup

### Requirements

- Python 3.10 or newer.
- Internet access to retrieve quote data from Yahoo Finance.
- Write access to the project directory for the SQLite database.

The current command-line script uses only the Python standard library; no package installation is required.

### Setup

From the repository root, initialize the local SQLite database:

```bash
python3 scripts/stock_watcher.py init
```

The database is created at `.state/stock_market_watcher.db`; runtime data is excluded from git.

### Fetch and store a price snapshot

Fetch daily closing prices for the last calendar month ending today (the default) and append them to the database:

```bash
python3 scripts/stock_watcher.py price --brand "Microsoft"
```

Each successful `price` command stores the available daily closes in `snapshots`, linked to the company and Yahoo Finance source, with both the capture time and the market's observation time. Previous snapshots are retained. The command prints the net price and percentage change between the first and last available closes, along with significant moves and any related news headlines, source-provided summaries, publication timing, and cautious relevance context for the Codex conversation. It writes an initial Markdown report in `reports/` with date coverage, price metrics, every retrieved close, and available details for each related article. When run through the Codex skill, the report is then enriched with roughly two to three paragraphs of source-verified detail for each news item presented in the conversation; the standalone script only has access to source-provided article summaries. Daily close-to-close moves of 3% or more (up or down) are highlighted; the script searches Yahoo Finance and Google News by ticker and ticker plus move date for news published within three days of each move. If those searches return no articles, it retries by company name and uses the closest available company coverage. Returned articles and their source links are included in the report and stored in `news_items`. The report distinguishes possible context from proven causation and explicitly notes when no article is returned or news lookup fails. An internet connection is required.

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
3. Select the public market-data source for the ticker.
4. Fetch historical daily closing prices for the requested period.
5. Calculate trend metrics such as net price change, percentage change, and daily volatility.
6. Store the brand, ticker, source information, and point-in-time price observations in the local SQLite database. Append new observations to the existing history rather than replacing earlier snapshots.
7. When previous snapshots are available or the request asks for a review, compare the latest prices and calculated changes with prior observations and flag notable movements (for example, changes over 3%).
8. For each close-to-close move of at least 3%, search public Yahoo Finance news around that date and associate matching articles with that day's snapshot.
9. Write a Markdown report with the sources, price trends, significant daily moves, related news (or an explicit unavailable/no-match note), and comparison findings.

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