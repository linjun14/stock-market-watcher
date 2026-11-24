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
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


# Keep runtime files beside the repository, regardless of the caller's cwd.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATE_DIR_NAME = ".state"
REPORTS_DIR_NAME = "reports"
DATABASE_NAME = "stock_market_watcher.db"
TOP_PRICE_CHANGE_COUNT = 3
NEWS_DATE_WINDOW_DAYS = 3


# Create the tables used for companies, price snapshots, news, and watched symbols.
SCHEMA = """
-- Store each tracked company and its unique ticker.
CREATE TABLE IF NOT EXISTS companies (
    company_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    ticker TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

-- Keep timestamped price observations linked to their company.
CREATE TABLE IF NOT EXISTS snapshots (
    snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL,
    captured_at TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    exchange_session_date TEXT NOT NULL,
    price REAL NOT NULL,
    currency TEXT NOT NULL,
    price_basis TEXT NOT NULL DEFAULT 'unadjusted'
        CHECK (price_basis IN ('split-adjusted', 'unadjusted')),
    FOREIGN KEY (company_id) REFERENCES companies(company_id),
    UNIQUE(company_id, exchange_session_date, currency, price_basis)
);

-- Associate published news items with Yahoo-linked snapshots.
CREATE TABLE IF NOT EXISTS news_items (
    news_id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id INTEGER NOT NULL,
    published_at TEXT,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    summary TEXT,
    FOREIGN KEY (snapshot_id) REFERENCES snapshots(snapshot_id)
);

-- Symbols explicitly requested for ongoing tracking. Summaries do not write here.
CREATE TABLE IF NOT EXISTS watched_symbols (
    watch_id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL,
    ticker TEXT NOT NULL,
    added_at TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    baseline_price REAL,
    baseline_session_date TEXT,
    UNIQUE(ticker),
    FOREIGN KEY (company_id) REFERENCES companies(company_id)
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
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.executescript(SCHEMA)
        for table_name in ("snapshots", "news_items", "watched_symbols"):
            exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table_name,),
            ).fetchone()
            if exists:
                columns = {
                    row[1]
                    for row in connection.execute(f"PRAGMA table_info({table_name})")
                }
                if "source_id" in columns:
                    connection.execute(f"ALTER TABLE {table_name} DROP COLUMN source_id")
        connection.execute("DROP TABLE IF EXISTS sources")
        connection.execute("PRAGMA foreign_keys = ON")

        snapshot_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(snapshots)")
        }
        if "exchange_session_date" not in snapshot_columns:
            connection.execute(
                "ALTER TABLE snapshots ADD COLUMN exchange_session_date TEXT"
            )
            connection.execute(
                """
                UPDATE snapshots
                SET exchange_session_date = substr(observed_at, 1, 10)
                WHERE exchange_session_date IS NULL
                """
            )
        if "price_basis" not in snapshot_columns:
            connection.execute(
                "ALTER TABLE snapshots ADD COLUMN price_basis TEXT "
                "NOT NULL DEFAULT 'unadjusted' "
                "CHECK (price_basis IN ('split-adjusted', 'unadjusted'))"
            )

        watch_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(watched_symbols)")
        }
        if "baseline_price" not in watch_columns:
            connection.execute("ALTER TABLE watched_symbols ADD COLUMN baseline_price REAL")
        if "baseline_session_date" not in watch_columns:
            connection.execute("ALTER TABLE watched_symbols ADD COLUMN baseline_session_date TEXT")

        connection.execute(
            """
            DELETE FROM snapshots
            WHERE snapshot_id NOT IN (
                SELECT MIN(snapshot_id)
                FROM snapshots
                GROUP BY company_id, exchange_session_date, currency, price_basis
            )
            """
        )
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_snapshots_one_per_session
            ON snapshots(company_id, exchange_session_date, currency, price_basis)
            """
        )

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
    for command_name, help_text in (
        (
            "summarize",
            "fetch and report historical prices without changing the watch list",
        ),
        (
            "watch",
            "resolve and confirm a symbol, then add it to the watch registry",
        ),
    ):
        command_parser = commands.add_parser(command_name, help=help_text)
        command_parser.add_argument("--brand", required=True,
                                    help="company or brand name (or stock ticker)")
        command_parser.add_argument("--months", type=positive_integer, default=1,
                                    help="number of calendar months to track (default: 1)")
        command_parser.add_argument("--fromdate", type=parse_date,
                                    default=datetime.now(timezone.utc).date(),
                                    help="end date in YYYY-MM-DD format (default: today)")
        if command_name == "watch":
            command_parser.add_argument(
                "--confirmed-ticker",
                required=True,
                help="ticker explicitly confirmed against Yahoo Finance",
            )

    # Backward-compatible alias: price performs summary only and never watches.
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

    check_parser = commands.add_parser(
        "watch-check",
        help="check a watched brand against its stored baseline",
    )
    check_parser.add_argument("--brand", required=True,
                              help="watched company or brand name (or ticker)")
    check_parser.add_argument("--threshold-percent", type=percentage,
                              default=3.0,
                              help="strict absolute percentage threshold (default: 3.0)")
    return parser


