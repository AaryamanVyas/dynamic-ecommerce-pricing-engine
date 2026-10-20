param([string]$PersonalHelper = '')
$ErrorActionPreference = 'Stop'
$pricingRepoUnderTest = Split-Path $PSScriptRoot -Parent
$env:__SAM_CLI_APP_DIR = Join-Path $PSScriptRoot 'inaccessible-old-sam-state'
$global:pricingSamCommandsForTest = [System.Collections.Generic.List[string]]::new()

# External command adapters: no AWS requests, credentials, or installations.
function aws { $global:LASTEXITCODE = 0 }
function python { $global:LASTEXITCODE = 0 }
function sam {
    if ($args[0] -eq '--version') { $global:LASTEXITCODE = 0; return }
    if ($env:__SAM_CLI_APP_DIR) {
        throw 'SAM received the inherited metadata override that caused PermissionError.'
    }
    $global:pricingSamCommandsForTest.Add([string]$args[0])
    $global:LASTEXITCODE = 0
}

if ($PersonalHelper) {
    . $PersonalHelper
    if ($env:__SAM_CLI_APP_DIR) { throw 'Personal helper retained the inaccessible SAM override.' }
}
& (Join-Path $pricingRepoUnderTest 'scripts/deploy.ps1') -VendorEmail 'test@example.com' -Profile 'offline-test'
if (($global:pricingSamCommandsForTest -join ',') -ne 'validate,build,deploy') {
    throw 'Expected validate, build, and deploy commands to receive the corrected environment.'
}
Write-Output 'Deployment environment regression passed (no cloud calls).'
