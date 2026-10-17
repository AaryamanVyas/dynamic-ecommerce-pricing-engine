"""Lambda handlers for the pricing demo."""
import json
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
from uuid import uuid4

import boto3
from boto3.dynamodb.conditions import Attr
from botocore.exceptions import ClientError

dynamodb = boto3.resource("dynamodb")
events = boto3.client("events")
ses = boto3.client("ses")

PRODUCTS = lambda: dynamodb.Table(os.environ["PRODUCTS_TABLE"])
PRICES = lambda: dynamodb.Table(os.environ["PRICES_TABLE"])


def _response(status, body):
    return {"statusCode": status, "headers": {"content-type": "application/json"},
            "body": json.dumps(body, default=str)}


def _body(event):
    try:
        return json.loads(event.get("body") or "{}")
    except (TypeError, json.JSONDecodeError):
        raise ValueError("Request body must be valid JSON")


def create_product(event, context):
    try:
        item = _body(event)
        required = ("name", "searchQuery", "currentPrice", "minimumPrice", "vendorEmail")
        missing = [key for key in required if item.get(key) in (None, "")]
        if missing:
            return _response(400, {"error": "Missing required fields", "fields": missing})
        current = Decimal(str(item["currentPrice"]))
        minimum = Decimal(str(item["minimumPrice"]))
        undercut = Decimal(str(item.get("undercutPercent", 5)))
        if current <= 0 or minimum <= 0 or minimum > current or undercut < 0 or undercut > 100:
            return _response(400, {"error": "Prices must be positive, minimumPrice <= currentPrice, and undercutPercent between 0 and 100"})
        product = {
            "productId": item.get("productId") or str(uuid4()),
            "name": str(item["name"]), "searchQuery": str(item["searchQuery"]),
            "marketplace": item.get("marketplace", "EBAY_US"),
            "currency": item.get("currency", "USD"), "currentPrice": current,
            "minimumPrice": minimum, "undercutPercent": undercut,
            "vendorEmail": item["vendorEmail"], "active": bool(item.get("active", True)),
        }
        PRODUCTS().put_item(Item=product, ConditionExpression=Attr("productId").not_exists())
        return _response(201, product)
    except InvalidOperation:
        return _response(400, {"error": "Prices and undercutPercent must be numbers"})
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return _response(409, {"error": "productId already exists"})
        print(f"create_product failed: {exc}")
        return _response(500, {"error": "Unable to save product"})
    except Exception as exc:
        print(f"create_product failed: {exc}")
        return _response(500, {"error": "Unable to save product"})


def list_products(event, context):
    try:
        rows = PRODUCTS().scan().get("Items", [])
        return _response(200, rows)
    except Exception as exc:
        print(f"list_products failed: {exc}")
        return _response(500, {"error": "Unable to list products"})