def percentage(value: str) -> float:
    """Parse a finite, non-negative percentage threshold."""
    try:
        number = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a number") from error
    if number != number or number in (float("inf"), float("-inf")):
        raise argparse.ArgumentTypeError("must be finite")
    if number < 0:
        raise argparse.ArgumentTypeError("must be greater than or equal to zero")
    return number


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
    resolved = resolve_equity(brand)
    ticker = resolved["ticker"]
    period1 = int(datetime.combine(start_date, time.min, timezone.utc).timestamp())
    period2 = int(
        datetime.combine(end_date + timedelta(days=1), time.min, timezone.utc).timestamp()
    )
    chart_url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?" + urlencode(
        {"period1": period1, "period2": period2, "interval": "1d", "events": "splits"}
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
    timezone_name = metadata.get("exchangeTimezoneName")
    if not timezone_name:
        raise ValueError(
            f"Yahoo Finance did not provide an exchange timezone for {ticker}."
        )
    try:
        exchange_timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as error:
        raise ValueError(
            f"Yahoo Finance returned an unknown exchange timezone for {ticker}."
        ) from error

    regular_period = (
        metadata.get("currentTradingPeriod") or {}
    ).get("regular") or {}
    regular_start = regular_period.get("start")
    regular_end = regular_period.get("end")
    current_period_date = (
        datetime.fromtimestamp(regular_start, exchange_timezone).date()
        if isinstance(regular_start, (int, float))
        else datetime.now(exchange_timezone).date()
    )
    regular_session_open = (
        not isinstance(regular_end, (int, float))
        or datetime.now(timezone.utc)
        < datetime.fromtimestamp(regular_end, timezone.utc)
    )
    observations = []
    for timestamp, price in zip(timestamps, closes):
        if not isinstance(price, (int, float)):
            continue
        session_date = datetime.fromtimestamp(timestamp, exchange_timezone).date()
        if regular_session_open and session_date == current_period_date:
            continue
        observations.append(
            {
                "price": price,
                "observed_at": datetime.fromtimestamp(
                    timestamp, timezone.utc
                ).isoformat(),
                "exchange_session_date": session_date.isoformat(),
                "price_basis": "unadjusted",
            }
        )
    if not observations:
        raise ValueError(f"No historical closing prices are available for {ticker}.")

    return {
        "brand": brand,
        "ticker": ticker,
        "company": metadata.get("longName") or metadata.get("shortName") or brand,
        "currency": metadata.get("currency") or "UNKNOWN",
        "price_hint": metadata.get("priceHint", 2),
        "price_basis": "unadjusted",
        "split_dates": extract_split_dates(results[0]),
        "observations": observations,
    }


def extract_split_dates(chart_result: dict) -> list[str]:
    """Return split event dates from a Yahoo chart result."""
    events = chart_result.get("events") or {}
    splits = events.get("splits") or {}
    dates = []
    for value in splits.values() if isinstance(splits, dict) else []:
        timestamp = value.get("date") if isinstance(value, dict) else None
        if isinstance(timestamp, (int, float)):
            dates.append(datetime.fromtimestamp(timestamp, timezone.utc).date().isoformat())
    return sorted(set(dates))


