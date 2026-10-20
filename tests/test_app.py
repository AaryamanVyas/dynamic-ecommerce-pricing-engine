"""Public Lambda interfaces exercised against isolated AWS adapters (Moto)."""
import importlib
import io
import json
import os
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch

import boto3
from moto import mock_aws

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class PricingFlowTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {
            "AWS_ACCESS_KEY_ID": "testing", "AWS_SECRET_ACCESS_KEY": "testing",
            "AWS_SESSION_TOKEN": "testing", "AWS_DEFAULT_REGION": "us-east-1",
            "AWS_CONFIG_FILE": str(Path(__file__).parent / "no-config"),
            "PRODUCTS_TABLE": "products", "PRICES_TABLE": "prices", "EVENT_BUS_NAME": "pricing-test",
            "VENDOR_EMAIL": "demo@example.com", "USE_MOCK_PRICES": "true",
            "ALERT_COOLDOWN_SECONDS": "86400",
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        os.environ.pop("AWS_PROFILE", None)
        os.environ.pop("AWS_DEFAULT_PROFILE", None)
        self.aws = mock_aws()
        self.aws.start()
        self.addCleanup(self.aws.stop)
        boto3.DEFAULT_SESSION = None
        db = boto3.resource("dynamodb")
        db.create_table(TableName="products", BillingMode="PAY_PER_REQUEST",
                        KeySchema=[{"AttributeName": "productId", "KeyType": "HASH"}],
                        AttributeDefinitions=[{"AttributeName": "productId", "AttributeType": "S"}])
        db.create_table(TableName="prices", BillingMode="PAY_PER_REQUEST",
                        KeySchema=[{"AttributeName": "productId", "KeyType": "HASH"},
                                   {"AttributeName": "checkedAt", "KeyType": "RANGE"}],
                        AttributeDefinitions=[{"AttributeName": "productId", "AttributeType": "S"},
                                              {"AttributeName": "checkedAt", "AttributeType": "S"}])
        boto3.client("events").create_event_bus(Name="pricing-test")
        self.ses = boto3.client("ses")
        self.ses.verify_email_identity(EmailAddress="demo@example.com")
        self.app = importlib.reload(importlib.import_module("app"))
        self.product = {
            "productId": "demo-001", "name": "Logitech M185 Wireless Mouse",
            "searchQuery": "Logitech M185 Wireless Mouse", "currentPrice": "29.99",
            "minimumPrice": "24.99", "undercutPercent": "5",
            "vendorEmail": "demo@example.com", "active": True,
        }

    def create(self, **overrides):
        return self.app.create_product({"body": json.dumps({**self.product, **overrides})}, None)

    def observation(self, **overrides):
        return {"detail": {"productId": "demo-001", "checkedAt": "2026-10-20T12:00:00+00:00",
                           "competitorPrice": "26.99", "shipping": "0", "currency": "USD",
                           "source": "mock", **overrides}}

    def test_real_web_collection_reaches_history_and_alert_with_item_price_basis(self):
        from legacy_market import BASIS
        fixtures = Path(__file__).parent / "fixtures"
        created = self.create(productId="web-pico2", currency="GBP", marketplace="EBAY_GB",
                              currentPrice="6.50", minimumPrice="4.00", priceSource="web",
                              catalogId="pico2-headers", comparisonBasis=BASIS)
        self.assertEqual(created["statusCode"], 201)
        def page(request, **kwargs):
            name = "pimoroni.html" if "pimoroni.com" in request.full_url else "pihut.html"
            return io.BytesIO((fixtures / name).read_bytes())
        with patch("legacy_market.urlopen", side_effect=page):
            checked = self.app.check_prices({"detail": {"productId": "web-pico2"}}, None)
        self.assertEqual(checked["observed"], 1)
        history = json.loads(self.app.price_history({"pathParameters": {"productId": "web-pico2"}}, None)["body"])
        self.assertEqual(history[0]["source"], "web")
        self.assertEqual(history[0]["competitorPrice"], "5.70")
        self.assertIsNone(history[0]["shipping"])
        self.assertEqual(history[0]["highestPrice"], "5.80")
        self.assertTrue(self.app.evaluate_price({"detail": history[0]}, None)["alerted"])

    def test_web_catalog_rejects_foreign_currency_and_undeclared_price_basis(self):
        self.assertEqual(self.create(priceSource="web", catalogId="pico2")["statusCode"], 400)
        self.assertEqual(self.create(priceSource="web", catalogId=[])["statusCode"], 400)

    def test_grocery_check_persists_real_observation_date_and_alerts_with_trace_id(self):
        from market import BASIS
        created = self.create(productId="web-nutella-400g", priceSource="web", catalogId="nutella-400g",
                              comparisonBasis=BASIS, currency="EUR", marketplace="WEB_FR",
                              currentPrice="6.50", minimumPrice="3.00")
        self.assertEqual(created["statusCode"], 201)
        payload = (Path(__file__).parent / "fixtures/grocery-nutella.json").read_bytes()
        with patch("market.urlopen", side_effect=lambda *args, **kwargs: io.BytesIO(payload)):
            checked = self.app.check_prices({"id":"grocery-trace-123", "detail":{"productId":"web-nutella-400g"}}, None)
        self.assertEqual(checked["observed"], 1)
        history = json.loads(self.app.price_history({"pathParameters":{"productId":"web-nutella-400g"}}, None)["body"])
        self.assertEqual(history[0]["competitorPrice"], "3.65")
        self.assertEqual(history[0]["priceObservedOn"], "2026-09-04")
        self.assertTrue(history[0]["recordedPriceDemo"])
        self.assertTrue(self.app.evaluate_price({"detail":history[0]},None)["alerted"])
        products = json.loads(self.app.list_products({},None)["body"])
        self.assertEqual(products[0]["lastAlertObservation"], "grocery-trace-123")

    def test_repeated_observation_sends_only_one_alert(self):
        self.assertEqual(self.create()["statusCode"], 201)
        first = self.app.evaluate_price(self.observation(), None)
        second = self.app.evaluate_price(self.observation(), None)
        self.assertTrue(first["alerted"])
        self.assertFalse(second["alerted"])
        self.assertEqual(self.ses.get_send_quota()["SentLast24Hours"], 1)

    def test_new_observation_during_cooldown_does_not_send_again(self):
        self.create()
        self.app.evaluate_price(self.observation(), None)
        result = self.app.evaluate_price(self.observation(checkedAt="2026-10-20T13:00:00+00:00"), None)
        self.assertFalse(result["alerted"])

    def test_threshold_equality_does_not_alert(self):
        self.create()
        result = self.app.evaluate_price(self.observation(competitorPrice="28.4905"), None)
        self.assertFalse(result["alerted"])

    def test_recommendation_respects_minimum_floor(self):
        self.create()
        result = self.app.evaluate_price(self.observation(competitorPrice="20.00"), None)
        self.assertEqual(result["suggestedPrice"], "24.99")

    def test_inactive_product_and_currency_mismatch_are_ignored(self):
        self.create(active=False)
        self.assertIn("ignored", self.app.evaluate_price(self.observation(), None))
        self.create(productId="active-001")
        self.assertIn("ignored", self.app.evaluate_price(
            self.observation(productId="active-001", currency="GBP"), None))
        self.assertEqual(self.ses.get_send_quota()["SentLast24Hours"], 0)

    def test_invalid_observation_is_rejected_without_email(self):
        self.create()
        for price in ("NaN", "Infinity", "-1", "0", True):
            with self.subTest(price=price):
                self.assertIn("ignored", self.app.evaluate_price(self.observation(competitorPrice=price), None))

    def test_product_validation_rejects_unsafe_fields(self):
        for fields in ({"currentPrice": True}, {"currency": "GBP", "marketplace": "EBAY_US"},
                       {"productId": "../bad"}, {"mockCompetitorPrice": "NaN"},
                       {"currentPrice": "29.999"}, {"gtin": "not-a-gtin"}):
            with self.subTest(fields=fields):
                self.assertEqual(self.create(**fields)["statusCode"], 400)

    def test_mock_collection_is_visible_through_price_history(self):
        self.create(mockCompetitorPrice="25.50", mockShipping="1.25")
        result = self.app.check_prices({"detail": {"productId": "demo-001"}}, None)
        self.assertEqual(result["observed"], 1)
        response = self.app.price_history({"pathParameters": {"productId": "demo-001"}}, None)
        self.assertEqual(response["statusCode"], 200)
        observation = json.loads(response["body"])[0]
        self.assertEqual(observation["source"], "mock")
        self.assertEqual(observation["competitorPrice"], "25.50")
        self.assertEqual(observation["shipping"], "1.25")
        self.assertEqual(self.app.evaluate_price({"detail": observation}, None)["suggestedPrice"], "26.75")

    def test_inactive_product_is_not_collected(self):
        self.create(active=False)
        self.assertEqual(self.app.check_prices({"productId": "demo-001"}, None)["observed"], 0)

    def test_failed_event_publication_raises_for_async_retry(self):
        from botocore.stub import Stubber
        self.create()
        with Stubber(self.app.events) as stub:
            stub.add_response("put_events", {"FailedEntryCount": 1, "Entries": [
                {"ErrorCode": "InternalFailure", "ErrorMessage": "retry"}]})
            with self.assertRaises(RuntimeError):
                self.app.check_prices({"productId": "demo-001"}, None)

    def test_failed_ses_send_can_be_retried(self):
        from botocore.stub import Stubber
        self.create()
        with Stubber(self.app.ses) as stub:
            stub.add_client_error("send_email", service_error_code="Throttling", http_status_code=400)
            with self.assertRaises(Exception):
                self.app.evaluate_price(self.observation(), None)
        self.assertTrue(self.app.evaluate_price(self.observation(), None)["alerted"])

    def test_live_collection_skips_unknown_shipping_and_other_currencies(self):
        self.create(gtin="012345678905")
        listing = lambda value, currency="USD", shipping=None: {
            "price": {"value": value, "currency": currency}, "title": "Comparable mouse",
            "itemWebUrl": "https://www.ebay.com/itm/123",
            "shippingOptions": [] if shipping is None else [{"shippingCostType": "FIXED",
                "shippingCost": {"value": shipping, "currency": currency}}],
        }
        response = {"itemSummaries": [listing("1"), listing("2", "GBP", "0"),
                                       listing("20", shipping="4"), listing("25", shipping="1")]}
        encode = lambda data: io.BytesIO(json.dumps(data).encode())
        with patch.dict(os.environ, {"USE_MOCK_PRICES": "false", "EBAY_CLIENT_ID": "fake-id",
                                     "EBAY_CLIENT_SECRET": "fake-secret"}):
            with patch("app.urlopen", side_effect=[encode({"access_token": "fake-token", "expires_in": 7200}),
                                                   encode(response), encode(response)]) as http:
                self.app.check_prices({"productId": "demo-001"}, None)
                self.app.check_prices({"productId": "demo-001"}, None)
                self.assertEqual(http.call_count, 3, "Token should be reused for the second request")
                self.assertIn("gtin=012345678905", http.call_args.args[0].full_url)
        result = json.loads(self.app.price_history({"pathParameters": {"productId": "demo-001"}}, None)["body"])
        self.assertEqual(result[0]["source"], "ebay")
        self.assertEqual(result[0]["competitorPrice"], "20")
        self.assertEqual(result[0]["shipping"], "4")

    def test_live_mode_never_silently_uses_mock_prices(self):
        self.create(gtin="012345678905")
        with patch.dict(os.environ, {"USE_MOCK_PRICES": "false", "EBAY_CLIENT_ID": "", "EBAY_CLIENT_SECRET": ""}):
            with self.assertRaises(RuntimeError):
                self.app.check_prices({"productId": "demo-001"}, None)

    def test_interrupted_alert_remains_retryable_until_lease_expires(self):
        self.create()
        with patch.object(self.app.ses, "send_email", side_effect=SystemExit("simulated timeout")):
            with self.assertRaises(SystemExit):
                self.app.evaluate_price(self.observation(), None)
        with self.assertRaises(RuntimeError):
            self.app.evaluate_price(self.observation(), None)
        future = time.time() + 121
        with patch("app.time.time", return_value=future):
            self.assertTrue(self.app.evaluate_price(self.observation(), None)["alerted"])
        self.assertEqual(self.ses.get_send_quota()["SentLast24Hours"], 1)

    def test_live_mode_requires_gtin_before_any_external_request(self):
        self.create()
        with patch.dict(os.environ, {"USE_MOCK_PRICES": "false", "EBAY_CLIENT_ID": "fake-id", "EBAY_CLIENT_SECRET": "fake-secret"}):
            with patch("app.urlopen") as http:
                with self.assertRaises(RuntimeError):
                    self.app.check_prices({"productId": "demo-001"}, None)
                http.assert_not_called()

    def test_history_validates_limit_and_unknown_product(self):
        self.assertEqual(self.app.price_history({"pathParameters": {"productId": "missing"}}, None)["statusCode"], 404)
        self.assertEqual(self.app.price_history({"pathParameters": {"productId": "demo-001"},
                                               "queryStringParameters": {"limit": "999"}}, None)["statusCode"], 400)

    def test_same_second_check_requests_do_not_overwrite_history(self):
        self.create()
        for event_id in ("request-one", "request-two"):
            self.app.check_prices({"id": event_id, "time": "2026-10-20T12:00:00Z",
                                   "detail": {"productId": "demo-001"}}, None)
        response = self.app.price_history({"pathParameters": {"productId": "demo-001"}}, None)
        rows = json.loads(response["body"])
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["observationId"] for row in rows}, {"request-one", "request-two"})


if __name__ == "__main__":
    unittest.main()
