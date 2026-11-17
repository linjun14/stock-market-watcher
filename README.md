# Stock Market watcher

This skill turns natural-language prompts about brands and market trends into persistent stock snapshots in the database, then creates a concise Markdown report so users can understand a brand without searching across multiple websites and URLs.

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
- Computes key performance metrics, including net price change and daily volatility, and provides insights into the brand's market health.
- Stores point-in-time stock price observations in a local SQLite database.
- Compares newly fetched prices with previous snapshots for the brand and flags brands with significant changes.
- Compiles findings into structured Markdown summary reports.
- Provides runnable Python scripts that turn parsed parameters into repeatable execution steps.

## Prerequisites & Setup

### Requirements

- Python 3.x. Python 3.10 or newer is recommended.
- The Python `playwright` package and a Playwright browser (Chromium) for browser-based lookups.
- Internet access to visit public market-data and news sources. The sources themselves may require accounts, block automated browsing, or change their pages.
- Write access to the project directory for the SQLite database and generated Markdown reports. Use the project-local `reports/` directory for reports; create it if it does not exist.

Python's standard library includes `argparse` for command-line arguments, `sqlite3` for persistence, and `math`/`statistics` for common metric calculations. These do not need separate installation. The repository currently has no dependency manifest, and its scripts are not implemented yet, so additional packages may be required once their data sources and calculations are defined.

### Setup

From the repository root, create and activate a virtual environment, then install Playwright and its Chromium browser:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install playwright
python -m playwright install chromium
mkdir -p reports
```

On Windows, activate the environment with `.venv\Scripts\activate` in Command Prompt or `.venv\Scripts\Activate.ps1` in PowerShell. Create the reports directory with `mkdir reports`.

On Linux, if the browser reports missing operating-system libraries, install the required browser dependencies with:

```bash
python -m playwright install --with-deps chromium
```

Check that Python and Playwright are available:

```bash
python --version
python -c "import playwright; print('Playwright is installed')"
```

## End-to-End Workflows

### Track a Brand and Review Market Trends

Example user input:

> Summarize Google's market trends from 2026-01-01 to 2026-06-01.

The workflow:

1. Parse the natural-language request to identify the brand, requested date range, and whether a comparison with previous snapshots is needed. If no date range is provided, use the last month.
2. Map the brand name to its standardized stock ticker symbol (for example, Google to GOOG).
3. Select appropriate public market-data and news sources for the ticker, and save the sources with the watched brand so later updates can use them.
4. Fetch historical prices for the requested period and relevant news about notable price changes.
5. Calculate trend metrics such as net price change, percentage change, and daily volatility.
6. Store the brand, ticker, source information, and point-in-time price observations in the local SQLite database. Append new observations to the existing history rather than replacing earlier snapshots.
7. When previous snapshots are available or the request asks for a review, compare the latest prices and calculated changes with prior observations and flag notable movements (for example, changes over 3%).
8. Write a formatted Markdown report with the sources, price trends, metrics, relevant news, and any comparison findings, then display the report in chat.

## What this Skill does not do

This skill package does not:

- Place trades or stock orders, or connect to live brokerage accounts.
- Send private search queries, financial histories, or tracking profiles to third-party services. Public-source requests should include only the information needed to retrieve the requested market data or news.
- Scrape websites or make unauthorized writes to external services. Prefer public market-data sources and use only access methods permitted by the source.
- Bypass paywalls, rate limits, authentication, or other access controls on restricted financial services.
- Perform a ticker lookup or data-fetch failure for one brand should be recorded and reported without stopping the rest of a batch when other symbols can still be processed.