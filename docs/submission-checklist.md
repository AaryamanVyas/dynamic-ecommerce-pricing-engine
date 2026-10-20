# Submission and demonstration checklist

## Code and local evidence

- [ ] Run the public-handler test suite and capture the pass count.
- [ ] Capture successful SAM validation and build output.
- [ ] Explain that vendor notification satisfies the brief's “update rates or notify vendors” option.
- [ ] Explain the diagram in the README and the direct API Gateway -> EventBridge check integration.

## Evidence from your own AWS account

- [ ] Confirm account and region using STS, without exposing credentials.
- [ ] Verify the SES sender/demo recipient in the deployment region.
- [ ] Run `scripts/deploy.ps1` successfully and save its deployment report.
- [ ] Capture API rejection of unsigned requests and success of the signed product/check requests.
- [ ] Capture the DynamoDB product rule and new price-history observation.
- [ ] Capture the EventBridge bus, request/observation rules, and hourly schedule.
- [ ] Capture the `[MOCK DEMO]` email and its floor-protected recommendation.
- [ ] Confirm the store price remains unchanged.
- [ ] Show the failure queue and CloudWatch alarm; distinguish recovery from normal delivery.

## Be clear about limitations

- [ ] Identify each demonstrated source accurately: explicit mock, fixed real grocery API catalog, or credentialed eBay.
- [ ] Explain how Secrets Manager and GTIN-based Browse requests enable live mode later.
- [ ] State that API fixture tests do not prove real eBay production access.
- [ ] Explain cooldown, asynchronous retries, and the narrow SES/state-update duplicate window.
- [ ] Identify small-catalog scope, known-shipping requirement for eBay, explicit delivery exclusion for recorded grocery item-price comparisons, and lack of currency conversion.
- [ ] Show the grocery dashboard, exact barcodes, store ranges and individual observation dates.
- [ ] Show same-grocery, same-store history; distinguish historical forecast dates from today.
- [ ] Check your account's billing and remove/disable the demo when no longer needed.

A local UI is now included for presentation. Follow `docs/real-pricing-demo.md`; the signed API demonstration, AWS resources, architecture diagram, source links and real email provide additional evidence.

## Classroom cloud evidence

- [ ] Use Inspect my AWS deployment and show actual account, stack and region.
- [ ] Open discovered API, EventBridge, Lambda, DynamoDB, SES, IAM, logs and SQS/monitoring resources.
- [ ] Show the actual POST /checks integration target and expected bus, not merely an orphaned integration.
- [ ] Match a cloud check EventId to the observation and SES state.
- [ ] Download the AWS evidence report.
- [ ] State that the grocery source is Open Prices; real eBay/RapidAPI access is not verified.
