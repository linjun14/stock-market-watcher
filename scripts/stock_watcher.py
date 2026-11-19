#!/usr/bin/env python3

# Standard-library modules for the CLI, SQLite storage, and filesystem paths.
import argparse
import calendar
from contextlib import closing
from datetime import date, datetime, time, timedelta, timezone
import json
import sqlite3
import sys
from pathlib import Path
from statistics import fmean, pstdev
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
    with closing(sqlite3.connect(database_path)) as connection, connection:
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
    price_parser.add_argument(
        "--months",
        type=positive_integer,
        default=1,
        help="number of calendar months to track (default: 1)",
    )
    price_parser.add_argument(
        "--fromdate",
        type=parse_date,
        default=datetime.now(timezone.utc).date(),
        help="end date in YYYY-MM-DD format (default: today)",
    )
    return parser


def positive_integer(value: str) -> int:
    """Parse a positive integer argument."""
    try:
        number = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a positive whole number") from error
    if number < 1:
        raise argparse.ArgumentTypeError("must be a positive whole number")
    return number


def parse_date(value: str) -> date:
    """Parse an ISO calendar date supplied on the command line."""
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("use YYYY-MM-DD format") from error


def subtract_months(value: date, months: int) -> date:
    """Subtract calendar months, clamping to the last valid day of the month."""
    month_index = value.year * 12 + value.month - 1 - months
    year, month_offset = divmod(month_index, 12)
    month = month_offset + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def get_date_range(months: int, fromdate: date) -> tuple[date, date]:
    """Return the start and end dates for the requested month window."""
    today = datetime.now(timezone.utc).date()
    if fromdate > today:
        raise ValueError("The end date cannot be in the future.")
    return subtract_months(fromdate, months), fromdate


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


def fetch_stock_history(brand: str, start_date: date, end_date: date) -> dict:
    """Resolve a brand and return daily closes within the requested date range."""
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
    period1 = int(datetime.combine(start_date, time.min, timezone.utc).timestamp())
    period2 = int(
        datetime.combine(end_date + timedelta(days=1), time.min, timezone.utc).timestamp()
    )
    chart_url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?" + urlencode(
        {"period1": period1, "period2": period2, "interval": "1d"}
    )
    chart_data = fetch_json(chart_url)
    chart = chart_data.get("chart", {})
    if chart.get("error"):
        raise ValueError(f"Could not retrieve a quote for {ticker}.")
    results = chart.get("result") or []
    if not results:
        raise ValueError(f"No latest quote is available for {ticker}.")

    metadata = results[0].get("meta", {})
    timestamps = results[0].get("timestamp") or []
    quote_data = results[0].get("indicators", {}).get("quote", [{}])[0]
    closes = quote_data.get("close", [])
    observations = [
        {
            "price": price,
            "observed_at": datetime.fromtimestamp(timestamp, timezone.utc).isoformat(),
        }
        for timestamp, price in zip(timestamps, closes)
        if isinstance(price, (int, float))
    ]
    if not observations:
        raise ValueError(f"No historical closing prices are available for {ticker}.")

    return {
        "brand": brand,
        "ticker": ticker,
        "company": metadata.get("longName") or metadata.get("shortName") or brand,
        "currency": metadata.get("currency") or "UNKNOWN",
        "price_hint": metadata.get("priceHint", 2),
        "observations": observations,
    }


