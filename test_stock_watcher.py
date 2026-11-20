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
            datetime(2026, 11, 16),
        )

    def test_fetches_date_and_ticker_searches_and_merges_deduplicated_results(self):
        responses = [
            {"news": [self.tagged_article]},
            {"news": [self.tagged_article, self.untagged_article]},
        ]
        with patch.object(stock_watcher, "fetch_json", side_effect=responses) as fetch:
            articles = stock_watcher.fetch_related_news("ACME", self.move_date)

        self.assertEqual(len(articles), 2)
        self.assertEqual(articles[0]["url"], "https://example.com/earnings")
        self.assertEqual(articles[1]["url"], "https://example.com/outlook")
        queries = [
            parse_qs(urlparse(call.args[0]).query)["q"][0]
            for call in fetch.call_args_list
        ]
        self.assertEqual(queries, ["ACME 2026-11-18", "ACME"])

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
            with self.assertRaisesRegex(RuntimeError, "news searches failed"):
                stock_watcher.fetch_related_news("ACME", self.move_date)


if __name__ == "__main__":
    unittest.main()
