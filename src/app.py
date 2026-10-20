"""Lambda handlers for the pricing demo."""
import json
import os
import re
import time
import base64
import binascii
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from uuid import uuid4
import market as retail_market
import legacy_market

import boto3
from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError

dynamodb = boto3.resource("dynamodb")
events = boto3.client("events")
ses = boto3.client("ses")

PRODUCTS = lambda: dynamodb.Table(os.environ["PRODUCTS_TABLE"])
PRICES = lambda: dynamodb.Table(os.environ["PRICES_TABLE"])
MARKETS = {"EBAY_US": "USD", "EBAY_GB": "GBP", "EBAY_DE": "EUR", "EBAY_CA": "CAD", "EBAY_AU": "AUD"}
PRODUCT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
_token_cache = {"value": None, "expiresAt": 0}
DELIVERY = {"EBAY_US": ("US", "10001"), "EBAY_GB": ("GB", "SW1A1AA"),
            "EBAY_DE": ("DE", "10115"), "EBAY_CA": ("CA", "M5V3L9"), "EBAY_AU": ("AU", "2000")}


def _number(value, name, *, zero=False, cents=False):
    try:
        if isinstance(value, bool) or len(str(value)) > 32:
            raise ValueError()
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0 or (not zero and amount == 0) or amount > Decimal("1000000000"):
            raise ValueError()
        if cents and amount != amount.quantize(Decimal("0.01")):
            raise ValueError()
        return amount
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError(f"{name} must be a finite {'non-negative' if zero else 'positive'} number"
                         + (" with at most two decimal places" if cents else "")) from None


def _public_product(item):
    private = {"alertLeaseId", "alertLeaseUntil", "lastAlertEpoch"}
    return {key: value for key, value in item.items() if key not in private}


def _response(status, body):
    return {"statusCode": status, "headers": {"content-type": "application/json"},
            "body": json.dumps(body, default=str)}


def _body(event):
    try:
        raw = event.get("body") or "{}"
        if event.get("isBase64Encoded"):
            raw = base64.b64decode(raw, validate=True).decode("utf-8")
        body = json.loads(raw)
    except (TypeError, json.JSONDecodeError, binascii.Error, UnicodeDecodeError):
        raise ValueError("Request body must be valid JSON")
    if not isinstance(body, dict):
        raise ValueError("Request body must be a JSON object")
    return body


def _scan_all(table, **kwargs):
    rows = []
    result = table.scan(**kwargs)
    rows.extend(result.get("Items", []))
    while result.get("LastEvaluatedKey"):
        result = table.scan(ExclusiveStartKey=result["LastEvaluatedKey"], **kwargs)
        rows.extend(result.get("Items", []))
    return rows


