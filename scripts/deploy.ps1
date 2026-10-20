param(
    [Parameter(Mandatory=$true)][string]$VendorEmail,
    [string]$Profile = 'pricing-personal',
    [string]$Region = 'ap-south-1',
    [string]$StackName = 'dynamic-pricing-demo',
    [string]$ReportPath = '',
    [string]$EbaySecretArn = '',
    [string]$Gtin = '',
    [string]$DemoProductId = 'demo-001',
    [switch]$Live,
    [switch]$DisableSchedule
)
$ErrorActionPreference = 'Stop'
if ($Live -and (-not $EbaySecretArn -or -not $Gtin)) {
    throw 'Live deployment requires -EbaySecretArn and a real -Gtin for the demo product.'
}
foreach ($pricingCommand in @('aws','sam','python')) {
    if (-not (Get-Command $pricingCommand -ErrorAction SilentlyContinue)) { throw "Missing command: $pricingCommand" }
}
python -c "import boto3, awscrt" 2>$null
if ($LASTEXITCODE -ne 0) {
    throw 'Deployment tools need SDK/CRT support. Run: python -m pip install -r requirements-tools.txt'
}
$env:AWS_PROFILE = $Profile
$env:AWS_DEFAULT_REGION = $Region
$env:AWS_REGION = $Region
$env:AWS_EC2_METADATA_DISABLED = 'true'
$env:SAM_CLI_TELEMETRY = '0'
# Clear old sandbox metadata overrides, including when rerun in the same shell.
$env:__SAM_CLI_APP_DIR = $null
$pricingRoot = Split-Path $PSScriptRoot -Parent
Push-Location -LiteralPath $pricingRoot
try {
    python scripts/demo.py prepare --profile $Profile --region $Region --email $VendorEmail
    if ($LASTEXITCODE -ne 0) { throw 'AWS account/SES preparation failed.' }
    sam validate --lint --region $Region
    if ($LASTEXITCODE -ne 0) { throw 'SAM validation failed.' }
    sam build
    if ($LASTEXITCODE -ne 0) { throw 'SAM build failed.' }
    $pricingMock = if ($Live) { 'false' } else { 'true' }
    $pricingScheduled = if ($DisableSchedule) { 'false' } else { 'true' }
    $pricingParameters = @("VendorEmail=$VendorEmail", "UseMockPrices=$pricingMock", "EnableSchedule=$pricingScheduled")
    if ($EbaySecretArn) { $pricingParameters += "EbaySecretArn=$EbaySecretArn" }
    sam deploy --stack-name $StackName --region $Region --profile $Profile --resolve-s3 --capabilities CAPABILITY_IAM --no-confirm-changeset --no-fail-on-empty-changeset --parameter-overrides @pricingParameters
    if ($LASTEXITCODE -ne 0) { throw 'SAM deployment failed. Inspect CloudFormation events.' }
    $pricingDemoArgs = @('scripts/demo.py','verify','--profile',$Profile,'--region',$Region,'--stack',$StackName,'--email',$VendorEmail,'--product-id',$DemoProductId)
    if ($ReportPath) { $pricingDemoArgs += @('--report',$ReportPath) }
    if ($Gtin) { $pricingDemoArgs += @('--gtin',$Gtin) }
    python @pricingDemoArgs
    if ($LASTEXITCODE -ne 0) { throw 'Deployment finished, but the cloud demo checks failed. Inspect the error above.' }
} finally {
    Pop-Location
}
