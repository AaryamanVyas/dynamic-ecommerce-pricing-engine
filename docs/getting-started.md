# Start here (Windows)

## 1. Make sure the account owner is ready

One teammate should own the AWS account and deploy the stack. Before deployment, they need:

- Access to an AWS account and a region to use for the demo.
- Permission to create Lambda, API Gateway, EventBridge, DynamoDB, IAM roles, and CloudFormation resources.
- An email identity verified in Amazon SES. In the SES sandbox, the destination must also be verified.
- For the first deploy, use mock pricing (`UseMockPrices=true`). eBay credentials are optional until the Browse API path is ready.

Never paste AWS secret keys, eBay client secrets, or account passwords into chat or commit them to this folder. Have the account owner configure credentials on their own computer.

## 2. Install the AWS tools

Install the AWS CLI and AWS SAM CLI using their official Windows installers:

- [Install or update the AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html)
- [Install the AWS SAM CLI](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html)

Close and reopen PowerShell, then check that both tools are available:

```powershell
aws --version
sam --version
```

The SAM local emulator requires Docker Desktop. Docker is optional for deploying the stack to AWS, but useful for running the API locally.

## 3. Configure AWS access

Use the account owner's approved AWS sign-in method. For an organization, use its IAM Identity Center (SSO) profile. The owner can configure that profile with `aws configure sso`, then sign in with `aws sso login --profile <profile-name>`. If the class account instead gave you a dedicated access key, configure it locally with the AWS CLI; do not share the key.

Confirm the active identity and region before deploying:

```powershell
aws sts get-caller-identity --profile <profile-name>
aws configure get region --profile <profile-name>
```

If using the default profile, omit `--profile <profile-name>` from the commands below. Choose and consistently use the same AWS region where SES is verified.

## 4. Build and deploy the project

In PowerShell, change to the project folder (the folder containing `template.yaml`), then run:

```powershell
sam validate --lint
sam build
sam deploy --guided
```

For prompts, use a clear stack name such as `dynamic-pricing-demo`, the chosen region, and `UseMockPrices=true`. Enter the verified SES sender address as `VendorEmail`; leave both eBay credential parameters blank for now. Allow SAM to create the IAM roles requested by this template. Review the changes before confirming deployment. Keep the generated `samconfig.toml` local and do not commit it if it contains account-specific details.

If using a named SSO profile, add `--profile <profile-name>` to `sam deploy --guided`. The first deployment may take several minutes. At the end, SAM prints `ApiUrl`; copy it for the next step.

## 5. Add the demo product and trigger a price check

Replace `https://YOUR_API_URL` below with the `ApiUrl` output from deployment (without a trailing slash). Set the recipient to an email that SES is allowed to send to. The account owner can run:

```powershell
$api = "https://YOUR_API_URL"
$body = Get-Content .\events\create-product.json -Raw | ConvertFrom-Json
$product = $body.body | ConvertFrom-Json
$product.vendorEmail = "verified-recipient@example.com"
$payload = $product | ConvertTo-Json -Compress
Invoke-RestMethod -Method Post -Uri "$api/products" -ContentType "application/json" -Body $payload
Invoke-RestMethod -Method Get -Uri "$api/products"
```

To trigger the check immediately instead of waiting for the hourly schedule, open the AWS Lambda console, select `PriceCheckFunction`, and run a test with:

```json
{"productId":"demo-001"}
```

The mock competitor total is $26.99. With the sample current price ($29.99) and undercut threshold (5%), this is below the alert threshold, so SES should send a notification. Check Lambda logs if the email does not arrive.

## 6. Team first-day checkpoint

- Member 1: account owner deploys; verify `POST /products` and `GET /products`.
- Member 2: verify the manual price-check invocation writes a record and publishes its event; keep mock pricing enabled until eBay credentials are confirmed.
- Member 3: verify SES sender/recipient configuration and the alert email.
- All three: rehearse one end-to-end run and capture the API response, Lambda log, DynamoDB record, and email for the submission.

## If deployment or email is blocked

Do not spend the first day waiting on eBay approval. Keep `UseMockPrices=true`. If nobody has AWS deploy access yet, run `sam validate --lint` and `sam build` after installing the tools, and ask the account owner to deploy the template. The full project can still be demonstrated with mocked price events and a recorded walkthrough if SES setup remains pending.
