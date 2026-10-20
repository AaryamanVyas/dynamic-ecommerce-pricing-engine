"""Exercise the dashboard's cloud workflow at its signed-HTTP adapter boundary."""
from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from dashboard import Demo
from market import BASIS


class DashboardCloudTests(unittest.TestCase):
    def test_correlates_event_and_waits_past_expired_cooldown_for_confirmed_alert(self):
        demo = Demo("unused", "ap-south-1", "test-stack", "demo@example.com")
        product = {"productId": "web-nutella-400g", "priceSource": "web", "catalogId": "nutella-400g",
                   "comparisonBasis": BASIS, "currency": "EUR", "currentPrice": "6.50", "minimumPrice": "3.00",
                   "undercutPercent": "5", "vendorEmail": "demo@example.com", "lastAlertAt": "2000-01-01T00:00:00+00:00"}
        session = Mock()
        session.client.return_value.describe_stacks.return_value = {"Stacks": [{"Outputs": [{"OutputKey": "ApiUrl", "OutputValue": "https://example.test/v1"}],
                                                                              "Parameters": [{"ParameterKey": "AlertCooldownSeconds", "ParameterValue": "86400"}]}]}
        polls = []
        def request(session, api, method, path, body=None):
            if method == "POST" and path == "/products":
                return 409, {}
            if method == "POST" and path == "/checks":
                return 200, {"Entries": [{"EventId": "requested-event"}]}
            if method == "GET" and path.startswith("/products/web-"):
                polls.append(1)
                return 200, [{"observationId": "other-event" if len(polls) == 1 else "requested-event",
                              "source": "web", "competitorPrice": "5.70"}]
            if method == "GET" and path == "/products":
                if len(polls) < 3:
                    return 200, [product]
                return 200, [{**product, "lastAlertAt": datetime.now(timezone.utc).isoformat(),
                              "lastAlertObservation": "requested-event", "suggestedPrice": "5.70"}]
            self.fail(f"Unexpected request: {method} {path}")
        with patch("boto3.Session", return_value=session), patch("demo.request", side_effect=request), patch("dashboard.time.sleep"):
            result = demo.cloud_check("nutella-400g")
        self.assertEqual(len(polls), 3)
        self.assertEqual(result["notification"], "accepted_by_ses")
        self.assertEqual(result["observation"]["observationId"], "requested-event")


if __name__ == "__main__":
    unittest.main()
