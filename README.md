# Dynamic E-Commerce Pricing & Competitor Monitoring

A serverless demo that checks competitor listings, compares them with a store price and configured minimum, and emails a vendor when a threshold is crossed. For the class demo, it recommends a price and sends an alert; it does not change a live store price.

## Architecture

- API Gateway exposes product create/list endpoints.
- EventBridge schedules periodic checks.
- AWS Lambda functions manage products, collect competitor prices, and evaluate thresholds.
- DynamoDB stores product rules and price history.
- Amazon SES sends vendor notifications.
- eBay Browse API is the planned live source. A mock-price mode is available for development and demos while API access is being set up.

## Team plan (3 members)

| Member | Workstream |
| --- | --- |
| 1 | Product API and DynamoDB data model |
| 2 | Scheduled price collection and eBay Browse API / mock adapter |
| 3 | Threshold evaluation, SES notification, and demo walkthrough |

Agree on the shared product and price-event formats before building in parallel. Integrate the product API, price check, and email path as soon as each part is ready. Target: one product end to end first.

## Requirements

- AWS account and permissions to create Lambda, API Gateway, EventBridge, DynamoDB, IAM, and CloudFormation resources.
- AWS CLI and AWS SAM CLI installed.
- A verified SES sender address; if SES is in its sandbox, the recipient must also be verified.
- eBay developer application credentials only when switching from mock prices to live Browse API requests.

Never commit AWS keys, eBay client secrets, passwords, or personal data. Configure credentials locally or through an approved secrets service.

## Deploy

From this repository's root in PowerShell:

```powershell
aws --version
sam --version
sam validate --lint
sam build
sam deploy --guided
```

During guided deployment, select your AWS region and enter the verified SES sender address. Leave eBay credentials empty and keep mock prices enabled for the first deployment. Review the proposed AWS resources before confirming deployment. Save the `ApiUrl` output for the API steps below.

## Add a demo product

Replace the URL and recipient with values for your deployment. The recipient must be allowed by the SES account's current sending mode.

```powershell
$api = "https://YOUR_API_URL"
$body = Get-Content .\events\create-product.json -Raw | ConvertFrom-Json
$product = $body.body | ConvertFrom-Json
$product.vendorEmail = "verified-recipient@example.com"
$payload = $product | ConvertTo-Json -Compress
Invoke-RestMethod -Method Post -Uri "$api/products" -ContentType "application/json" -Body $payload
Invoke-RestMethod -Method Get -Uri "$api/products"
```

Manually invoke the deployed `PriceCheckFunction` once with this test event to avoid waiting for the schedule:

```json
{"productId":"demo-001"}
```

The mock price is $26.99. With the sample product's $29.99 current price and 5% threshold, it should trigger an alert. No store price is automatically changed.

## Suggested timeline

- **October 17:** agree on the data/event contract; set up AWS/SAM; build each workstream against mock data.
- **October 18:** integrate one complete flow; handle failures; freeze features by evening.
- **October 19:** rehearse, capture screenshots or a short demo, finish the architecture explanation, and submit.

## Project files

The application code, SAM template, and example events should live in `template.yaml`, `src/`, and `events/` respectively. Keep setup and submission notes in `docs/` as the project develops.
