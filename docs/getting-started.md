# Windows deployment walkthrough

## Connect your account

Use the AWS CLI and SAM CLI already installed on the machine, or follow their official installers: [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html), [SAM CLI](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html).

```powershell
aws login --profile pricing-personal --remote
aws sts get-caller-identity --profile pricing-personal
```

Open the newly printed login URL in the browser where you are signed in. Enter the authorization code in PowerShell, never in chat or Git. Confirm the returned account is yours. Expired temporary sessions require another login. For an account already using IAM Identity Center, configure/sign into that SSO profile instead.

## Deploy and demonstrate

From the folder containing `template.yaml`:

```powershell
python -m pip install -r requirements-tools.txt
.\scripts\deploy.ps1 -VendorEmail 'your-email@example.com' `
  -Profile pricing-personal -Region ap-south-1 `
  -ReportPath '.local\deployment-report.json'
```

The script asks you to click the SES verification link if the identity is not verified. It waits up to ten minutes, then deploys and runs the full HTTP/EventBridge smoke check. Using one email address for sender and recipient supports the SES sandbox. Deployment needs permissions for the resources named in the README, including SAM's CloudFormation/S3 artifact handling and IAM role creation.

The first mock run should show `unauthenticatedAccess: blocked`, a `source: mock` observation at 26.99 USD, and `notification: accepted_by_ses`. Find the message titled `[MOCK DEMO] Competitor price alert: Logitech M185 Wireless Mouse`. Confirm it recommends 26.99 without changing the stored 29.99 current price.

The API URL appears in CloudFormation outputs and the JSON report. The report is local evidence; do not claim deployment succeeded if the script exits with an error. Repeated runs may suppress email because of cooldown. `-DemoProductId another-demo` creates a separate rule; use deliberately to avoid extra scheduled products.

## Troubleshooting

- `PermissionError` for `work/sam-config/metadata.json`: use the updated helper/deployment script, which clears the old metadata override. SAM now uses its normal user-owned folder. Do not change folder permissions or delete AWS credentials to fix this error.
- `ProfileNotFound` or `NoCredentials`: run login and STS in the same PowerShell session/profile. A sandboxed coding session may lack access to a profile that your own PowerShell can use; run the prepared deployment script directly there.
- `AccessDenied`: inspect the named AWS action and grant the deployment identity the appropriate resource permissions. The login policy by itself does not grant deployment rights.
- SES identity pending: click the latest verification email in the same region selected for the stack. Check spam. Production sending to unverified recipients needs separate SES production access.
- CloudFormation failure: inspect stack events. SAM stops before smoke verification on deploy failure.
- No new history: inspect the `PriceCheckFunction` logs and `FailureQueueUrl`; check that the EventBridge request contains a valid `productId`.
- History exists but no alert: verify the total is below the threshold, the product is active, currency matches, the cooldown is not active, and SES is verified. Inspect `EvaluatePriceFunction` logs and the failure queue.
- Mock mode works but live mode fails: verify approved production credentials, secret ARN access, and real GTINs on all active rules. No known shipping/no matching GTIN means no observation and no fabricated result.

For ordinary API calls, use the signer in `scripts/demo.py` or Postman's AWS Signature authorization with temporary credentials. Do not expose access keys in a browser dashboard. An unsigned `Invoke-RestMethod` example is intentionally not provided because all routes use IAM.