def create_product(event, context):
    try:
        item = _body(event)
        required = ("name", "searchQuery", "currentPrice", "minimumPrice", "vendorEmail")
        missing = [key for key in required if item.get(key) in (None, "")]
        if missing:
            return _response(400, {"error": "Missing required fields", "fields": missing})
        if not isinstance(item["name"], str) or not item["name"].strip():
            return _response(400, {"error": "name must be a non-empty string"})
        if not isinstance(item["searchQuery"], str) or not item["searchQuery"].strip():
            return _response(400, {"error": "searchQuery must be a non-empty string"})
        if not isinstance(item["vendorEmail"], str) or "@" not in item["vendorEmail"] or any(ch.isspace() for ch in item["vendorEmail"]):
            return _response(400, {"error": "vendorEmail must be a valid email address"})
        if "active" in item and not isinstance(item["active"], bool):
            return _response(400, {"error": "active must be true or false"})
        current = _number(item["currentPrice"], "currentPrice", cents=True)
        minimum = _number(item["minimumPrice"], "minimumPrice", cents=True)
        undercut = _number(item.get("undercutPercent", 5), "undercutPercent", zero=True)
        if (not current.is_finite() or not minimum.is_finite() or not undercut.is_finite()
                or current <= 0 or minimum <= 0 or minimum > current or undercut < 0 or undercut > 100):
            return _response(400, {"error": "Prices must be positive, minimumPrice <= currentPrice, and undercutPercent between 0 and 100"})
        product_id = item.get("productId", str(uuid4()))
        if not isinstance(product_id, str) or not PRODUCT_ID.fullmatch(product_id):
            raise ValueError("productId must contain 1-64 letters, digits, hyphens or underscores")
        grocery = item.get("priceSource") == "web" and isinstance(item.get("catalogId"), str) and item["catalogId"] in retail_market.CATALOG
        market = item.get("marketplace", "WEB_FR" if grocery else "EBAY_US")
        currency = item.get("currency", "EUR" if grocery else "USD")
        if not (grocery and market == "WEB_FR" and currency == "EUR") and (not isinstance(market, str) or market not in MARKETS or currency != MARKETS[market]):
            raise ValueError("Use a supported marketplace and its matching currency")
        if len(item["name"]) > 200 or len(item["searchQuery"]) > 200 or len(item["vendorEmail"]) > 254:
            raise ValueError("Product name, search query, or email is too long")
        if item.get("gtin") is not None and (not isinstance(item["gtin"], str)
                                             or not re.fullmatch(r"(?:\d{8}|\d{12}|\d{13}|\d{14})", item["gtin"])):
            raise ValueError("gtin must be an 8, 12, 13 or 14 digit string")
        web = item.get("priceSource") == "web"
        if item.get("priceSource") not in (None, "web"):
            raise ValueError("priceSource must be web or omitted for the stack default")
        adapter = retail_market if grocery else legacy_market
        if web and (not isinstance(item.get("catalogId"), str) or item["catalogId"] not in adapter.CATALOG or currency != ("EUR" if grocery else "GBP")
                    or item.get("comparisonBasis") != adapter.BASIS):
            raise ValueError("Web products require a known catalogId, matching currency and explicit item-price comparisonBasis")
        if not web and os.environ.get("USE_MOCK_PRICES", "true").lower() != "true" and not item.get("gtin"):
            raise ValueError("gtin is required in live mode to compare the same product")
        product = {
            "productId": product_id,
            "name": item["name"].strip(), "searchQuery": item["searchQuery"].strip(),
            "marketplace": market,
            "currency": currency, "currentPrice": current,
            "minimumPrice": minimum, "undercutPercent": undercut,
            "vendorEmail": item["vendorEmail"], "active": bool(item.get("active", True)),
        }
        if web:
            product.update(priceSource="web", catalogId=item["catalogId"], comparisonBasis=adapter.BASIS)
        for field in ("mockCompetitorPrice", "mockShipping"):
            if field in item:
                product[field] = _number(item[field], field, zero=field == "mockShipping", cents=True)
        if item.get("gtin"):
            product["gtin"] = item["gtin"]
        if "deliveryPostalCode" in item:
            postal_code = item["deliveryPostalCode"]
            if not isinstance(postal_code, str) or not re.fullmatch(r"[A-Za-z0-9 -]{1,12}", postal_code):
                raise ValueError("deliveryPostalCode must contain 1-12 letters, digits, spaces or hyphens")
            product["deliveryPostalCode"] = postal_code
        PRODUCTS().put_item(Item=product, ConditionExpression=Attr("productId").not_exists())
        return _response(201, product)
    except InvalidOperation:
        return _response(400, {"error": "Prices and undercutPercent must be numbers"})
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return _response(409, {"error": "productId already exists"})
        print(f"create_product failed: {exc}")
        return _response(500, {"error": "Unable to save product"})
    except ValueError as exc:
        return _response(400, {"error": str(exc)})
    except Exception as exc:
        print(f"create_product failed: {exc}")
        return _response(500, {"error": "Unable to save product"})


def list_products(event, context):
    try:
        return _response(200, [_public_product(row) for row in _scan_all(PRODUCTS())])
    except Exception as exc:
        print(f"list_products failed: {exc}")
        return _response(500, {"error": "Unable to list products"})


def price_history(event, context):
    product_id = (event.get("pathParameters") or {}).get("productId", "")
    if not isinstance(product_id, str) or not PRODUCT_ID.fullmatch(product_id):
        return _response(400, {"error": "Invalid productId"})
    try:
        limit = int((event.get("queryStringParameters") or {}).get("limit", 50))
        if not 1 <= limit <= 200:
            raise ValueError()
    except (ValueError, TypeError):
        return _response(400, {"error": "limit must be an integer from 1 to 200"})
    try:
        if not PRODUCTS().get_item(Key={"productId": product_id}).get("Item"):
            return _response(404, {"error": "Product not found"})
        rows = PRICES().query(KeyConditionExpression=Key("productId").eq(product_id),
                              ScanIndexForward=False, Limit=limit, ConsistentRead=True)
        return _response(200, rows.get("Items", []))
    except ClientError:
        print("price_history failed")
        return _response(500, {"error": "Unable to load price history"})


