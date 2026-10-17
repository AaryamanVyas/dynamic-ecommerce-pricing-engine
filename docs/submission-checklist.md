# Submission checklist

- [ ] Replace the sample SES recipient with the verified demo recipient.
- [ ] Confirm AWS account, region, permissions, and SES sender identity.
- [ ] Keep mock prices enabled until eBay application credentials and API access work.
- [ ] Create one product using `events/create-product.json`.
- [ ] Invoke the scheduled checker for `demo-001`.
- [ ] Confirm a price history record is written and the threshold email arrives.
- [ ] Rehearse the fallback with `events/observed-price.json` if external API access fails.
- [ ] Prepare an architecture diagram, README walkthrough, screenshots, and limitations.
- [ ] State clearly that the demo recommends prices and sends alerts; it does not change a live store price.
