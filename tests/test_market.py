"""Grocery collection at its public HTTP-data adapter seam."""
import json
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from market import collect_catalog
from trends import analyze_history

class GroceryTests(unittest.TestCase):
    def test_exact_barcode_country_currency_dates_and_latest_record_per_store(self):
        def record(id, code, price, day, store, country="FR", currency="EUR"):
            return {"id":id,"product_code":code,"price":price,"date":day,"location_id":store,
                    "currency":currency,"location":{"osm_name":"Store "+str(store),"osm_address_country_code":country}}
        rows=[record(1,"3017620422003",3.50,"2026-10-01",1),record(2,"3017620422003",3.70,"2026-10-10",1),
              record(3,"3017620422003",4.20,"2026-10-11",2),record(4,"wrong-pack",.50,"2026-10-11",3),
              record(5,"3017620422003",.40,"2026-10-11",4,country="DE"),record(6,"3017620422003",.30,"2026-10-11",5,currency="USD")]
        result=collect_catalog(lambda url: json.dumps({"items":rows,"pages":1}),catalog_ids=["nutella-400g"],today="2026-10-20")
        product=result["products"]["nutella-400g"]
        self.assertEqual(product["lowest"],"3.70")
        self.assertEqual(product["highest"],"4.20")
        self.assertEqual(product["sellerCount"],2)
        self.assertEqual(product["offers"][0]["observedOn"],"2026-10-10")
        self.assertEqual(len(result["observations"]),3)
        self.assertEqual(product["currency"],"EUR")
        self.assertNotIn("owner",str(result))

    def test_old_records_are_labeled_and_page_failures_are_explicit(self):
        row={"id":1,"product_code":"3017620422003","price":3.50,"date":"2026-04-11","location_id":1,
             "currency":"EUR","location":{"osm_address_country_code":"FR"}}
        result=collect_catalog(lambda url:json.dumps({"items":[row],"pages":1}),catalog_ids=["nutella-400g"],today="2026-10-20")
        self.assertTrue(result["products"]["nutella-400g"]["stale"])
        self.assertEqual(result["products"]["nutella-400g"]["latestObservationDate"],"2026-04-11")
        with self.assertRaises(ValueError):collect_catalog(catalog_ids=["arbitrary-url"])

    def test_decade_old_grocery_prices_do_not_produce_a_forecast(self):
        rows = [{"date": day, "price": "1.42", "currency": "EUR", "series": "cheese:one-store"}
                for day in ("2011-04-01", "2011-04-08", "2011-04-15", "2011-04-22", "2011-04-29")]
        analysis = analyze_history(rows, today="2026-10-20")
        self.assertIsNone(analysis["forecast"])
        self.assertIn("over a year old", analysis["reason"])

if __name__=="__main__":unittest.main()
