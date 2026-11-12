<#
.SYNOPSIS
Validates and streams the operational-efficiency CSVs into the existing Azure SQL database.
.EXAMPLE
.\scripts\operational-efficiency\Import-OperationalEfficiency.ps1 -Mode Check
.EXAMPLE
.\scripts\operational-efficiency\Import-OperationalEfficiency.ps1 -Mode Validate -AllowOrganizationCityRepair
.EXAMPLE
.\scripts\operational-efficiency\Import-OperationalEfficiency.ps1 -Mode Import -AllowOrganizationCityRepair
#>
[CmdletBinding()]
param(
    [ValidateSet('Check', 'Validate', 'Import', 'Finalize', 'Status', 'Scripts', 'Diagnostics')]
    [string]$Mode = 'Check',
    [string]$DataDirectory = (Join-Path $PSScriptRoot '..\..\va-data\OperationalEfficiency-Relational'),
    [string]$ManifestPath,
    [switch]$AllowOrganizationCityRepair,
    [ValidateRange(1, 100000)]
    [int]$BatchSize = 25000,
    [ValidateSet('Browser', 'AzureCli')]
    [string]$Authentication = 'Browser',
    [string]$UserId
)

$ErrorActionPreference = 'Stop'
if (-not (Get-Command dotnet -ErrorAction SilentlyContinue)) {
    throw '.NET 10 SDK is required.'
}
$project = Join-Path $PSScriptRoot 'OperationalEfficiency.Import\OperationalEfficiency.Import.csproj'
$arguments = @(
    'run', '--configuration', 'Release', '--project', $project, '--',
    $Mode.ToLowerInvariant(),
    '--data', $DataDirectory,
    '--output', $PSScriptRoot,
    '--allow-city-repair', $AllowOrganizationCityRepair.IsPresent.ToString().ToLowerInvariant(),
    '--batch-size', $BatchSize.ToString(),
    '--authentication', $Authentication
)
if ($ManifestPath) { $arguments += @('--manifest', $ManifestPath) }
if ($UserId) { $arguments += @('--user', $UserId) }
& dotnet @arguments
if ($LASTEXITCODE -ne 0) {
    throw "Operational-efficiency $Mode failed (exit code $LASTEXITCODE)."
}