def comparison_is_valid(quote: dict, first: dict, last: dict) -> bool:
    """Reject comparisons across split events for unadjusted closes."""
    if quote.get("price_basis") == "split-adjusted":
        return True
    start = date.fromisoformat(observation_session_date(first))
    end = date.fromisoformat(observation_session_date(last))
    return not any(
        start < date.fromisoformat(split_date) <= end
        for split_date in quote.get("split_dates", [])
    )


def significant_moves(quote: dict) -> list[dict]:
    """Return the largest daily close-to-close moves, up to a fixed limit."""
    observations = sorted(quote["observations"], key=observation_session_date)
    moves = []
    for previous, current in zip(observations, observations[1:]):
        if not previous["price"]:
            continue
        pair_quote = {
            "price_basis": quote.get("price_basis", "unadjusted"),
            "split_dates": quote.get("split_dates", []),
        }
        if not comparison_is_valid(pair_quote, previous, current):
            continue
        change = current["price"] - previous["price"]
        percent_change = change / previous["price"] * 100
        moves.append(
            {
                "previous": previous,
                "current": current,
                "change": change,
                "percent_change": percent_change,
            }
        )
    moves.sort(key=lambda move: abs(move["percent_change"]), reverse=True)
    return moves[:TOP_PRICE_CHANGE_COUNT]


def observation_session_date(observation: dict) -> str:
    """Return the exchange-local session date, with legacy quote compatibility."""
    if "exchange_session_date" in observation:
        return observation["exchange_session_date"]
    return datetime.fromisoformat(observation["observed_at"]).date().isoformat()


def parse_news_date(value: object) -> datetime | None:
    """Parse Yahoo's epoch or ISO publication timestamp into UTC."""
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    return None


def normalize_news_item(item: dict) -> dict | None:
    """Normalize legacy and current Yahoo Finance news response shapes."""
    content = item.get("content")
    if not isinstance(content, dict):
        content = item
    title = content.get("title")
    if not isinstance(title, str) or not title.strip():
        return None

    url = content.get("link")
    if not isinstance(url, str):
        for key in ("canonicalUrl", "clickThroughUrl"):
            candidate = content.get(key)
            if isinstance(candidate, dict) and isinstance(candidate.get("url"), str):
                url = candidate["url"]
                break
    parsed_url = urlparse(url) if isinstance(url, str) else None
    if (
        parsed_url is None
        or parsed_url.scheme not in {"http", "https"}
        or not parsed_url.netloc
    ):
        return None

    published_at = parse_news_date(
        content.get("providerPublishTime") or content.get("pubDate")
    )
    if published_at is None:
        return None

    provider = content.get("provider")
    publisher = (
        provider.get("displayName")
        if isinstance(provider, dict)
        else content.get("publisher")
    )
    related_tickers = content.get("relatedTickers") or item.get("relatedTickers") or []
    if not isinstance(related_tickers, list):
        related_tickers = []
    symbols = set()
    for related in related_tickers:
        symbol = related.get("symbol") if isinstance(related, dict) else related
        if isinstance(symbol, str):
            symbols.add(symbol.upper())
    summary = (
        content.get("summary")
        or content.get("description")
        or content.get("synopsis")
    )
    return {
        "title": title.strip(),
        "url": url,
        "published_at": published_at.isoformat(),
        "publisher": publisher if isinstance(publisher, str) else "Yahoo Finance",
        "summary": summary if isinstance(summary, str) else None,
        "related_tickers": symbols,
    }


