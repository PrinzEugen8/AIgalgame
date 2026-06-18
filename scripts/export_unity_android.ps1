param(
    [string]$UnityEditor = "",
    [string]$ProjectPath = "$PSScriptRoot\..\RelaxRoomAI",
    [string]$ExportPath = "$PSScriptRoot\..\relaxroom-mobile\unity\builds\android"
)

$ErrorActionPreference = "Stop"

if (-not $UnityEditor) {
    $candidates = @(
        "E:\unity PROJECT\6000.0.77f1\Editor\Unity.exe",
        "C:\Program Files\Unity\Hub\Editor\6000.0.77f1\Editor\Unity.exe",
        "C:\Program Files\Unity\Hub\Editor\2023.3.62f1\Editor\Unity.exe"
    )
    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) {
            $UnityEditor = $candidate
            break
        }
    }
}

if (-not (Test-Path $UnityEditor)) {
    Write-Host "Unity editor not found."
    Write-Host "Either install Unity 6000.0.77f1 or pass -UnityEditor <path-to-Unity.exe>."
    Write-Host "You can also export manually from the Unity menu:"
    Write-Host "  Tools > AIgalgame > RN > Export Android Library"
    exit 1
}

$lockFile = Join-Path $ProjectPath "Temp\UnityLockfile"
if (Test-Path $lockFile) {
    Write-Host "Unity editor appears to have this project open (UnityLockfile exists)."
    Write-Host "Close Unity, or export manually from the open editor:"
    Write-Host "  Tools > AIgalgame > RN > Export Android Library"
    exit 1
}

New-Item -ItemType Directory -Force -Path $ExportPath | Out-Null

Write-Host "Exporting Unity Android library..."
& $UnityEditor `
    -batchmode `
    -nographics `
    -quit `
    -projectPath $ProjectPath `
    -executeMethod AIgalgame.EditorTools.AIGalgameRnExportTool.ExportAndroidLibrary `
    -logFile "$PSScriptRoot\..\relaxroom-mobile\unity\export.log"

if ($LASTEXITCODE -ne 0) {
    Write-Error "Unity export failed. See relaxroom-mobile/unity/export.log"
}

Write-Host "Export complete: $ExportPath"