def store_snapshot(quote: dict, database_path: Path) -> int:
    """Append a quote to the database and return its snapshot ID."""
    captured_at = datetime.now(timezone.utc).isoformat()
    source_name = "Yahoo Finance"
    source_url = "https://query1.finance.yahoo.com/"

    with closing(sqlite3.connect(database_path)) as connection, connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            """
            INSERT INTO companies (name, ticker, created_at)
            VALUES (?, ?, ?)
            ON CONFLICT(ticker) DO UPDATE SET name = excluded.name
            """,
            (quote["company"], quote["ticker"], captured_at),
        )
        company_id = connection.execute(
            "SELECT company_id FROM companies WHERE ticker = ?",
            (quote["ticker"],),
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO sources (name, url) VALUES (?, ?) "
            "ON CONFLICT(url) DO UPDATE SET name = excluded.name",
            (source_name, source_url),
        )
        source_id = connection.execute(
            "SELECT source_id FROM sources WHERE url = ?",
            (source_url,),
        ).fetchone()[0]
        cursor = connection.execute(
            """
            INSERT INTO snapshots (
                company_id, source_id, captured_at, observed_at, price, currency
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                company_id,
                source_id,
                captured_at,
                quote["observed_at"],
                quote["price"],
                quote["currency"],
            ),
        )
        return cursor.lastrowid


def format_quote(quote: dict) -> str:
    """Format the requested period's price change for display."""
    precision = quote["price_hint"]
    if not isinstance(precision, int) or not 0 <= precision <= 8:
        precision = 2
    first = quote["observations"][0]
    last = quote["observations"][-1]
    net_change = last["price"] - first["price"]
    percent_change = net_change / first["price"] * 100 if first["price"] else 0
    start_text = datetime.fromisoformat(first["observed_at"]).strftime("%Y-%m-%d")
    end_text = datetime.fromisoformat(last["observed_at"]).strftime("%Y-%m-%d")
    return (
        f"{quote['company']} ({quote['ticker']}): net change "
        f"{net_change:+,.{precision}f} {quote['currency']} ({percent_change:+.2f}%) "
        f"from {start_text} to {end_text}; "
        f"{len(quote['observations'])} daily closing prices (source: Yahoo Finance)"
    )


def write_report(
    quote: dict,
    report_dir: Path,
    requested_start: date,
    requested_end: date,
) -> Path:
    """Write a quantitative Markdown summary for the requested date range."""
    precision = quote["price_hint"]
    if not isinstance(precision, int) or not 0 <= precision <= 8:
        precision = 2
    observations = sorted(quote["observations"], key=lambda item: item["observed_at"])
    first = observations[0]
    last = observations[-1]
    net_change = last["price"] - first["price"]
    percent_change = net_change / first["price"] * 100 if first["price"] else None
    highest = max(observations, key=lambda item: item["price"])
    lowest = min(observations, key=lambda item: item["price"])
    daily_returns = []
    for previous, current in zip(observations, observations[1:]):
        if previous["price"]:
            daily_returns.append(
                (current["price"] - previous["price"]) / previous["price"] * 100
            )
    volatility = pstdev(daily_returns) if len(daily_returns) > 1 else None

    def price_text(value: float) -> str:
        return f"{value:,.{precision}f} {quote['currency']}"

    def observed_date(observation: dict) -> str:
        return datetime.fromisoformat(observation["observed_at"]).strftime("%Y-%m-%d")

    lines = [
        f"# {quote['company']} ({quote['ticker']}) stock performance",
        "",
        f"- Requested period: {requested_start.isoformat()} to {requested_end.isoformat()}",
        f"- Available data: {observed_date(first)} to {observed_date(last)}",
        f"- Source: Yahoo Finance historical daily closing prices",
        f"- Observations: {len(observations)} daily closes",
        "",
        "## Summary Metrics",
        "",
        f"- Starting close ({observed_date(first)}): {price_text(first['price'])}",
        f"- Ending close ({observed_date(last)}): {price_text(last['price'])}",
        f"- Net change: {price_text(last['price'])} - {price_text(first['price'])} = "
        f"{net_change:+,.{precision}f} {quote['currency']}",
        (
            f"- Percentage change: ({net_change:+,.{precision}f} / "
            f"{first['price']:,.{precision}f}) * 100 = {percent_change:+.2f}%"
            if percent_change is not None
            else "- Percentage change: N/A (starting close is zero)"
        ),
        f"- Highest close: {price_text(highest['price'])} ({observed_date(highest)})",
        f"- Lowest close: {price_text(lowest['price'])} ({observed_date(lowest)})",
        f"- Mean close: {price_text(fmean(item['price'] for item in observations))}",
        (
            f"- Daily return volatility (population standard deviation): {volatility:.2f}%"
            if volatility is not None
            else "- Daily return volatility: N/A (fewer than two daily returns)"
        ),
        "",
        "## Daily Closing Prices",
        "",
        f"| Date | Close ({quote['currency']}) | Change ({quote['currency']}) | Daily return |",
        "| --- | ---: | ---: | ---: |",
    ]
    previous = None
    for observation in observations:
        if previous is None:
            change_text = "N/A"
            return_text = "N/A"
        else:
            change = observation["price"] - previous["price"]
            daily_return = (
                change / previous["price"] * 100 if previous["price"] else None
            )
            change_text = f"{change:+,.{precision}f}"
            return_text = f"{daily_return:+.2f}%" if daily_return is not None else "N/A"
        lines.append(
            f"| {observed_date(observation)} | {observation['price']:,.{precision}f} "
            f"| {change_text} | {return_text} |"
        )
        previous = observation

    report_dir.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(timezone.utc)
    report_path = report_dir / (
        f"{quote['ticker']}_{generated_at.strftime('%Y%m%dT%H%M%SZ')}.md"
    )
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report_path


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
            database_path = initialize_project()
            start_date, end_date = get_date_range(args.months, args.fromdate)
            quote = fetch_stock_history(args.brand, start_date, end_date)
            for observation in quote["observations"]:
                store_snapshot({**quote, **observation}, database_path)
            report_path = write_report(
                quote,
                PROJECT_ROOT / REPORTS_DIR_NAME,
                start_date,
                end_date,
            )
            print(f"{format_quote(quote)}")
            print(f"Report: {report_path.relative_to(PROJECT_ROOT)}")
        except (RuntimeError, ValueError, OSError, sqlite3.Error) as error:
            print(f"Error: {error}", file=sys.stderr)
            return 1
        return 0

    return 2


# Run the CLI only when this file is executed directly, not when imported.
if __name__ == "__main__":
    raise SystemExit(main())
