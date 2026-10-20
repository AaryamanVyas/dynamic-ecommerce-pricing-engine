# Grocery pricing and AWS classroom demonstration

## Scope and vertical slices

The active dashboard is grocery-only: Nutella 400 g, Nutella 220 g, Barilla Spaghetti No. 5 500 g, Oreo Original 154 g, Eco+ grated Emmental 200 g and Leclerc baguette 250 g. User-requested slices: barcode-matched grocery records → same-store history → actual AWS inspection → correlated cloud run and classroom walkthrough. Public test seams are collection, history analysis, Lambda handlers, signed HTTP workflow and read-only AWS inspection. Previous backend adapters remain compatible; electronics are absent from the UI.

## Run from the connected PowerShell

```powershell
.\scripts\start-demo.ps1 -Deploy
```

On this prepared machine, from the repository directory:

```powershell
& ..\outputs\Start-RealPricingDemo.ps1 -Deploy
```

The first run updates your existing CloudFormation stack, preserves stack parameters and opens localhost:8765. Later omit `-Deploy`. Keep the terminal open; Ctrl+C stops the UI. Use `-Port 8766` if needed. For browsing without AWS, `python scripts/dashboard.py --open` works with Python and the bundled snapshot; inspection/cloud buttons require boto3/CRT and the signed-in profile.

## Five-minute presentation

1. **Grocery shelf:** select Nutella 400 g. Show exact GTIN, France/EUR, source links and each observation date. The October 20 data retrieval contains a recorded range of €3.53–€4.68 in the displayed August 7–October 6 window. Refresh queries public records; it does not invent a new receipt date. Other groceries are searchable and grouped by category.
2. **Inspect my AWS deployment:** show actual account, region, stack status and discovered resources. Unknown/denied checks stay unknown. Inspections are fresh per launch; prior-account reports are never restored as current state. Open the CloudFormation stack and service console links.
3. **Security and integration:** show all API routes using IAM, unsigned GET rejection, and POST /checks targeting the actual native EventBridge-PutEvents integration and the expected custom bus. Explain why API acceptance is asynchronous.
4. **Run selected grocery through AWS:** the local server signs an IAM request; the cloud collector fetches its own public source data. Show the accepted EventBridge ID, matching DynamoDB observation and price observation date. Show SES acceptance for the same ID, or explicitly labeled active-cooldown inference/pending state. The demo's saved store prices are illustrative benchmarks, and the recommendation respects its minimum floor. Local price input edits preview only.
5. **Logs, recovery and email:** open collector/evaluator log groups from inspection. Search the observation ID; structured logs identify `price_observation_published` and `vendor_alert_accepted_by_ses`. Show the failure queue, encrypted SQS attributes and alarm resource. The alarm has no notification action; it is a visible monitoring signal. Open the vendor email yourself; SES acceptance alone does not establish inbox delivery.
6. **Same-grocery history:** show Nutella/Clichy, Emmental or baguette. One store and currency per series. Forecast dates are seven days after the last regular-price record. Historical records yield historical replay estimates; products/stores without enough data show that state. Download AWS demo evidence after inspection for review.

## What is actually inspected

Read-only STS identity and CloudFormation resource discovery, with pagination; actual API routes and native integration target/bus; configuration of collector/evaluator Lambdas; active DynamoDB tables and billing mode; history TTL; custom-bus event rules and the stack's default-bus schedule; SES identity and sending/sandbox mode; SQS queue depth and managed encryption. Discovered IAM roles, Lambda helpers, log groups and the alarm have console links. Resource existence alone is not proof a request executed. Event/DynamoDB/SES correlation is separate.

To save an inspection from the terminal:

```powershell
python scripts/aws_inspection.py --profile pricing-personal --region ap-south-1 --stack dynamic-pricing-demo --report ..\outputs\aws-architecture-report.json
```

Inspection never reads secret values or Lambda environment variables. Exported evidence contains account/resource identifiers, checks and the last cloud run, not credentials. Inspection failure leaves the error visible; it does not manufacture a green status. The profile must permit the read operations above; denied optional checks are retained as unknown.

## Source correctness and attribution

Public data: [Open Prices / Open Food Facts](https://prices.openfoodfacts.org), [API documentation](https://github.com/openfoodfacts/open-prices/blob/main/API.md), licensed [ODbL 1.0](https://opendatacommons.org/licenses/odbl/1-0/). Attribution and individual record URLs are retained. Contributor identities, precise coordinates and receipt images are excluded. The bundled snapshot preserves real retrieval times and real observation dates; it is not synthetic history.

Only exact barcode, EUR, France, unit prices and nonduplicate records are accepted. Retrieve up to two pages / 200 records per product. Compare the latest recorded observation per store within 60 days before that product's newest observation. These dates can differ across stores and do not prove today's availability or delivery cost. All source dates are shown. No currency conversion or assumed free shipping. The API is a structured public alternative to supermarket pages that block scraping; no access blocks are bypassed. Its availability is not guaranteed, so the UI keeps visibly dated real cached records. Refresh is limited to once per five minutes.

The original brief names eBay Browse / RapidAPI. eBay support remains prepared and fixture-tested, but no real credentials have been supplied. If the rubric strictly requires a named provider, this grocery API demonstration does not establish that access.

## Forecast method

Use actual observation dates, one product/store/currency, and daily regular-price medians. Promotions are shown and included in extrema but excluded from fitting. Use regular prices in the final 90-day window, requiring at least five distinct dates spanning 14 days. Expanding-window validation selects elapsed-day linear regression only when its absolute errors beat the last-price baseline. Withhold forecasts when the latest regular-price record is over one year old. Target seven days after the last regular-price record. Report MAE/sample size and staleness, not a confidence guarantee. Nutella/Clichy selects €3.50 for April 18 from April 11, 2026; it is not an October forecast. The same-grocery history is genuine public imported history, not months of observations collected by this AWS deployment.

## Architecture discussion points

- API Gateway is IAM protected; browser credentials remain in the local Python server.
- EventBridge decouples collection from evaluation, with an hourly schedule and asynchronous retries.
- Lambda handles collection/evaluation without a continuously running cloud server. The UI itself runs locally.
- DynamoDB separates product rules from product/date history. Tables use pay-per-request billing; existing history expires after 30 days.
- SES sends recommendations, satisfying the brief's notification option; no store price is automatically mutated.
- Execution roles scope service access; inspect actual policies in IAM before making stronger security claims.
- CloudWatch logs retain 14 days; retries and the SQS queue make failures inspectable. The queue alarm does not itself email anyone.
- Per-product alert leases/cooldowns reduce duplicates. A crash between SES acceptance and its final state update can still duplicate an email; exactly-once delivery is not claimed.
- No new hosted frontend service is introduced. Stop/disable the demo when done and inspect AWS billing separately.