def fetch_related_news(
    ticker: str, move_date: date, company: str | None = None
) -> list[dict]:
    """Search Yahoo Finance news near a move, with a closest-result fallback."""
    search_url = "https://query1.finance.yahoo.com/v1/finance/search?"
    candidates = []
    seen_urls = set()
    errors = []
    yahoo_queries = (f"{ticker} {move_date.isoformat()}", ticker)
    for query in yahoo_queries:
        url = search_url + urlencode(
            {"q": query, "quotesCount": 0, "newsCount": 25}
        )
        try:
            response = fetch_json(url)
        except RuntimeError as error:
            errors.append(f"Yahoo Finance: {error}")
            continue
        news_items = response.get("news") or []
        if not isinstance(news_items, list):
            errors.append("Yahoo Finance returned an invalid news response.")
            continue
        for item in news_items:
            if not isinstance(item, dict):
                continue
            article = normalize_news_item(item)
            if article is None:
                continue
            if (
                article["related_tickers"]
                and ticker.upper() not in article["related_tickers"]
            ):
                continue
            if article["url"] in seen_urls:
                continue
            seen_urls.add(article["url"])
            candidates.append(article)

    if not candidates and company:
        try:
            response = fetch_json(
                search_url
                + urlencode({"q": company, "quotesCount": 0, "newsCount": 25})
            )
        except RuntimeError as error:
            errors.append(f"Yahoo Finance company-name search: {error}")
        else:
            news_items = response.get("news") or []
            if not isinstance(news_items, list):
                errors.append("Yahoo Finance returned an invalid company-name news response.")
            else:
                for item in news_items:
                    if not isinstance(item, dict):
                        continue
                    article = normalize_news_item(item)
                    if (
                        article is None
                        or (
                            article["related_tickers"]
                            and ticker.upper() not in article["related_tickers"]
                        )
                        or article["url"] in seen_urls
                    ):
                        continue
                    seen_urls.add(article["url"])
                    candidates.append(article)

    if not candidates and errors:
        raise RuntimeError(
            "News searches failed: " + "; ".join(dict.fromkeys(errors))
        )

    candidates.sort(
        key=lambda article: (
            ticker.upper() not in article["related_tickers"],
            abs(
                (
                    datetime.fromisoformat(article["published_at"]).date()
                    - move_date
                ).days
            ),
        )
    )

    def days_from_move(article: dict) -> int:
        return abs(
            (
                datetime.fromisoformat(article["published_at"]).date()
                - move_date
            ).days
        )

    in_window = [
        article
        for article in candidates
        if days_from_move(article) <= NEWS_DATE_WINDOW_DAYS
    ]
    if in_window:
        return in_window
    return sorted(
        candidates,
        key=lambda article: (
            days_from_move(article),
            ticker.upper() not in article["related_tickers"],
        ),
    )[:1]


def store_snapshot(quote: dict, database_path: Path) -> int:
    """Append a quote to the database and return its snapshot ID."""
    captured_at = datetime.now(timezone.utc).isoformat()

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
        session_date = observation_session_date(quote)
        existing = connection.execute(
            """
            SELECT snapshot_id FROM snapshots
            WHERE company_id = ? AND exchange_session_date = ?
              AND currency = ? AND price_basis = 'unadjusted'
            """,
            (company_id, session_date, quote["currency"]),
        ).fetchone()
        if existing:
            return existing[0]

        cursor = connection.execute(
            """
            INSERT INTO snapshots (
                company_id, captured_at, observed_at,
                exchange_session_date, price, currency, price_basis
            ) VALUES (?, ?, ?, ?, ?, ?, 'unadjusted')
            """,
            (
                company_id,
                captured_at,
                quote["observed_at"],
                session_date,
                quote["price"],
                quote["currency"],
            ),
        )
        return cursor.lastrowid


