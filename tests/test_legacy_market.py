"""Public market collection and forecasting behavior; no live requests in tests."""
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from legacy_market import collect_catalog
from trends import analyze_history


class MarketTests(unittest.TestCase):
    def test_expanded_catalog_matches_skus_and_skips_out_of_stock_comparisons(self):
        fixtures = Path(__file__).parent / "fixtures"
        calls = []
        def fetch(url):
            calls.append(url)
            seller = "pimoroni" if "pimoroni.com" in url else "pihut"
            slug = url.rsplit("/", 1)[-1]
            name = seller + ".html" if slug == "raspberry-pi-pico-2" else seller + "-" + slug + ".html"
            return (fixtures / name).read_text(encoding="utf-8")
        result = collect_catalog(fetch)
        self.assertEqual(len(result["products"]), 6)
        self.assertEqual(result["products"]["pico-w"]["lowest"], "5.80")
        self.assertEqual(result["products"]["pico-w"]["sellerCount"], 1)
        self.assertEqual(result["products"]["pico2-w"]["lowest"], "6.60")
        self.assertEqual(result["products"]["pico2-w"]["highest"], "6.70")
        self.assertEqual(result["products"]["pico2-w-headers"]["lowest"], "7.70")
        self.assertEqual(len(calls), 6)
        calls.clear()
        result = collect_catalog(fetch, catalog_ids=["pico2-w"])
        self.assertEqual(list(result["products"]), ["pico2-w"])
        self.assertEqual(len(calls), 2)

    def test_same_variant_gross_prices_are_compared_across_sellers(self):
        fixtures = Path(__file__).parent / "fixtures"
        result = collect_catalog(lambda url: (fixtures / (
            "pimoroni.html" if "pimoroni.com" in url else "pihut.html")).read_text(encoding="utf-8"), catalog_ids=["pico2", "pico2-headers"])
        headers = result["products"]["pico2-headers"]
        self.assertEqual(headers["lowest"], "5.70")
        self.assertEqual(headers["highest"], "5.80")
        self.assertEqual(headers["currency"], "GBP")
        self.assertEqual(headers["comparisonBasis"], "item_price_including_vat_excluding_delivery")
        self.assertEqual(result["products"]["pico2"]["lowest"], "4.80")

    def test_forecast_uses_elapsed_days_and_last_observation_date(self):
        rows = [{"date": day, "price": price, "currency": "GBP", "series": "one-seller-one-sku"}
                for day, price in [("2026-01-01", 10), ("2026-01-03", 12), ("2026-01-09", 18),
                                   ("2026-01-16", 25), ("2026-01-21", 30)]]
        result = analyze_history(rows, today="2026-10-20")
        self.assertEqual(result["forecast"]["date"], "2026-01-28")
        self.assertEqual(result["forecast"]["price"], "37.00")
        self.assertEqual(result["forecast"]["model"], "linear_trend")
        self.assertTrue(result["stale"])

    def test_repeated_refreshes_are_not_a_time_trend_and_series_never_mix(self):
        rows = [{"date": "2026-10-20", "price": 4.80, "currency": "GBP", "series": "one"}] * 8
        self.assertIsNone(analyze_history(rows)["forecast"])
        with self.assertRaises(ValueError):
            analyze_history(rows + [{**rows[0], "currency": "USD"}])

    def test_foreign_currency_or_blocked_source_is_not_counted_as_a_comparable_offer(self):
        fixtures = Path(__file__).parent / "fixtures"
        def fetch(url):
            if "pimoroni.com" in url:
                return (fixtures / "pimoroni.html").read_text()
            return '<meta property="product:price:currency" content="USD">' + (fixtures / "pihut.html").read_text()
        result = collect_catalog(fetch, catalog_ids=["pico2", "pico2-headers"])
        self.assertEqual(result["products"]["pico2-headers"]["sellerCount"], 1)
        self.assertEqual(result["products"]["pico2-headers"]["highest"], "5.70")
        self.assertEqual(len(result["errors"]), 1)

    def test_unavailable_bare_variant_does_not_hide_available_header_variant(self):
        fixtures = Path(__file__).parent / "fixtures"
        def fetch(url):
            if "pimoroni.com" in url:
                return (fixtures / "pimoroni.html").read_text().replace("/InStock", "/OutOfStock", 1)
            return (fixtures / "pihut.html").read_text()
        result = collect_catalog(fetch, catalog_ids=["pico2", "pico2-headers"])
        self.assertEqual(result["products"]["pico2-headers"]["lowest"], "5.70")
        self.assertEqual(result["products"]["pico2"]["sellerCount"], 1)


if __name__ == "__main__":
    unittest.main()