def _ebay_token():
    if _token_cache["value"] and time.time() < _token_cache["expiresAt"]:
        return _token_cache["value"]
    client_id = os.environ.get("EBAY_CLIENT_ID", "")
    client_secret = os.environ.get("EBAY_CLIENT_SECRET", "")
    secret_arn = os.environ.get("EBAY_SECRET_ARN")
    if secret_arn:
        payload = boto3.client("secretsmanager").get_secret_value(SecretId=secret_arn)
        credentials = json.loads(payload["SecretString"])
        client_id, client_secret = credentials["clientId"], credentials["clientSecret"]
    if not client_id or not client_secret:
        raise RuntimeError("eBay application credentials are not configured")
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    request = Request("https://api.ebay.com/identity/v1/oauth2/token",
                      data=b"grant_type=client_credentials&scope=https%3A%2F%2Fapi.ebay.com%2Foauth%2Fapi_scope",
                      headers={"Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"})
    with urlopen(request, timeout=10) as result:
        payload = json.loads(result.read())
    _token_cache.update(value=payload["access_token"], expiresAt=time.time() + max(0, int(payload.get("expires_in", 7200)) - 60))
    return _token_cache["value"]


def _competitor(product):
    if product.get("priceSource") == "web":
        grocery = product["catalogId"] in retail_market.CATALOG
        adapter = retail_market if grocery else legacy_market
        result = adapter.collect_catalog(fetch=adapter.fetch_page, catalog_ids=[product["catalogId"]])
        catalog = result["products"][product["catalogId"]]
        if not catalog["offers"]:
            raise RuntimeError("No retail sources succeeded; no mock prices substituted")
        cheapest = min(catalog["offers"], key=lambda row: Decimal(row["price"]))
        return {"competitorPrice": Decimal(cheapest["price"]), "highestPrice": Decimal(catalog["highest"]),
                "shipping": None, "comparisonBasis": adapter.BASIS, "source": "web",
                "currency": "EUR" if grocery else "GBP", "itemUrl": cheapest["url"], "title": catalog["name"],
                "sourceName": "Open Prices / Open Food Facts" if grocery else "Retail web scraping",
                "priceObservedOn": cheapest.get("observedOn"), "recordedPriceDemo": grocery,
                "sellerCount": catalog["sellerCount"], "offers": catalog["offers"],
                "sourceErrors": result["errors"]}
    if os.environ.get("USE_MOCK_PRICES", "true").lower() == "true":
        return {"competitorPrice": product.get("mockCompetitorPrice", Decimal("26.99")),
                "shipping": product.get("mockShipping", Decimal("0")), "source": "mock",
                "currency": product.get("currency", "USD"), "itemUrl": "",
                "title": "Simulated competitor listing"}
    if not product.get("gtin"):
        raise RuntimeError("Live comparisons require a GTIN; keyword-only matches can select accessories")
    token = _ebay_token()
    market = product.get("marketplace", "EBAY_US")
    country, default_postal = DELIVERY[market]
    postal = product.get("deliveryPostalCode", default_postal)
    search = {"gtin": product["gtin"]}
    query = urlencode({**search, "limit": 50, "sort": "price",
                       "filter": f"buyingOptions:{{FIXED_PRICE}},conditionIds:{{1000}},deliveryCountry:{country},deliveryPostalCode:{postal}"})
    url = f"https://api.ebay.com/buy/browse/v1/item_summary/search?{query}"
    request = Request(url, headers={"Authorization": f"Bearer {token}", "X-EBAY-C-MARKETPLACE-ID": market,
                                   "X-EBAY-C-ENDUSERCTX": "contextualLocation=" + quote(f"country={country},zip={postal}", safe="")})
    with urlopen(request, timeout=12) as result:
        payload = json.loads(result.read())
    candidates = []
    for listing in payload.get("itemSummaries", []):
        price = listing.get("price", {})
        if price.get("currency") != product.get("currency", "USD"):
            continue
        try:
            amount = _number(price.get("value"), "listing price")
            costs = [_number(option["shippingCost"]["value"], "shipping", zero=True)
                     for option in listing.get("shippingOptions", [])
                     if option.get("shippingCostType") == "FIXED"
                     and option.get("shippingCost", {}).get("currency") == price["currency"]]
        except (ValueError, KeyError, TypeError):
            continue
        if not costs:
            continue  # Unknown shipping is not free shipping.
        shipping = min(costs)
        candidates.append({"competitorPrice": amount, "shipping": shipping,
                           "currency": price["currency"], "itemUrl": listing.get("itemWebUrl", ""),
                           "source": "ebay", "title": listing.get("title", "")})
    return min(candidates, key=lambda row: row["competitorPrice"] + row["shipping"]) if candidates else None


