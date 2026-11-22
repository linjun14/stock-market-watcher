#!/usr/bin/env python3

# Standard-library modules for the CLI, SQLite storage, and filesystem paths.
import argparse
import calendar
from contextlib import closing
from datetime import date, datetime, time, timedelta, timezone
from email.utils import parsedate_to_datetime
import html
import json
import re
import sqlite3
import sys
from pathlib import Path
from statistics import fmean, pstdev
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
from xml.etree import ElementTree


# Keep runtime files beside the repository, regardless of the caller's cwd.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATE_DIR_NAME = ".state"
REPORTS_DIR_NAME = "reports"
DATABASE_NAME = "stock_market_watcher.db"
SIGNIFICANT_CHANGE_PERCENT = 3.0
NEWS_DATE_WINDOW_DAYS = 30


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


def parse_google_news_rss(payload: bytes) -> list[dict]:
    """Parse Google News RSS entries into the shared article shape."""
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError as error:
        raise RuntimeError("Google News returned an invalid RSS feed.") from error

    articles = []
    for item in root.findall(".//item"):
        title = item.findtext("title")
        url = item.findtext("link")
        published_text = item.findtext("pubDate")
        if not title or not url or not published_text:
            continue
        try:
            published_at = parsedate_to_datetime(published_text)
        except (TypeError, ValueError, OverflowError):
            continue
        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=timezone.utc)
        parsed_url = urlparse(url)
        if (
            parsed_url.scheme not in {"http", "https"}
            or not parsed_url.netloc
        ):
            continue

        source_element = item.find("source")
        publisher = (
            source_element.text.strip()
            if source_element is not None and source_element.text
            else "Google News"
        )
        description = item.findtext("description")
        summary = (
            re.sub(
                r"\s+([.,;:!?])",
                r"\1",
                " ".join(
                    html.unescape(re.sub(r"<[^>]*>", " ", description)).split()
                ),
            )
            if description
            else None
        )
        articles.append(
            {
                "title": html.unescape(title).strip(),
                "url": url,
                "published_at": published_at.astimezone(timezone.utc).isoformat(),
                "publisher": publisher,
                "summary": summary or None,
                "related_tickers": set(),
                "search_provider": "Google News",
            }
        )
    return articles


def fetch_google_news(query: str) -> list[dict]:
    """Search Google News using its public RSS feed."""
    url = "https://news.google.com/rss/search?" + urlencode(
        {"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"}
    )
    request = Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; StockMarketWatcher/1.0)"},
    )
    try:
        with urlopen(request, timeout=15) as response:
            return parse_google_news_rss(response.read())
    except HTTPError as error:
        raise RuntimeError(f"Google News request failed (HTTP {error.code}).") from error
    except URLError as error:
        raise RuntimeError(
            f"Could not reach Google News: {error.reason}"
        ) from error


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


def significant_moves(quote: dict) -> list[dict]:
    """Return daily close-to-close moves meeting the significance threshold."""
    observations = sorted(quote["observations"], key=lambda item: item["observed_at"])
    moves = []
    for previous, current in zip(observations, observations[1:]):
        if not previous["price"]:
            continue
        change = current["price"] - previous["price"]
        percent_change = change / previous["price"] * 100
        if abs(percent_change) >= SIGNIFICANT_CHANGE_PERCENT:
            moves.append(
                {
                    "previous": previous,
                    "current": current,
                    "change": change,
                    "percent_change": percent_change,
                }
            )
    return moves


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
        "search_provider": "Yahoo Finance",
    }


def fetch_related_news(
    ticker: str, move_date: date, company: str | None = None
) -> list[dict]:
    """Search multiple news sources near a move, with a closest-result fallback."""
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

    subject = f'"{company}" {ticker}' if company else ticker
    google_queries = (
        f"{subject} {move_date.isoformat()}",
        f"{subject} stock",
    )
    for query in google_queries:
        try:
            articles = fetch_google_news(query)
        except RuntimeError as error:
            errors.append(f"Google News: {error}")
            continue
        for article in articles:
            if article["url"] in seen_urls:
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


