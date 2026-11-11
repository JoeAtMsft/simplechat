<#
.SYNOPSIS
Downloads all blobs from hack2025shared/data into the repository's va-data folder.
.DESCRIPTION
Requires Azure CLI. Uses the current az login unless AZURE_STORAGE_SAS_TOKEN
is set to a container SAS with read and list permissions (sp=rl).
Preserves blob folder paths and skips existing files unless -Overwrite is set.
.EXAMPLE
.\scripts\download-va-data.ps1
.EXAMPLE
$env:AZURE_STORAGE_SAS_TOKEN = '<container SAS with read and list permissions>'
.\scripts\download-va-data.ps1
Remove-Item Env:\AZURE_STORAGE_SAS_TOKEN
.EXAMPLE
.\scripts\download-va-data.ps1 -DryRun
#>
[CmdletBinding()]
param(
    [switch]$Overwrite,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
    throw 'Azure CLI is required. Install it and run az login, or set AZURE_STORAGE_SAS_TOKEN.'
}

$destination = Join-Path (Split-Path -Parent $PSScriptRoot) 'va-data'
New-Item -ItemType Directory -Path $destination -Force | Out-Null

$authMode = 'login'
if ($env:AZURE_STORAGE_SAS_TOKEN) {
    $authMode = 'key'
}

$arguments = @(
    'storage', 'blob', 'download-batch',
    '--source', 'https://hack2025shared.blob.core.windows.net/data',
    '--destination', $destination,
    '--auth-mode', $authMode,
    '--overwrite', $Overwrite.IsPresent.ToString().ToLowerInvariant(),
    '--no-progress',
    '--only-show-errors',
    '--output', 'none'
)
if ($DryRun) {
    $arguments += '--dryrun'
}

& az @arguments
if ($LASTEXITCODE -ne 0) {
    throw "Blob download failed (Azure CLI exit code $LASTEXITCODE). Check access and SAS expiry; a SAS must allow both read and list."
}

if ($DryRun) {
    Write-Host "Download preview completed for $destination"
} else {
    Write-Host "Download completed: $destination"
}