def check_prices(event, context):
    # Accept either a scheduled invocation (scan active catalog) or a direct test event.
    request = event.get("detail", event)
    if not isinstance(request, dict):
        raise ValueError("Price check detail must be a JSON object")
    product_id = request.get("productId")
    if product_id:
        if not isinstance(product_id, str) or not PRODUCT_ID.fullmatch(product_id):
            raise ValueError("Invalid productId")
        product = PRODUCTS().get_item(Key={"productId": product_id}, ConsistentRead=True).get("Item")
        if not product:
            return {"checked": 0, "observed": 0, "failedProductIds": [], "message": "product not found"}
        if not product.get("active", True):
            return {"checked": 0, "observed": 0, "failedProductIds": [], "message": "inactive product"}
        products = [product]
    else:
        if event.get("source") == "pricing.api":
            raise ValueError("productId is required for an API price check")
        products = _scan_all(PRODUCTS(), FilterExpression=Attr("active").eq(True))
    checked_at = datetime.now(timezone.utc).isoformat()
    observation_id = str(event.get("id") or uuid4())
    observed = 0
    failures = []
    for product in products:
        try:
            result = _competitor(product)
            if not result:
                continue
            detail = {"productId": product["productId"], "checkedAt": checked_at,
                      "observationId": observation_id, **result}
            PRICES().put_item(Item={**detail, "expiresAt": int((datetime.now(timezone.utc) + timedelta(days=30)).timestamp())})
            publish_result = events.put_events(Entries=[{"Source": "pricing.competitor", "DetailType": "CompetitorPriceObserved",
                                                         "Detail": json.dumps(detail, default=str),
                                                         "EventBusName": os.environ.get("EVENT_BUS_NAME", "default")}])
            if publish_result.get("FailedEntryCount", 0):
                entry_error = publish_result.get("Entries", [{}])[0]
                raise RuntimeError(f"Unable to publish price event: {entry_error.get('ErrorMessage', 'unknown EventBridge error')}")
            observed += 1
            print(json.dumps({"event": "price_observation_published", "productId": product["productId"],
                              "observationId": observation_id, "source": result["source"],
                              "priceObservedOn": result.get("priceObservedOn")}, default=str))
        except Exception as exc:
            print(json.dumps({"event": "price_check_failed", "productId": product.get("productId"),
                              "errorType": type(exc).__name__}))
            failures.append(product.get("productId"))
    if failures:
        raise RuntimeError(f"Price collection failed for {len(failures)} product(s); retry or inspect failure queue")
    return {"checked": len(products), "observed": observed, "failedProductIds": failures}


