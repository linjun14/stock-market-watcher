import sqlite3
import sys
import unittest
from datetime import date, datetime, timezone
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))
import stock_watcher


def news_item(title, url, published_at, related_tickers=None, summary=None):
    return {
        "title": title,
        "link": url,
        "providerPublishTime": int(
            published_at.replace(tzinfo=timezone.utc).timestamp()
        ),
        "relatedTickers": related_tickers or [],
        "summary": summary,
    }


class PriceSnapshotDateTests(unittest.TestCase):
    def test_weekend_history_uses_friday_session_date_and_saturday_capture_time(self):
        fixed_now = datetime(2026, 11, 21, 12, tzinfo=timezone.utc)

        class FixedDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed_now if tz is None else fixed_now.astimezone(tz)

        friday_timestamp = int(
            datetime(2026, 11, 20, 14, 30, tzinfo=timezone.utc).timestamp()
        )
        monday_start = int(
            datetime(2026, 11, 23, 14, 30, tzinfo=timezone.utc).timestamp()
        )
        monday_end = int(
            datetime(2026, 11, 23, 21, tzinfo=timezone.utc).timestamp()
        )
        responses = [
            {"quotes": [{"quoteType": "EQUITY", "symbol": "ACME"}]},
            {
                "chart": {
                    "result": [
                        {
                            "meta": {
                                "exchangeTimezoneName": "America/New_York",
                                "currentTradingPeriod": {
                                    "regular": {
                                        "start": monday_start,
                                        "end": monday_end,
                                    }
                                },
                            },
                            "timestamp": [friday_timestamp],
                            "indicators": {"quote": [{"close": [123.45]}]},
                        }
                    ]
                }
            },
        ]
        with (
            patch.object(stock_watcher, "datetime", FixedDateTime),
            patch.object(stock_watcher, "fetch_json", side_effect=responses),
            TemporaryDirectory() as directory,
        ):
            quote = stock_watcher.fetch_stock_history(
                "ACME", date(2026, 11, 1), date(2026, 11, 21)
            )
            database_path = stock_watcher.initialize_project(Path(directory))
            stock_watcher.store_snapshot(
                {**quote, **quote["observations"][0]}, database_path
            )
            with closing(sqlite3.connect(database_path)) as connection:
                captured_at, session_date = connection.execute(
                    "SELECT captured_at, exchange_session_date FROM snapshots"
                ).fetchone()

            self.assertEqual(session_date, "2026-11-20")
            self.assertTrue(captured_at.startswith("2026-11-21T"))

    def test_excludes_the_current_regular_session_before_its_close(self):
        fixed_now = datetime(2026, 11, 23, 15, tzinfo=timezone.utc)

        class FixedDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed_now if tz is None else fixed_now.astimezone(tz)

        timestamps = [
            int(datetime(2026, 11, 20, 14, 30, tzinfo=timezone.utc).timestamp()),
            int(datetime(2026, 11, 23, 14, 30, tzinfo=timezone.utc).timestamp()),
        ]
        responses = [
            {"quotes": [{"quoteType": "EQUITY", "symbol": "ACME"}]},
            {
                "chart": {
                    "result": [
                        {
                            "meta": {
                                "exchangeTimezoneName": "America/New_York",
                                "currentTradingPeriod": {
                                    "regular": {
                                        "start": timestamps[1],
                                        "end": int(
                                            datetime(
                                                2026, 11, 23, 21,
                                                tzinfo=timezone.utc,
                                            ).timestamp()
                                        ),
                                    }
                                },
                            },
                            "timestamp": timestamps,
                            "indicators": {"quote": [{"close": [120.0, 125.0]}]},
                        }
                    ]
                }
            },
        ]
        with (
            patch.object(stock_watcher, "datetime", FixedDateTime),
            patch.object(stock_watcher, "fetch_json", side_effect=responses),
        ):
            quote = stock_watcher.fetch_stock_history(
                "ACME", date(2026, 11, 1), date(2026, 11, 23)
            )

        self.assertEqual(
            [item["exchange_session_date"] for item in quote["observations"]],
            ["2026-11-20"],
        )

    def test_includes_the_current_session_after_its_regular_close(self):
        fixed_now = datetime(2026, 11, 23, 22, tzinfo=timezone.utc)

        class FixedDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed_now if tz is None else fixed_now.astimezone(tz)

        timestamp = int(
            datetime(2026, 11, 23, 14, 30, tzinfo=timezone.utc).timestamp()
        )
        responses = [
            {"quotes": [{"quoteType": "EQUITY", "symbol": "ACME"}]},
            {
                "chart": {
                    "result": [
                        {
                            "meta": {
                                "exchangeTimezoneName": "America/New_York",
                                "currentTradingPeriod": {
                                    "regular": {
                                        "start": timestamp,
                                        "end": int(
                                            datetime(
                                                2026, 11, 23, 21,
                                                tzinfo=timezone.utc,
                                            ).timestamp()
                                        ),
                                    }
                                },
                            },
                            "timestamp": [timestamp],
                            "indicators": {"quote": [{"close": [125.0]}]},
                        }
                    ]
                }
            },
        ]
        with (
            patch.object(stock_watcher, "datetime", FixedDateTime),
            patch.object(stock_watcher, "fetch_json", side_effect=responses),
        ):
            quote = stock_watcher.fetch_stock_history(
                "ACME", date(2026, 11, 1), date(2026, 11, 23)
            )

        self.assertEqual(
            [item["exchange_session_date"] for item in quote["observations"]],
            ["2026-11-23"],
        )

    def test_existing_database_gets_exchange_session_date_from_legacy_observation(self):
        with TemporaryDirectory() as directory:
            project_root = Path(directory)
            state_directory = project_root / ".state"
            state_directory.mkdir()
            database_path = state_directory / stock_watcher.DATABASE_NAME
            with closing(sqlite3.connect(database_path)) as connection, connection:
                connection.execute(
                    """
                    CREATE TABLE snapshots (
                        snapshot_id INTEGER PRIMARY KEY,
                        company_id INTEGER NOT NULL,
                        source_id INTEGER NOT NULL,
                        captured_at TEXT NOT NULL,
                        observed_at TEXT NOT NULL,
                        price REAL NOT NULL,
                        currency TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO snapshots VALUES (
                        1, 1, 1, '2026-11-21T12:00:00+00:00',
                        '2026-11-20T14:30:00+00:00', 123.45, 'USD'
                    )
                    """
                )

            stock_watcher.initialize_project(project_root)

            with closing(sqlite3.connect(database_path)) as connection:
                session_date = connection.execute(
                    "SELECT exchange_session_date FROM snapshots"
                ).fetchone()[0]

        self.assertEqual(session_date, "2026-11-20")


