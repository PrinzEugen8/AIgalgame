# Validates Unity RN export artifact sizes against optional thresholds.
# Usage:
#   .\scripts\check-unity-build-artifacts.ps1
#   .\scripts\check-unity-build-artifacts.ps1 -MaxLibIl2CppMb 80 -MaxMetadataMb 12

param(
    [string]$ReportPath = "",
    [double]$MaxLibIl2CppMb = 0,
    [double]$MaxMetadataMb = 0,
    [double]$MaxTotalMb = 0
)

$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
if ([string]::IsNullOrWhiteSpace($ReportPath)) {
    $ReportPath = Join-Path $root "unity\build-artifacts.json"
}

if (-not (Test-Path $ReportPath)) {
    Write-Error "Artifact report not found: $ReportPath. Export Unity library first."
}

$report = Get-Content $ReportPath -Raw | ConvertFrom-Json
Write-Host "Unity export artifacts ($ReportPath)"
Write-Host "  generated_at_utc: $($report.generated_at_utc)"
Write-Host "  il2cpp_code_generation: $($report.il2cpp_code_generation)"
Write-Host "  total_size_mb: $($report.total_size_mb)"

$failures = @()
foreach ($artifact in $report.artifacts) {
    Write-Host "  $($artifact.name): $($artifact.size_mb) MB"
    switch ($artifact.name) {
        "libil2cpp.so" {
            if ($MaxLibIl2CppMb -gt 0 -and [double]$artifact.size_mb -gt $MaxLibIl2CppMb) {
                $failures += "libil2cpp.so $($artifact.size_mb) MB exceeds max $MaxLibIl2CppMb MB"
            }
        }
        "global-metadata.dat" {
            if ($MaxMetadataMb -gt 0 -and [double]$artifact.size_mb -gt $MaxMetadataMb) {
                $failures += "global-metadata.dat $($artifact.size_mb) MB exceeds max $MaxMetadataMb MB"
            }
        }
    }
}

if ($MaxTotalMb -gt 0 -and [double]$report.total_size_mb -gt $MaxTotalMb) {
    $failures += "total tracked size $($report.total_size_mb) MB exceeds max $MaxTotalMb MB"
}

if ($failures.Count -gt 0) {
    Write-Error ($failures -join "; ")
}

Write-Host "Artifact size check passed."