def evaluate_price(event, context):
    detail = event.get("detail", event)
    try:
        if not isinstance(detail, dict) or not isinstance(detail.get("productId"), str) or not detail.get("checkedAt"):
            return {"ignored": "invalid price observation"}
        product = PRODUCTS().get_item(Key={"productId": detail["productId"]}).get("Item")
        if not product:
            return {"ignored": "unknown product"}
        if not product.get("active", True):
            return {"ignored": "inactive product"}
        if detail.get("currency") != product.get("currency", "USD"):
            return {"ignored": "currency mismatch"}
        try:
            if detail.get("source") == "web":
                if product.get("priceSource") != "web" or product.get("comparisonBasis") not in (retail_market.BASIS, legacy_market.BASIS) or detail.get("comparisonBasis") != product.get("comparisonBasis"):
                    return {"ignored": "comparison basis mismatch"}
                competitor_total = _number(detail.get("competitorPrice"), "competitorPrice")
            else:
                competitor_total = _number(detail.get("competitorPrice"), "competitorPrice") + _number(
                    detail.get("shipping", 0), "shipping", zero=True)
        except ValueError:
            return {"ignored": "invalid price observation"}
        current = Decimal(str(product["currentPrice"]))
        minimum = Decimal(str(product["minimumPrice"]))
        percent = Decimal(str(product.get("undercutPercent", 5)))
        threshold = current * (Decimal("1") - percent / Decimal("100"))
        if competitor_total >= threshold:
            return {"alerted": False, "reason": "threshold not met"}
        recommendation = max(minimum, competitor_total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        recipient = product["vendorEmail"]
        now = int(time.time())
        lease_id = str(uuid4())
        observation = detail.get("observationId", detail["checkedAt"])
        try:
            PRODUCTS().update_item(
                Key={"productId": product["productId"]},
                UpdateExpression="SET alertLeaseId = :lease, alertLeaseUntil = :until",
                ConditionExpression=(
                    "attribute_exists(productId) AND "
                    "(attribute_not_exists(alertLeaseUntil) OR alertLeaseUntil < :now) AND "
                    "(attribute_not_exists(lastAlertEpoch) OR lastAlertEpoch <= :cutoff) AND "
                    "(attribute_not_exists(lastAlertObservation) OR lastAlertObservation <> :observation)"
                ),
                ExpressionAttributeValues={
                    ":lease": lease_id, ":until": now + 120, ":now": now,
                    ":cutoff": now - int(os.environ.get("ALERT_COOLDOWN_SECONDS", "86400")),
                    ":observation": observation,
                },
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                latest = PRODUCTS().get_item(Key={"productId": product["productId"]}, ConsistentRead=True).get("Item", {})
                completed = latest.get("lastAlertObservation") == observation
                cooling_down = latest.get("lastAlertEpoch", 0) > now - int(os.environ.get("ALERT_COOLDOWN_SECONDS", "86400"))
                if not completed and not cooling_down and latest.get("alertLeaseUntil", 0) >= now:
                    raise RuntimeError("Alert has an unfinished lease; retry after the lease expires") from exc
                return {"alerted": False, "reason": "duplicate or cooldown"}
            raise
        try:
            mock = detail.get("source") == "mock"
            ses.send_email(
            Source=os.environ["VENDOR_EMAIL"], Destination={"ToAddresses": [recipient]},
            Message={"Subject": {"Data": f"{'[MOCK DEMO] ' if mock else '[RECORDED PRICE DEMO] ' if detail.get('recordedPriceDemo') else ''}Competitor price alert: {product['name']}"},
                     "Body": {"Text": {"Data": (
                         ("SIMULATED PRICE: this alert uses mock data.\n" if mock else
                          (f"Source: Open Prices / Open Food Facts. Recorded grocery item price dated {detail.get('priceObservedOn')}; not a guaranteed current quote. Delivery is unknown and excluded.\n"
                           if detail.get("recordedPriceDemo") else "Source: retail web scraping. Item prices include VAT; delivery is unknown and excluded.\n")
                          if detail.get("source") == "web" else "Source: eBay Browse API.\n") +
                         f"Product: {product['name']}\n"
                         f"Competitor {'item price (delivery excluded)' if detail.get('source') == 'web' else 'total'}: {competitor_total} {product.get('currency', 'USD')}\n"
                         f"Your current price: {current}\n"
                         f"Suggested price (minimum floor applied): {recommendation}\n"
                         f"Listing: {detail.get('itemUrl') or 'Simulated listing; no real URL'}\n"
                         "This is a recommendation only; no store price was changed."
                     )}}}
            )
        except Exception:
            PRODUCTS().update_item(
                Key={"productId": product["productId"]},
                UpdateExpression="REMOVE alertLeaseId, alertLeaseUntil",
                ConditionExpression="alertLeaseId = :lease",
                ExpressionAttributeValues={":lease": lease_id},
            )
            raise
        PRODUCTS().update_item(
            Key={"productId": product["productId"]},
            UpdateExpression=("SET lastAlertEpoch = :now, lastAlertAt = :at, "
                              "lastAlertObservation = :observation, suggestedPrice = :price "
                              "REMOVE alertLeaseId, alertLeaseUntil"),
            ConditionExpression="alertLeaseId = :lease",
            ExpressionAttributeValues={":now": now, ":at": datetime.now(timezone.utc).isoformat(),
                                       ":observation": observation, ":price": recommendation,
                                       ":lease": lease_id},
        )
        print(json.dumps({"event": "vendor_alert_accepted_by_ses", "productId": product["productId"],
                          "observationId": observation, "suggestedPrice": str(recommendation)}))
        return {"alerted": True, "suggestedPrice": str(recommendation)}
    except Exception as exc:
        print(f"evaluate_price failed: {type(exc).__name__}")
        raise