def store_news_item(
    article: dict, snapshot_id: int, database_path: Path
) -> None:
    """Append a related article and its source to the local database."""
    search_provider = article.get("search_provider", "Yahoo Finance")
    if search_provider == "Google News":
        source_name = "Google News RSS Search"
        source_url = "https://news.google.com/rss/search"
    else:
        source_name = "Yahoo Finance News Search"
        source_url = "https://query1.finance.yahoo.com/v1/finance/search"
    with closing(sqlite3.connect(database_path)) as connection, connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            "INSERT INTO sources (name, url) VALUES (?, ?) "
            "ON CONFLICT(url) DO UPDATE SET name = excluded.name",
            (source_name, source_url),
        )
        source_id = connection.execute(
            "SELECT source_id FROM sources WHERE url = ?",
            (source_url,),
        ).fetchone()[0]
        connection.execute(
            """
            INSERT INTO news_items (
                snapshot_id, source_id, published_at, title, url, summary
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot_id,
                source_id,
                article["published_at"],
                article["title"],
                article["url"],
                article["summary"],
            ),
        )


def escape_markdown_text(value: str) -> str:
    """Escape untrusted text before including it in a Markdown report."""
    for character in ("\\", "`", "*", "_", "{", "}", "[", "]", "<", ">", "|"):
        value = value.replace(character, f"\\{character}")
    return value.replace("\n", " ").replace("\r", " ")


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


def format_move_news(
    quote: dict,
    moves: list[dict],
    news_by_move: dict[str, list[dict]],
    news_errors: dict[str, str],
) -> list[str]:
    """Format significant moves and related-news context for the CLI conversation."""
    lines = []
    for move in moves:
        move_date = datetime.fromisoformat(
            move["current"]["observed_at"]
        ).date()
        move_date_text = move_date.isoformat()
        lines.append(
            f"Significant move on {move_date_text}: "
            f"{move['percent_change']:+.2f}% "
            f"({move['change']:+,.2f} {quote['currency']})."
        )
        if move_date_text in news_errors:
            lines.append(f"  Related news lookup failed: {news_errors[move_date_text]}")
            continue
        articles = news_by_move.get(move_date_text, [])
        if not articles:
            lines.append(
                "  No related news article was returned by Yahoo Finance or Google News."
            )
            continue
        for article in articles:
            published_date = datetime.fromisoformat(
                article["published_at"]
            ).date()
            offset = (published_date - move_date).days
            timing = (
                "on the move date"
                if offset == 0
                else f"{abs(offset)} day(s) "
                f"{'after' if offset > 0 else 'before'} the move"
            )
            association = (
                f"Yahoo tags it to {quote['ticker']}"
                if quote["ticker"].upper() in article["related_tickers"]
                else (
                    f"{article.get('search_provider', 'Yahoo Finance')} "
                    f"returned it for a {quote['ticker']} news search"
                )
            )
            if abs(offset) > NEWS_DATE_WINDOW_DAYS:
                association = f"closest available result; {association}"
            lines.append(
                f"  News: {article['title']} "
                f"({article['publisher']}, {published_date.isoformat()})"
            )
            if article.get("summary"):
                lines.append(f"  Summary: {article['summary']}")
            lines.append(
                f"  Why related: {association}; it was published {timing}. "
                "This is potential context, not proof of causation."
            )
    return lines


def write_report(
    quote: dict,
    report_dir: Path,
    requested_start: date,
    requested_end: date,
    moves: list[dict] | None = None,
    news_by_move: dict[str, list[dict]] | None = None,
    news_errors: dict[str, str] | None = None,
) -> Path:
    """Write a Markdown summary of price performance and related news."""
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
        f"## Significant Daily Moves (absolute return >= {SIGNIFICANT_CHANGE_PERCENT:.0f}%)",
        "",
    ]
    moves = moves if moves is not None else significant_moves(quote)
    news_by_move = news_by_move or {}
    news_errors = news_errors or {}
    if not moves:
        lines.append("No daily close-to-close moves met the significance threshold.")
    for move in moves:
        move_date = observed_date(move["current"])
        lines.extend(
            [
                f"### {move_date}: {move['percent_change']:+.2f}%",
                "",
                f"- Close-to-close change: {move['change']:+,.{precision}f} "
                f"{quote['currency']} "
                f"({price_text(move['previous']['price'])} to "
                f"{price_text(move['current']['price'])})",
            ]
        )
        if move_date in news_errors:
            lines.extend(
                [
                    f"- Related news lookup failed: "
                    f"{escape_markdown_text(news_errors[move_date])}",
                ]
            )
        else:
            articles = news_by_move.get(move_date, [])
            if not articles:
                lines.append(
                    "- Yahoo Finance and Google News returned no related news "
                    "article for this move."
                )
            for article in articles:
                published_date = datetime.fromisoformat(
                    article["published_at"]
                ).strftime("%Y-%m-%d")
                safe_title = escape_markdown_text(article["title"])
                safe_publisher = escape_markdown_text(article["publisher"])
                safe_url = (
                    article["url"]
                    .replace(" ", "%20")
                    .replace("(", "%28")
                    .replace(")", "%29")
                )
                lines.append(
                    f"- [{safe_title}]({safe_url}) - {safe_publisher}, "
                    f"{published_date}"
                )
                if article.get("summary"):
                    lines.append(
                        f"  - {escape_markdown_text(article['summary'])}"
                    )
                move_day = datetime.fromisoformat(
                    move["current"]["observed_at"]
                ).date()
                article_day = datetime.fromisoformat(
                    article["published_at"]
                ).date()
                day_offset = (article_day - move_day).days
                timing = (
                    "on the move date"
                    if day_offset == 0
                    else f"{abs(day_offset)} day(s) "
                    f"{'after' if day_offset > 0 else 'before'} the move"
                )
                association = (
                    f"Yahoo tags this article to {quote['ticker']}"
                    if quote["ticker"].upper() in article["related_tickers"]
                    else (
                        f"{article.get('search_provider', 'Yahoo Finance')} "
                        f"returned this article for a {quote['ticker']} news search"
                    )
                )
                if abs(day_offset) > NEWS_DATE_WINDOW_DAYS:
                    association = f"closest available result; {association}"
                lines.append(
                    f"  - Why it may be related: {association}; it was published "
                    f"{timing}. The timing/topic offer potential context, not proof "
                    "that the article caused the price move."
                )
        lines.append("")

    lines.extend(
        [
            "",
            "## Daily Closing Prices",
            "",
            f"| Date | Close ({quote['currency']}) | Change ({quote['currency']}) | Daily return |",
            "| --- | ---: | ---: | ---: |",
        ]
    )
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
            snapshot_ids = {}
            for observation in quote["observations"]:
                snapshot_ids[observation["observed_at"]] = store_snapshot(
                    {**quote, **observation}, database_path
                )
            moves = significant_moves(quote)
            news_by_move = {}
            news_errors = {}
            for move in moves:
                move_date = datetime.fromisoformat(
                    move["current"]["observed_at"]
                ).date()
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

    return 2


# Run the CLI only when this file is executed directly, not when imported.
if __name__ == "__main__":
    raise SystemExit(main())
