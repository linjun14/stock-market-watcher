import sys
import unittest
from datetime import date, datetime, timezone
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
            datetime(2026, 11, 8),
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
            patch.object(stock_watcher, "fetch_google_news", return_value=[]),
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
        ), patch.object(stock_watcher, "fetch_google_news", return_value=[]):
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

        self.assertIn(
            "[Acme announces a product update](https://example.com/product-update)",
            report,
        )
        self.assertIn("closest available result", report)

    def test_google_news_rss_article_is_included_and_attributed(self):
        article = {
            **stock_watcher.normalize_news_item(
                news_item(
                    "Acme product launch",
                    "https://example.com/launch",
                    datetime(2026, 11, 17),
                )
            ),
            "search_provider": "Google News",
            "publisher": "Example News",
        }
        with (
            patch.object(
                stock_watcher,
                "fetch_json",
                return_value={"news": []},
            ),
            patch.object(
                stock_watcher,
                "fetch_google_news",
                side_effect=[[article], []],
            ) as fetch_news,
        ):
            articles = stock_watcher.fetch_related_news(
                "ACME", self.move_date, "Acme"
            )

        self.assertEqual(articles[0]["search_provider"], "Google News")
        self.assertIn(
            '"Acme" ACME 2026-11-18',
            fetch_news.call_args_list[0].args[0],
        )

        quote = {
            "company": "Acme",
            "ticker": "ACME",
            "currency": "USD",
            "price_hint": 2,
            "observations": [
                {"price": 100.0, "observed_at": "2026-11-16T00:00:00+00:00"},
                {"price": 104.0, "observed_at": "2026-11-17T00:00:00+00:00"},
            ],
        }
        output = "\n".join(
            stock_watcher.format_move_news(
                quote,
                stock_watcher.significant_moves(quote),
                {"2026-11-17": articles},
                {},
            )
        )
        self.assertIn("Acme product launch", output)
        self.assertIn("Google News returned it", output)

        with TemporaryDirectory() as directory:
            report_path = stock_watcher.write_report(
                quote,
                Path(directory),
                date(2026, 11, 16),
                date(2026, 11, 17),
                stock_watcher.significant_moves(quote),
                {"2026-11-17": articles},
            )
            report = report_path.read_text(encoding="utf-8")
        self.assertIn("[Acme product launch](https://example.com/launch)", report)
        self.assertIn("Example News", report)

    def test_parses_google_news_rss_article_metadata(self):
        feed = b"""<rss><channel><item>
            <title>Acme reports results</title>
            <link>https://example.com/results</link>
            <pubDate>Tue, 17 Nov 2026 12:00:00 GMT</pubDate>
            <source>Example News</source>
            <description><![CDATA[<p>Revenue <b>grew</b>.</p>]]></description>
        </item></channel></rss>"""

        articles = stock_watcher.parse_google_news_rss(feed)

        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0]["publisher"], "Example News")
        self.assertEqual(articles[0]["published_at"], "2026-11-17T12:00:00+00:00")
        self.assertEqual(articles[0]["summary"], "Revenue grew.")

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
        ), patch.object(stock_watcher, "fetch_google_news", return_value=[]):
            with self.assertRaisesRegex(RuntimeError, "News searches failed"):
                stock_watcher.fetch_related_news("ACME", self.move_date)


if __name__ == "__main__":
    unittest.main()