def store_news_item(
    article: dict, snapshot_id: int, database_path: Path
) -> None:
    """Append a Yahoo Finance news item linked to a snapshot."""
    with closing(sqlite3.connect(database_path)) as connection, connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            """
            INSERT INTO news_items (
                snapshot_id, published_at, title, url, summary
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                snapshot_id,
                article["published_at"],
                article["title"],
                article["url"],
                article["summary"],
            ),
        )


def add_watched_symbol(quote: dict, confirmed_ticker: str, database_path: Path) -> dict:
    """Add an explicitly confirmed Yahoo Finance symbol to the watch registry."""
    resolved_ticker = str(quote["ticker"]).upper()
    confirmed_ticker = confirmed_ticker.strip().upper()
    if confirmed_ticker != resolved_ticker:
        raise ValueError(
            f"Confirmation mismatch: Yahoo Finance resolved {quote['brand']!r} "
            f"to {resolved_ticker}, not {confirmed_ticker}."
        )

    captured_at = datetime.now(timezone.utc).isoformat()
    with closing(sqlite3.connect(database_path)) as connection, connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            """
            INSERT INTO companies (name, ticker, created_at)
            VALUES (?, ?, ?)
            ON CONFLICT(ticker) DO UPDATE SET name = excluded.name
            """,
            (quote["company"], resolved_ticker, captured_at),
        )
        company_id = connection.execute(
            "SELECT company_id FROM companies WHERE ticker = ?", (resolved_ticker,)
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO watched_symbols (company_id, ticker, added_at, active) "
            "VALUES (?, ?, ?, 1) "
            "ON CONFLICT(ticker) DO UPDATE SET "
            "company_id = excluded.company_id, active = 1",
            (company_id, resolved_ticker, captured_at),
        )
        row = connection.execute(
            """
            SELECT watch_id, ticker, added_at, active
            FROM watched_symbols WHERE ticker = ?
            """,
            (resolved_ticker,),
        ).fetchone()
    return {
        "watch_id": row[0],
        "ticker": row[1],
        "added_at": row[2],
        "active": bool(row[3]),
        "company": quote["company"],
    }


def escape_markdown_text(value: str) -> str:
    """Escape untrusted text before including it in a Markdown report."""
    for character in ("\\", "`", "*", "_", "{", "}", "[", "]", "<", ">", "|"):
        value = value.replace(character, f"\\{character}")
    return value.replace("\n", " ").replace("\r", " ")


def format_quote(quote: dict) -> str:
    """Format the period summary, skipping comparisons across splits."""
    precision = quote["price_hint"]
    if not isinstance(precision, int) or not 0 <= precision <= 8:
        precision = 2
    observations = sorted(quote["observations"], key=observation_session_date)
    first = observations[0]
    last = observations[-1]
    basis = quote.get("price_basis", "unadjusted")
    start_text = observation_session_date(first)
    end_text = observation_session_date(last)
    if not comparison_is_valid(quote, first, last):
        split_text = ", ".join(quote.get("split_dates", [])) or "a stock split"
        return (
            f"{quote['company']} ({quote['ticker']}): price comparison skipped "
            f"because the unadjusted series spans {split_text}; "
            f"period {start_text} to {end_text}; {len(observations)} daily closes "
            f"(price basis: {basis}; source: Yahoo Finance)"
        )
    net_change = last["price"] - first["price"]
    percent_change = net_change / first["price"] * 100 if first["price"] else 0
    return (
        f"{quote['company']} ({quote['ticker']}): net change "
        f"{net_change:+,.{precision}f} {quote['currency']} ({percent_change:+.2f}%) "
        f"from {start_text} to {end_text}; {len(observations)} daily closing prices "
        f"(price basis: {basis}; source: Yahoo Finance)"
    )


def format_move_news(quote, moves, news_by_move, news_errors):
    """Format significant moves and related news for CLI output."""
    lines = []
    for move in moves:
        move_date = date.fromisoformat(observation_session_date(move["current"]))
        lines.append(
            f"Significant move on {move_date.isoformat()}: "
            f"{move['percent_change']:+.2f}% "
            f"({move['change']:+,.2f} {quote['currency']})."
        )
        if move_date.isoformat() in news_errors:
            lines.append(
                f"  Related news lookup failed: "
                f"{news_errors[move_date.isoformat()]}"
            )
        else:
            articles = news_by_move.get(move_date.isoformat(), [])
            if not articles:
                lines.append("  No related news article was returned by Yahoo Finance.")
            for article in articles:
                published_date = datetime.fromisoformat(article["published_at"]).date()
                lines.append(
                    f"  News: {article['title']} "
                    f"({article['publisher']}, {published_date.isoformat()})"
                )
                if article.get("summary"):
                    lines.append(f"  Summary: {article['summary']}")
                offset = (published_date - move_date).days
                timing = (
                    "on the move date" if offset == 0
                    else f"{abs(offset)} day(s) {'after' if offset > 0 else 'before'} the move"
                )
                association = (
                    f"Yahoo tags it to {quote['ticker']}"
                    if quote["ticker"].upper() in article["related_tickers"]
                    else f"Yahoo Finance returned it for a {quote['ticker']} news search"
                )
                if abs(offset) > NEWS_DATE_WINDOW_DAYS:
                    association = f"closest available result; {association}"
                lines.append(
                    f"  Why related: {association}; it was published {timing}. "
                    "This is potential context, not proof of causation."
                )
    return lines


def write_report(quote, report_dir, requested_start, requested_end,
                moves=None, news_by_move=None, news_errors=None):
    """Write a split-aware Markdown stock performance report."""
    precision = quote["price_hint"]
    if not isinstance(precision, int) or not 0 <= precision <= 8:
        precision = 2
    observations = sorted(quote["observations"], key=observation_session_date)
    first = observations[0]
    last = observations[-1]
    basis = quote.get("price_basis", "unadjusted")
    comparable = comparison_is_valid(quote, first, last)
    net_change = last["price"] - first["price"] if comparable else None
    percent_change = net_change / first["price"] * 100 if comparable and net_change is not None and first["price"] else None
    split_dates = ", ".join(quote.get("split_dates", []))

    lines = [
        f"# {quote['company']} ({quote['ticker']}) stock performance",
        "",
        f"- Requested period: {requested_start.isoformat()} to {requested_end.isoformat()}",
        f"- Available data: {observation_session_date(first)} to {observation_session_date(last)}",
        f"- Price basis: {basis}",
        "- Price basis note: unadjusted means Yahoo Finance raw closes; split-adjusted means historical closes are aligned for splits.",
        "- Source: Yahoo Finance historical daily closing prices",
        "",
        "## Summary Metrics",
        "",
        f"- Starting close ({observation_session_date(first)}): {first['price']:,.{precision}f} {quote['currency']}",
        f"- Ending close ({observation_session_date(last)}): {last['price']:,.{precision}f} {quote['currency']}",
    ]
    if comparable:
        lines.extend([
            f"- Net change: {net_change:+,.{precision}f} {quote['currency']}",
            f"- Percentage change: {percent_change:+.2f}%",
        ])
    else:
        lines.extend([
            "- Net change: N/A - comparison skipped across a stock split",
            "- Percentage change: N/A - not comparable across a stock split",
            f"- Split date(s): {split_dates}",
        ])

    moves = moves if moves is not None else significant_moves(quote)
    news_by_move = news_by_move or {}
    news_errors = news_errors or {}
    lines.extend(["", f"## Top {len(moves)} Daily Price Changes", ""])
    for move in moves:
        move_date = observation_session_date(move["current"])
        lines.extend([
            f"### {move_date}: {move['percent_change']:+.2f}%",
            "",
            f"- Close-to-close change: {move['change']:+,.{precision}f} {quote['currency']}",
        ])
        if move_date in news_errors:
            lines.append(f"- Related news lookup failed: {news_errors[move_date]}")
        for article in news_by_move.get(move_date, []):
            published_date = datetime.fromisoformat(article["published_at"]).strftime("%Y-%m-%d")
            lines.append(f"- [{article['title']}]({article['url']}) - {article['publisher']}, {published_date}")
            if article.get("summary"):
                lines.append(f"  - {article['summary']}")
            move_day = date.fromisoformat(move_date)
            article_day = datetime.fromisoformat(article["published_at"]).date()
            day_offset = (article_day - move_day).days
            timing = (
                "on the move date" if day_offset == 0
                else f"{abs(day_offset)} day(s) {'after' if day_offset > 0 else 'before'} the move"
            )
            association = (
                f"Yahoo tags this article to {quote['ticker']}"
                if quote["ticker"].upper() in article["related_tickers"]
                else f"Yahoo Finance returned this article for a {quote['ticker']} news search"
            )
            if abs(day_offset) > NEWS_DATE_WINDOW_DAYS:
                association = f"closest available result; {association}"
            lines.append(
                f"  - Why it may be related: {association}; it was published {timing}. "
                "The timing/topic offer potential context, not proof that the article caused the price move."
            )
        lines.append("")

    lines.extend(["", "## Daily Closing Prices", "", f"| Date | Close ({quote['currency']}) |", "| --- | ---: |"])
    for observation in observations:
        lines.append(
            f"| {observation_session_date(observation)} | "
            f"{observation['price']:,.{precision}f} |"
        )

    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"{quote['ticker']}_{datetime.now(timezone.utc).strftime('%Y%m%d')}.md"
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report_path


def resolve_equity(brand: str) -> dict:
    """Resolve a brand or ticker to one confirmed Yahoo Finance equity."""
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
    ticker = str(match["symbol"]).strip().upper()
    company = (
        match.get("longname")
        or match.get("shortname")
        or match.get("longName")
        or match.get("shortName")
        or brand
    )
    return {"brand": brand, "ticker": ticker, "company": company}


def check_watched_symbol(resolved: dict, threshold_percent: float, database_path: Path) -> dict:
    """Check a watched symbol and establish or update its session baseline."""
    ticker = str(resolved["ticker"]).upper()
    with closing(sqlite3.connect(database_path)) as connection:
        row = connection.execute(
            """
            SELECT baseline_price, baseline_session_date
            FROM watched_symbols
            WHERE ticker = ? AND active = 1
            """,
            (ticker,),
        ).fetchone()
    if row is None:
        raise ValueError(f"{ticker} is not in the active watchlist.")

    quote = fetch_stock_history(
        ticker,
        date.today() - timedelta(days=31),
        date.today(),
    )
    observations = sorted(quote["observations"], key=observation_session_date)
    latest = observations[-1]
    new_price = latest["price"]
    new_session_date = observation_session_date(latest)
    baseline_price, baseline_session_date = row

    if baseline_price is None or baseline_session_date is None:
        with closing(sqlite3.connect(database_path)) as connection, connection:
            connection.execute(
                """
                UPDATE watched_symbols
                SET baseline_price = ?, baseline_session_date = ?
                WHERE ticker = ?
                """,
                (new_price, new_session_date, ticker),
            )
        return {
            "baseline_established": True,
            "flag": False,
            "new_price": new_price,
            "new_session_date": new_session_date,
            "net_change": 0.0,
            "percent_change": 0.0,
            "threshold_percent": threshold_percent,
        }

    if baseline_session_date is not None and new_session_date == baseline_session_date:
        return {
            "baseline_established": False,
            "same_session": True,
            "flag": False,
            "new_price": new_price,
            "new_session_date": new_session_date,
            "net_change": None,
            "percent_change": None,
            "threshold_percent": threshold_percent,
            "reason": "no new trading session",
        }

    pair = {
        "price_basis": "unadjusted",
        "split_dates": quote.get("split_dates", []),
    }
    baseline = {
        "price": baseline_price,
        "exchange_session_date": baseline_session_date,
    }
    if not comparison_is_valid(pair, baseline, latest):
        return {
            "baseline_established": False,
            "flag": False,
            "new_price": new_price,
            "new_session_date": new_session_date,
            "net_change": None,
            "percent_change": None,
            "threshold_percent": threshold_percent,
            "reason": "comparison skipped across a stock split",
        }

    net_change = new_price - baseline_price
    percent_change = net_change / baseline_price * 100 if baseline_price else 0.0
    triggered = percent_change > threshold_percent or percent_change < -threshold_percent

    if new_session_date != baseline_session_date:
        with closing(sqlite3.connect(database_path)) as connection, connection:
            connection.execute(
                """
                UPDATE watched_symbols
                SET baseline_price = ?, baseline_session_date = ?
                WHERE ticker = ?
                """,
                (new_price, new_session_date, ticker),
            )

    return {
        "baseline_established": False,
        "flag": triggered,
        "new_price": new_price,
        "new_session_date": new_session_date,
        "net_change": net_change,
        "percent_change": percent_change,
        "threshold_percent": threshold_percent,
    }


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

    if args.command in {"price", "summarize"}:
        try:
            database_path = initialize_project()
            start_date, end_date = get_date_range(args.months, args.fromdate)
            quote = fetch_stock_history(args.brand, start_date, end_date)
            snapshot_ids = {}
            for observation in quote["observations"]:
                snapshot_ids[observation["observed_at"]] = store_snapshot(
                    {**quote, **observation}, database_path
                )
            moves = significant_moves(quote)
            news_by_move = {}
            news_errors = {}
            for move in moves:
                move_date = date.fromisoformat(
                    observation_session_date(move["current"])
                )
                move_date_text = move_date.isoformat()
                try:
                    articles = fetch_related_news(
                        quote["ticker"], move_date, quote["company"]
                    )
                except RuntimeError as error:
                    news_errors[move_date_text] = str(error)
                    print(
                        f"Warning: related news lookup failed for "
                        f"{move_date_text}: {error}",
                        file=sys.stderr,
                    )
                    continue
                news_by_move[move_date_text] = articles
                snapshot_id = snapshot_ids[move["current"]["observed_at"]]
                for article in articles:
                    store_news_item(article, snapshot_id, database_path)
            report_path = write_report(
                quote,
                PROJECT_ROOT / REPORTS_DIR_NAME,
                start_date,
                end_date,
                moves,
                news_by_move,
                news_errors,
            )
            print(f"{format_quote(quote)}")
            for line in format_move_news(
                quote, moves, news_by_move, news_errors
            ):
                print(line)
            print(f"Report: {report_path.relative_to(PROJECT_ROOT)}")
        except (RuntimeError, ValueError, OSError, sqlite3.Error) as error:
            print(f"Error: {error}", file=sys.stderr)
            return 1
        return 0

    if args.command == "watch":
        try:
            database_path = initialize_project()
            resolved = resolve_equity(args.brand)
            watched = add_watched_symbol(
                resolved, args.confirmed_ticker, database_path
            )
            print(
                f"Watching {watched['company']} ({watched['ticker']}); "
                f"Yahoo Finance confirmed the symbol."
            )
        except (RuntimeError, ValueError, OSError, sqlite3.Error) as error:
            print(f"Error: {error}", file=sys.stderr)
            return 1
        return 0

    if args.command == "watch-check":
        try:
            database_path = initialize_project()
            resolved = resolve_equity(args.brand)
            result = check_watched_symbol(
                resolved, args.threshold_percent, database_path
            )
            if result["baseline_established"]:
                print(
                    f"Baseline established for {resolved['ticker']} at "
                    f"{result['new_price']:.2f} on {result['new_session_date']}; "
                    "no alert on the first check."
                )
            elif result["percent_change"] is None:
                print(
                    f"Comparison skipped for {resolved['ticker']}: "
                    f"{result['reason']}."
                )
            elif result.get("same_session"):
                print(
                    f"No changes for {resolved['ticker']}: "
                    f"session {result['new_session_date']} matches the baseline session."
                )
            else:
                sign = "+" if result["net_change"] >= 0 else "-"
                print(
                    f"{resolved['ticker']} net change: {sign}{abs(result['net_change']):.2f}; "
                    f"percent change: {sign}{abs(result['percent_change']):.2f}%; "
                    f"threshold: {result['threshold_percent']:.2f}%; "
                    f"session: {result['new_session_date']}; "
                    f"flag: {'yes' if result['flag'] else 'no'}"
                )
            return 0
        except (RuntimeError, ValueError, OSError, sqlite3.Error) as error:
            print(f"Error: {error}", file=sys.stderr)
            return 1

    return 2


# Run the CLI only when this file is executed directly, not when imported.
if __name__ == "__main__":
    raise SystemExit(main())