class RelatedNewsTests(unittest.TestCase):
    def setUp(self):
        self.move_date = date(2026, 11, 18)
        self.tagged_article = news_item(
            "Company reports strong quarterly results",
            "https://example.com/earnings",
            datetime(2026, 11, 18),
            ["ACME"],
            "Revenue and earnings exceeded expectations.",
        )
        self.untagged_article = news_item(
            "Analyst discusses company outlook",
            "https://example.com/outlook",
            datetime(2026, 11, 17),
        )

    def test_significant_moves_returns_top_three_without_percentage_threshold(self):
        quote = {
            "observations": [
                {"price": 100.0, "observed_at": "2026-11-17T00:00:00+00:00"},
                {"price": 101.0, "observed_at": "2026-11-18T00:00:00+00:00"},
                {"price": 103.0, "observed_at": "2026-11-19T00:00:00+00:00"},
                {"price": 99.0, "observed_at": "2026-11-20T00:00:00+00:00"},
                {"price": 100.5, "observed_at": "2026-11-21T00:00:00+00:00"},
            ]
        }

        moves = stock_watcher.significant_moves(quote)

        self.assertEqual(len(moves), 3)
        self.assertEqual(
            [move["percent_change"] for move in moves],
            [-3.8834951456310676, 1.9801980198019802, 1.5151515151515151],
        )

    def test_fetches_date_and_ticker_searches_and_merges_deduplicated_results(self):
        responses = [
            {"news": [self.tagged_article]},
            {"news": [self.tagged_article, self.untagged_article]},
        ]
        with (
            patch.object(
                stock_watcher, "fetch_json", side_effect=responses
            ) as fetch,
        ):
            articles = stock_watcher.fetch_related_news("ACME", self.move_date)

        self.assertEqual(len(articles), 2)
        self.assertEqual(articles[0]["url"], "https://example.com/earnings")
        self.assertEqual(articles[1]["url"], "https://example.com/outlook")
        queries = [
            parse_qs(urlparse(call.args[0]).query)["q"][0]
            for call in fetch.call_args_list
        ]
        self.assertEqual(queries, ["ACME 2026-11-18", "ACME"])

    def test_falls_back_to_closest_ticker_article_and_displays_it(self):
        older_article = news_item(
            "Acme announces a product update",
            "https://example.com/product-update",
            datetime(2026, 9, 1),
            ["ACME"],
        )
        with patch.object(
            stock_watcher,
            "fetch_json",
            side_effect=[{"news": []}, {"news": [older_article]}],
        ):
            articles = stock_watcher.fetch_related_news("ACME", self.move_date)

        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0]["title"], "Acme announces a product update")

        quote = {
            "company": "Acme",
            "ticker": "ACME",
            "currency": "USD",
            "price_hint": 2,
            "observations": [
                {"price": 100.0, "observed_at": "2026-11-17T00:00:00+00:00"},
                {"price": 104.0, "observed_at": "2026-11-18T00:00:00+00:00"},
            ],
        }
        moves = stock_watcher.significant_moves(quote)
        news_by_move = {"2026-11-18": articles}

        output = "\n".join(
            stock_watcher.format_move_news(quote, moves, news_by_move, {})
        )
        self.assertIn("Acme announces a product update", output)
        self.assertIn("closest available result", output)

        with TemporaryDirectory() as directory:
            report_path = stock_watcher.write_report(
                quote,
                Path(directory),
                date(2026, 11, 17),
                date(2026, 11, 18),
                moves,
                news_by_move,
            )
            report = report_path.read_text(encoding="utf-8")

        prefix, separator, filename_date = report_path.name[:-3].partition("_")
        self.assertEqual(prefix, "ACME")
        self.assertEqual(separator, "_")
        datetime.strptime(filename_date, "%Y%m%d")

        self.assertIn(
            "[Acme announces a product update](https://example.com/product-update)",
            report,
        )
        self.assertIn("closest available result", report)

    def test_company_name_fallback_returns_closest_related_article(self):
        older_article = news_item(
            "Nvidia expands AI chip supply",
            "https://example.com/nvidia-supply",
            datetime(2026, 9, 1),
        )
        with (
            patch.object(
                stock_watcher,
                "fetch_json",
                side_effect=[
                    {"news": []},
                    {"news": []},
                    {"news": [older_article]},
                ],
            ) as fetch,
        ):
            articles = stock_watcher.fetch_related_news(
                "NVDA", self.move_date, "Nvidia"
            )

        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0]["title"], "Nvidia expands AI chip supply")
        queries = [
            parse_qs(urlparse(call.args[0]).query)["q"][0]
            for call in fetch.call_args_list
        ]
        self.assertEqual(queries, ["NVDA 2026-11-18", "NVDA", "Nvidia"])
        self.assertTrue(
            all(
                urlparse(call.args[0]).netloc == "query1.finance.yahoo.com"
                for call in fetch.call_args_list
            )
        )

    def test_prefers_articles_within_three_days_to_closest_fallback(self):
        recent_article = news_item(
            "Company reports recent results",
            "https://example.com/recent-results",
            datetime(2026, 11, 17),
            ["ACME"],
        )
        older_article = news_item(
            "Company reports results",
            "https://example.com/results",
            datetime(2026, 11, 10),
            ["ACME"],
        )
        with patch.object(
            stock_watcher,
            "fetch_json",
            side_effect=[{"news": [older_article, recent_article]}, {"news": []}],
        ):
            articles = stock_watcher.fetch_related_news("ACME", self.move_date)

        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0]["url"], "https://example.com/recent-results")

    def test_yahoo_finance_is_the_only_news_search_source(self):
        with (
            patch.object(
                stock_watcher,
                "fetch_json",
                return_value={"news": [self.tagged_article]},
            ) as fetch,
        ):
            articles = stock_watcher.fetch_related_news(
                "ACME", self.move_date, "Acme"
            )

        self.assertEqual(len(articles), 1)
        self.assertEqual(
            [parse_qs(urlparse(call.args[0]).query)["q"][0] for call in fetch.call_args_list],
            ["ACME 2026-11-18", "ACME"],
        )
        self.assertTrue(
            all(
                urlparse(call.args[0]).netloc == "query1.finance.yahoo.com"
                for call in fetch.call_args_list
            )
        )

    def test_news_items_are_attributed_to_yahoo_finance(self):
        article = stock_watcher.normalize_news_item(self.tagged_article)
        with TemporaryDirectory() as directory:
            database_path = stock_watcher.initialize_project(Path(directory))
            snapshot_id = stock_watcher.store_snapshot(
                {
                    "company": "Acme",
                    "ticker": "ACME",
                    "currency": "USD",
                    "price": 100.0,
                    "observed_at": "2026-11-18T00:00:00+00:00",
                },
                database_path,
            )
            stock_watcher.store_news_item(article, snapshot_id, database_path)

            with closing(sqlite3.connect(database_path)) as connection, connection:
                source = connection.execute(
                    """
                    SELECT sources.name, sources.url
                    FROM news_items
                    JOIN sources USING (source_id)
                    """
                ).fetchone()

        self.assertEqual(
            source,
            (
                "Yahoo Finance News",
                "https://query1.finance.yahoo.com/v1/finance/search",
            ),
        )

    def test_cli_and_report_include_summary_and_cautious_relevance(self):
        quote = {
            "company": "Acme",
            "ticker": "ACME",
            "currency": "USD",
            "price_hint": 2,
            "observations": [
                {"price": 100.0, "observed_at": "2026-11-17T00:00:00+00:00"},
                {"price": 104.0, "observed_at": "2026-11-18T00:00:00+00:00"},
            ],
        }
        moves = stock_watcher.significant_moves(quote)
        news_by_move = {
            "2026-11-18": [
                stock_watcher.normalize_news_item(self.tagged_article)
            ]
        }

        output = "\n".join(
            stock_watcher.format_move_news(quote, moves, news_by_move, {})
        )
        self.assertIn("Revenue and earnings exceeded expectations.", output)
        self.assertIn("potential context, not proof of causation", output)

        with TemporaryDirectory() as directory:
            report_path = stock_watcher.write_report(
                quote,
                Path(directory),
                date(2026, 11, 17),
                date(2026, 11, 18),
                moves,
                news_by_move,
            )
            report = report_path.read_text(encoding="utf-8")

        self.assertIn("Revenue and earnings exceeded expectations.", report)
        self.assertIn("Why it may be related", report)
        self.assertIn("not proof that the article caused the price move", report)

    def test_partial_lookup_failure_is_not_reported_as_no_matching_news(self):
        with patch.object(
            stock_watcher,
            "fetch_json",
            side_effect=[RuntimeError("service unavailable"), {"news": []}],
        ):
            with self.assertRaisesRegex(RuntimeError, "News searches failed"):
                stock_watcher.fetch_related_news("ACME", self.move_date)


if __name__ == "__main__":
    unittest.main()
