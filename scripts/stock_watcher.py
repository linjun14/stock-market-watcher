#!/usr/bin/env python3

# Standard-library modules for the CLI, SQLite storage, and filesystem paths.
import argparse
from datetime import datetime, timezone
import json
import sqlite3
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


# Keep runtime files beside the repository, regardless of the caller's cwd.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATE_DIR_NAME = ".state"
REPORTS_DIR_NAME = "reports"
DATABASE_NAME = "stock_market_watcher.db"


# Create the tables used for companies, data sources, price snapshots, and news.
SCHEMA = """
-- Store each tracked company and its unique ticker.
CREATE TABLE IF NOT EXISTS companies (
    company_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    ticker TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

-- Record the public sources used to retrieve prices or news.
CREATE TABLE IF NOT EXISTS sources (
    source_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    url TEXT NOT NULL UNIQUE
);

-- Keep timestamped price observations linked to their company and source.
CREATE TABLE IF NOT EXISTS snapshots (
    snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL,
    source_id INTEGER NOT NULL,
    captured_at TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    price REAL NOT NULL,
    currency TEXT NOT NULL,
    FOREIGN KEY (company_id) REFERENCES companies(company_id),
    FOREIGN KEY (source_id) REFERENCES sources(source_id)
);

-- Associate published news items with the snapshot and source they support.
CREATE TABLE IF NOT EXISTS news_items (
    news_id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id INTEGER NOT NULL,
    source_id INTEGER NOT NULL,
    published_at TEXT,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    summary TEXT,
    FOREIGN KEY (snapshot_id) REFERENCES snapshots(snapshot_id),
    FOREIGN KEY (source_id) REFERENCES sources(source_id)
);
"""


def initialize_project(project_root: Path | None = None) -> Path:
    """Create runtime folders and initialize the SQLite database."""
    # Allow tests or callers to target a temporary project directory.
    root = (project_root or PROJECT_ROOT).resolve()
    state_dir = root / STATE_DIR_NAME
    reports_dir = root / REPORTS_DIR_NAME

    # Repeated initialization should leave existing directories and data intact.
    state_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)

    # Create missing tables without replacing any existing database contents.
    database_path = state_dir / DATABASE_NAME
    with sqlite3.connect(database_path) as connection:
        # Enforce the foreign-key relationships defined in the schema.
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(SCHEMA)

    # Return the database location for the CLI and other callers.
    return database_path


def build_parser() -> argparse.ArgumentParser:
    """Define the command-line arguments accepted by this script."""
    parser = argparse.ArgumentParser(
        description="Initialize and run the stock market watcher."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "init",
        help="initialize the .state and reports directories",
    )
    price_parser = commands.add_parser(
        "price",
        help="fetch the latest available stock price for a brand or company",
    )
    price_parser.add_argument(
        "--brand",
        required=True,
        help="company or brand name (or stock ticker)",
    )
    return parser


def fetch_json(url: str) -> dict:
    """Fetch a JSON response from a public market-data endpoint."""
    request = Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; StockMarketWatcher/1.0)"},
    )
    try:
        with urlopen(request, timeout=15) as response:
            return json.load(response)
    except HTTPError as error:
        raise RuntimeError(f"Market data request failed (HTTP {error.code}).") from error
    except URLError as error:
        raise RuntimeError(
            f"Could not reach the market data service: {error.reason}"
        ) from error
    except json.JSONDecodeError as error:
        raise RuntimeError("The market data service returned invalid JSON.") from error


def fetch_latest_price(brand: str) -> str:
    """Resolve a brand to a ticker and format its latest reported quote."""
    search_url = "https://query1.finance.yahoo.com/v1/finance/search?" + urlencode(
        {"q": brand, "quotesCount": 10, "newsCount": 0}
    )
    search_data = fetch_json(search_url)
    quotes = search_data.get("quotes", [])
    equity_matches = [
        quote
        for quote in quotes
        if quote.get("quoteType") == "EQUITY" and quote.get("symbol")
    ]
    if not equity_matches:
        raise ValueError(f"No publicly traded stock was found for {brand!r}.")

    match = equity_matches[0]
    ticker = match["symbol"]
    chart_url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?"
        + urlencode({"range": "1d", "interval": "1m"})
    )
    chart_data = fetch_json(chart_url)
    chart = chart_data.get("chart", {})
    if chart.get("error"):
        raise ValueError(f"Could not retrieve a quote for {ticker}.")
    results = chart.get("result") or []
    if not results:
        raise ValueError(f"No latest quote is available for {ticker}.")

    metadata = results[0].get("meta", {})
    price = metadata.get("regularMarketPrice")
    observed_at = metadata.get("regularMarketTime")
    if not isinstance(price, (int, float)) or not isinstance(observed_at, (int, float)):
        raise ValueError(f"The latest quote for {ticker} is incomplete.")

    precision = metadata.get("priceHint", 2)
    if not isinstance(precision, int) or not 0 <= precision <= 8:
        precision = 2
    price_text = f"{price:,.{precision}f}"
    timestamp = datetime.fromtimestamp(observed_at, timezone.utc).strftime(
        "%Y-%m-%d %H:%M UTC"
    )
    company = metadata.get("longName") or metadata.get("shortName") or brand
    currency = metadata.get("currency") or "unknown currency"
    return (
        f"{company} ({ticker}): {price_text} {currency} "
        f"(latest reported {timestamp}; source: Yahoo Finance)"
    )


def main() -> int:
    """Parse the requested command and execute its action."""
    args = build_parser().parse_args()

    if args.command == "init":
        # Initialize the project and report the paths created or reused.
        database_path = initialize_project()
        print(f"Initialized .state/ at {PROJECT_ROOT / STATE_DIR_NAME}")
        print(f"Initialized reports/ at {PROJECT_ROOT / REPORTS_DIR_NAME}")
        print(f"Initialized SQLite database at {database_path}")
        return 0

    if args.command == "price":
        try:
            print(fetch_latest_price(args.brand))
        except (RuntimeError, ValueError, OSError) as error:
            print(f"Error: {error}", file=sys.stderr)
            return 1
        return 0

    return 2


# Run the CLI only when this file is executed directly, not when imported.
if __name__ == "__main__":
    raise SystemExit(main())