def _ebay_token():
    client_id = os.environ.get("EBAY_CLIENT_ID", "")
    client_secret = os.environ.get("EBAY_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        raise RuntimeError("eBay application credentials are not configured")
    import base64
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    request = Request("https://api.ebay.com/identity/v1/oauth2/token",
                      data=b"grant_type=client_credentials&scope=https%3A%2F%2Fapi.ebay.com%2Foauth%2Fapi_scope",
                      headers={"Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"})
    with urlopen(request, timeout=10) as result:
        return json.loads(result.read())["access_token"]


def _competitor(product):
    if os.environ.get("USE_MOCK_PRICES", "true").lower() == "true":
        # Deterministic local/demo fallback; replace with eBay when credentials are approved.
        return {"competitorPrice": Decimal("26.99"), "shipping": Decimal("0"),
                "currency": product.get("currency", "USD"), "itemUrl": "https://www.ebay.com/"}
    token = _ebay_token()
    query = quote(product["searchQuery"])
    url = f"https://api.ebay.com/buy/browse/v1/item_summary/search?q={query}&limit=10&filter=buyingOptions:FIXED_PRICE"
    request = Request(url, headers={"Authorization": f"Bearer {token}", "X-EBAY-C-MARKETPLACE-ID": product.get("marketplace", "EBAY_US")})
    with urlopen(request, timeout=12) as result:
        payload = json.loads(result.read())
    candidates = []
    for listing in payload.get("itemSummaries", []):
        price = listing.get("price", {})
        if price.get("currency") != product.get("currency", "USD"):
            continue
        amount = Decimal(price["value"])
        shipping = Decimal("0")
        options = listing.get("shippingOptions", [])
        if options and options[0].get("shippingCost", {}).get("currency") == price.get("currency"):
            shipping = Decimal(options[0]["shippingCost"]["value"])
        candidates.append({"competitorPrice": amount, "shipping": shipping,
                           "currency": price["currency"], "itemUrl": listing.get("itemWebUrl", "")})
    return min(candidates, key=lambda row: row["competitorPrice"] + row["shipping"]) if candidates else None


def check_prices(event, context):
    # Accept either a scheduled invocation (scan active catalog) or a direct test event.
    if event.get("productId"):
        product = PRODUCTS().get_item(Key={"productId": event["productId"]}).get("Item")
        products = [product] if product else []
    else:
        products = PRODUCTS().scan(FilterExpression=Attr("active").eq(True)).get("Items", [])
    checked_at = datetime.now(timezone.utc).isoformat()
    observed = 0
    failures = []
    for product in products:
        try:
            result = _competitor(product)
            if not result:
                continue
            detail = {"productId": product["productId"], "checkedAt": checked_at, **result}
            PRICES().put_item(Item={**detail, "expiresAt": int((datetime.now(timezone.utc) + timedelta(days=30)).timestamp())})
            events.put_events(Entries=[{"Source": "pricing.competitor", "DetailType": "CompetitorPriceObserved",
                                        "Detail": json.dumps(detail, default=str), "EventBusName": "default"}])
            observed += 1
        except (HTTPError, URLError, TimeoutError, RuntimeError, ValueError) as exc:
            print(f"price check failed for {product.get('productId')}: {exc}")
            failures.append(product.get("productId"))
    return {"checked": len(products), "observed": observed, "failedProductIds": failures}


def evaluate_price(event, context):
    detail = event.get("detail", event)
    try:
        product = PRODUCTS().get_item(Key={"productId": detail["productId"]}).get("Item")
        if not product:
            return {"ignored": "unknown product"}
        competitor_total = Decimal(str(detail["competitorPrice"])) + Decimal(str(detail.get("shipping", 0)))
        current = Decimal(str(product["currentPrice"]))
        minimum = Decimal(str(product["minimumPrice"]))
        percent = Decimal(str(product.get("undercutPercent", 5)))
        threshold = current * (Decimal("1") - percent / Decimal("100"))
        if competitor_total >= threshold:
            return {"alerted": False, "reason": "threshold not met"}
        recommendation = max(minimum, competitor_total.quantize(Decimal("0.01")))
        recipient = product["vendorEmail"]
        ses.send_email(
            Source=os.environ["VENDOR_EMAIL"], Destination={"ToAddresses": [recipient]},
            Message={"Subject": {"Data": f"Competitor price alert: {product['name']}"},
                     "Body": {"Text": {"Data": (
                         f"A comparable listing was found for {product['name']}.\n"
                         f"Competitor total: {competitor_total} {product.get('currency', 'USD')}\n"
                         f"Your current price: {current}\n"
                         f"Suggested price (minimum floor applied): {recommendation}\n"
                         f"Listing: {detail.get('itemUrl', 'not available')}\n"
                         "This is a recommendation only; no store price was changed."
                     )}}}
        )
        return {"alerted": True, "suggestedPrice": str(recommendation)}
    except Exception as exc:
        print(f"evaluate_price failed: {exc}")
        raise
