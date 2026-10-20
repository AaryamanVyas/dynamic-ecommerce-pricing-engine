"""Prepare SES or verify a deployed stack using IAM-signed HTTP requests."""
import argparse
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.exceptions import ClientError


def prepare(session, email):
    identity = session.client("sts").get_caller_identity()
    print(f"Connected AWS account: {identity['Account']}")
    ses = session.client("sesv2")
    try:
        result = ses.get_email_identity(EmailIdentity=email)
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "NotFoundException":
            raise
        ses.create_email_identity(EmailIdentity=email)
        result = {}
    if result.get("VerifiedForSendingStatus"):
        print(f"SES identity verified in {session.region_name}.")
        return
    print(f"Open the AWS verification email sent to {email} and click its link.", flush=True)
    print("Waiting up to 10 minutes for verification...", flush=True)
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        if ses.get_email_identity(EmailIdentity=email).get("VerifiedForSendingStatus"):
            print("SES verification complete.")
            return
        time.sleep(5)
    raise RuntimeError("SES verification is pending. Open the verification email, then rerun deployment.")


def request(session, api, method, path, body=None, signed=True):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    url = api.rstrip("/") + path
    if signed:
        aws_request = AWSRequest(method=method, url=url, data=data, headers=headers)
        credentials = session.get_credentials().get_frozen_credentials()
        SigV4Auth(credentials, "execute-api", session.region_name).add_auth(aws_request)
        headers = dict(aws_request.headers.items())
    try:
        with urlopen(Request(url, data=data, headers=headers, method=method), timeout=20) as response:
            return response.status, json.loads(response.read())
    except HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(text)
        except ValueError:
            payload = {"error": text[:200]}
        return exc.code, payload


def verify(session, args):
    stack = session.client("cloudformation").describe_stacks(StackName=args.stack)["Stacks"][0]
    outputs = {row["OutputKey"]: row["OutputValue"] for row in stack["Outputs"]}
    api = outputs["ApiUrl"]
    status, _ = request(session, api, "GET", "/products", signed=False)
    if status not in (401, 403):
        raise RuntimeError(f"Unsigned request returned {status}; IAM protection check failed")
    product = {
        "productId": args.product_id, "name": "Logitech M185 Wireless Mouse",
        "searchQuery": "Logitech M185 Wireless Mouse", "marketplace": "EBAY_US", "currency": "USD",
        "currentPrice": "29.99", "minimumPrice": "24.99", "undercutPercent": "5",
        "mockCompetitorPrice": "26.99", "mockShipping": "0.00", "vendorEmail": args.email, "active": True,
    }
    if args.gtin:
        product["gtin"] = args.gtin
    status, payload = request(session, api, "POST", "/products", product)
    if status not in (201, 409):
        raise RuntimeError(f"Product creation failed ({status}): {payload}")
    status, products = request(session, api, "GET", "/products")
    if status != 200:
        raise RuntimeError(f"Product listing failed ({status}): {products}")
    existing = next((row for row in products if row["productId"] == args.product_id), None)
    if not existing or any(str(existing.get(key)) != str(product[key]) for key in
                           ("vendorEmail", "currentPrice", "minimumPrice", "active")):
        raise RuntimeError("The demo product already has different settings. Use --product-id with a new ID.")
    if args.gtin and existing.get("gtin") != args.gtin:
        raise RuntimeError("The existing demo product has a different GTIN. Use --product-id with a new ID.")
    status, history = request(session, api, "GET", f"/products/{args.product_id}/prices?limit=1")
    if status != 200:
        raise RuntimeError(f"History API failed ({status}): {history}")
    before = history[0]["checkedAt"] if history else ""
    prior_alert = existing.get("lastAlertAt")
    status, accepted = request(session, api, "POST", "/checks", {"productId": args.product_id})
    if status != 200 or accepted.get("FailedEntryCount", 0) or not accepted.get("Entries", [{}])[0].get("EventId"):
        raise RuntimeError(f"EventBridge rejected the check ({status}): {accepted}")
    print("Check queued through API Gateway -> EventBridge. Waiting for collection and evaluation...", flush=True)
    latest, alert_state = None, None
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        status, history = request(session, api, "GET", f"/products/{args.product_id}/prices?limit=1")
        if status == 200 and history and history[0]["checkedAt"] > before:
            latest = history[0]
            status, products = request(session, api, "GET", "/products")
            if status != 200:
                raise RuntimeError("Unable to retrieve alert status")
            state = next(row for row in products if row["productId"] == args.product_id)
            total = Decimal(latest["competitorPrice"]) + Decimal(latest["shipping"])
            threshold = Decimal(state["currentPrice"]) * (1 - Decimal(state["undercutPercent"]) / 100)
            if total >= threshold:
                alert_state = "not_expected_above_threshold"
                break
            if state.get("lastAlertAt") and state["lastAlertAt"] != prior_alert:
                alert_state = "accepted_by_ses"
                break
            if prior_alert:
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(prior_alert)).total_seconds()
                cooldown = next((float(row["ParameterValue"]) for row in stack.get("Parameters", [])
                                 if row["ParameterKey"] == "AlertCooldownSeconds"), 86400)
                if age < cooldown:
                    alert_state = "previous_alert_exists_cooldown_active"
                    break
        time.sleep(3)
    if latest is None or alert_state is None:
        raise RuntimeError("Timed out: inspect the Lambda logs, SES identity, and FailureQueueUrl stack output")
    if latest.get("source") != outputs["PriceMode"]:
        raise RuntimeError("Observation source does not match the deployed mode")
    queue = session.client("sqs").get_queue_attributes(QueueUrl=outputs["FailureQueueUrl"],
                                                    AttributeNames=["ApproximateNumberOfMessages"])
    if int(queue["Attributes"].get("ApproximateNumberOfMessages", 0)):
        raise RuntimeError("The failure queue contains events. Inspect it before calling the deployment healthy.")
    report = {"stack": args.stack, "apiUrl": api, "mode": outputs["PriceMode"],
              "productId": args.product_id, "unauthenticatedAccess": "blocked", "observation": latest,
              "notification": alert_state, "eventId": accepted["Entries"][0]["EventId"],
              "verifiedAt": datetime.now(timezone.utc).isoformat()}
    if args.report:
        destination = Path(args.report)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    print("Check your inbox for the SES email; API acceptance does not prove inbox delivery.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "verify"])
    parser.add_argument("--profile", default="pricing-personal")
    parser.add_argument("--region", default="ap-south-1")
    parser.add_argument("--stack", default="dynamic-pricing-demo")
    parser.add_argument("--email", required=True)
    parser.add_argument("--product-id", default="demo-001")
    parser.add_argument("--gtin")
    parser.add_argument("--report")
    args = parser.parse_args()
    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    if args.action == "prepare":
        prepare(session, args.email)
    else:
        verify(session, args)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ClientError) as exc:
        raise SystemExit(f"Setup/demo failed: {exc}")
