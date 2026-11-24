# Yahoo Finance Price Data

## Price series

Yahoo Finance is the sole source for the stock watcher's price series. The
watcher uses the provider's daily `close` values for the requested historical
range. It does not merge prices from another provider, request a separate live
quote, use the `adjclose` field, or fetch after-hours prints. Values shown as
current in a user request must therefore be described as the latest available
daily close, not as a real-time price.

## Retrieval

The CLI uses Python's standard-library `urllib` to make HTTPS requests and
parse JSON; it does not render web pages with Playwright or another browser.
The price workflow makes two requests to Yahoo Finance:

1. Ticker resolution: `https://query1.finance.yahoo.com/v1/finance/search`,
   with the requested company or ticker as `q`. The script selects the first
   returned result with `quoteType` equal to `EQUITY` and a `symbol`.
2. Daily history:
   `https://query1.finance.yahoo.com/v8/finance/chart/{symbol}`, with
   `period1`, `period2`, and `interval=1d`. The period values are Unix
   timestamps delimiting the requested date range.

From the chart JSON, the script reads `meta.longName` or `meta.shortName` for
the company name, `meta.currency` and `meta.priceHint` for display, the
`timestamp` array for observation times, and
`indicators.quote[0].close` for daily prices. It pairs each timestamp with its
close, stores the source timestamp as UTC, and derives the exchange session date
in `meta.exchangeTimezoneName`. It skips entries whose close is not numeric.
The current session's daily bar is omitted until the regular-session end time
from `meta.currentTradingPeriod` has passed; this prevents an in-progress bar
from being presented as a completed close.

SQLite stores `exchange_session_date` as the exchange-local date when that
session closed and `captured_at` as the UTC retrieval timestamp. A Saturday or
Sunday retrieval therefore records Friday's session date alongside the actual
weekend capture timestamp. The legacy `observed_at` field continues to retain
the provider timestamp. It does not read or store an intraday quote or an
after-hours field.

Ticker search results are not disambiguated by exchange: if multiple equity
matches are possible, the first result is used. Confirm the ticker when the
company name could refer to more than one listed company.

## Exchange coverage and limitations

The documented primary coverage is U.S.-listed equities on the NYSE and Nasdaq,
when Yahoo Finance returns a matching equity symbol and daily chart data. The
script does not filter or validate the listing exchange, however, so actual
availability is determined by Yahoo Finance's search and chart responses;
this is not an exchange-level coverage guarantee. The code does not establish
or test reliable coverage for Canadian exchanges, other international markets,
OTC securities, or illiquid tickers. Those symbols may require a Yahoo Finance
exchange suffix and may have missing history, different currencies, or
different data timing.

The script reports the currency supplied in chart metadata and the dates
present in the response; it does not verify exchange calendars, correct missing
observations, or validate data latency. Yahoo Finance data can be delayed.
Treat results as historical data, confirm the resolved ticker and observation
dates, and do not rely on them as a real-time quote.
