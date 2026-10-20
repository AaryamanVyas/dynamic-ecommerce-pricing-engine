"""Barcode-matched grocery observations from the public Open Prices API.

Records are dated evidence, not guaranteed current supermarket offers. No private
contributor fields or receipt images are retained. Open Prices data: ODbL 1.0.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import json
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASIS = "recorded_grocery_item_price_excluding_delivery"
SOURCE = "https://prices.openfoodfacts.org"
LICENSE = "https://opendatacommons.org/licenses/odbl/1-0/"
CATALOG = {
    "nutella-400g": {"name":"Nutella Hazelnut Spread · 400 g", "sku":"3017620422003", "gtin":"3017620422003", "category":"Spreads", "icon":"jar", "currentPrice":"6.50", "minimumPrice":"3.00"},
    "nutella-220g": {"name":"Nutella Hazelnut Spread · 220 g", "sku":"80861263", "gtin":"80861263", "category":"Spreads", "icon":"jar", "currentPrice":"4.50", "minimumPrice":"2.00"},
    "barilla-spaghetti-500g": {"name":"Barilla Spaghetti No. 5 · 500 g", "sku":"8076800195057", "gtin":"8076800195057", "category":"Pantry", "icon":"pasta", "currentPrice":"4.00", "minimumPrice":"1.00"},
    "oreo-original-154g": {"name":"Oreo Original Biscuits · 154 g", "sku":"7622300336738", "gtin":"7622300336738", "category":"Snacks", "icon":"cookie", "currentPrice":"4.00", "minimumPrice":"1.00"},
    "emmental-200g": {"name":"Eco+ Grated Emmental · 200 g", "sku":"3450970024686", "gtin":"3450970024686", "category":"Dairy", "icon":"cheese", "currentPrice":"2.50", "minimumPrice":"1.00"},
    "baguette-250g": {"name":"Leclerc Baguette · 250 g", "sku":"3289664113609", "gtin":"3289664113609", "category":"Bakery", "icon":"bread", "currentPrice":"0.75", "minimumPrice":"0.25"},
}


def fetch_page(url):
    request = Request(url, headers={"User-Agent":"PricingClassDemo/1.0 (educational grocery comparison)", "Accept":"application/json"})
    with urlopen(request, timeout=8) as response:
        raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError("Source response exceeds size limit")
        return raw.decode("utf-8")


def _collect_one(key, fetch, today, checked):
    product = {**CATALOG[key], "currency":"EUR", "country":"FR", "comparisonBasis":BASIS,
               "offers":[], "excludedOffers":[], "lowest":None, "highest":None, "sellerCount":0,
               "source":"Open Prices / Open Food Facts", "licenseUrl":LICENSE}
    rows, errors, seen = [], [], set()
    for page in (1, 2):
        query = urlencode({"product_code":product["gtin"], "currency":"EUR", "duplicate_of__isnull":"true", "size":100, "page":page, "order_by":"-date"})
        url = SOURCE + "/api/v1/prices?" + query
        try:
            payload = json.loads(fetch(url))
            if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
                raise ValueError("Unexpected Open Prices response")
            for raw in payload["items"]:
                location = raw.get("location") or {}
                if (raw.get("product_code") != product["gtin"] or raw.get("currency") != "EUR"
                        or location.get("osm_address_country_code") != "FR" or raw.get("duplicate_of") is not None
                        or raw.get("price_per") not in (None,"UNIT") or not raw.get("location_id")):
                    continue
                try:
                    amount = Decimal(str(raw["price"]))
                    day = date.fromisoformat(raw["date"])
                    record_id = int(raw["id"])
                    if not amount.is_finite() or not 0 < amount < 1_000_000 or day > today or record_id in seen:
                        continue
                except (ValueError, TypeError, KeyError, InvalidOperation):
                    continue
                seen.add(record_id)
                store = str(location.get("osm_name") or location.get("osm_brand") or "Recorded store")[:150]
                city = str(location.get("osm_address_city") or "")[:80]
                rows.append({"recordId":record_id,"catalogId":key,"productCode":product["gtin"],
                             "price":str(amount),"date":day.isoformat(),"currency":"EUR","countryCode":"FR",
                             "locationId":raw["location_id"],"seller":store + (" · " + city if city else ""),
                             "series":product["gtin"]+":"+str(raw["location_id"])+":EUR",
                             "discounted":bool(raw.get("price_is_discounted")),
                             "sourceRecordUrl":SOURCE+"/api/v1/prices/"+str(record_id)})
            if int(payload.get("pages",1)) <= page:
                break
        except Exception as exc:
            errors.append({"seller":"Open Prices", "catalogId":key, "error":type(exc).__name__})
            break
    rows.sort(key=lambda row:(row["date"],row["recordId"]))
    if rows:
        newest = max(row["date"] for row in rows)
        cutoff = date.fromisoformat(newest) - timedelta(days=60)
        latest_by_store = {}
        for row in rows:
            if date.fromisoformat(row["date"]) >= cutoff:
                latest_by_store[row["locationId"]] = row
        for row in latest_by_store.values():
            product["offers"].append({"seller":row["seller"],"locationId":row["locationId"],
                                      "price":format(Decimal(row["price"]),".2f"),"rawPrice":row["price"],
                                      "url":row["sourceRecordUrl"],"availability":"recorded_not_live_stock",
                                      "observedOn":row["date"],"checkedAt":checked,"discounted":row["discounted"]})
        values=[Decimal(row["price"]) for row in product["offers"]]
        product.update(lowest=format(min(values),".2f"),highest=format(max(values),".2f"),
                       sellerCount=len(values),latestObservationDate=newest,
                       comparisonWindowStart=cutoff.isoformat(),stale=(today-date.fromisoformat(newest)).days>14,
                       recordsLoaded=len(rows))
    else:
        product.update(stale=True,recordsLoaded=0)
    product["dataNote"]="Latest record per store within 60 days before this product's newest observation. France/EUR only. Collection time is not the price-observation date; availability and delivery are unknown."
    return product, rows, errors


def collect_catalog(fetch=fetch_page, catalog_ids=None, today=None):
    selected=list(CATALOG) if catalog_ids is None else catalog_ids
    if not isinstance(selected,list) or any(not isinstance(key,str) or key not in CATALOG for key in selected):
        raise ValueError("Unknown fixed grocery catalog product")
    today=date.fromisoformat(today) if today else datetime.now(timezone.utc).date()
    checked=datetime.now(timezone.utc).isoformat()
    result={"checkedAt":checked,"products":{},"observations":[],"errors":[],
            "attribution":"Open Prices / Open Food Facts", "license":"ODbL-1.0","licenseUrl":LICENSE}
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs={key:pool.submit(_collect_one,key,fetch,today,checked) for key in selected}
        for key,job in jobs.items():
            product,rows,errors=job.result()
            result["products"][key]=product
            result["observations"].extend(rows)
            result["errors"].extend(errors)
    return result
