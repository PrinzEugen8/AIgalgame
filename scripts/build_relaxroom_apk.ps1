param(
    [string]$UnityEditor = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path $PSScriptRoot -Parent
$mobileRoot = Join-Path $repoRoot "relaxroom-mobile"
$unityLibrary = Join-Path $mobileRoot "unity\builds\android\unityLibrary"

& (Join-Path $PSScriptRoot "export_unity_android.ps1") -UnityEditor $UnityEditor

if (Test-Path $unityLibrary) {
    & (Join-Path $mobileRoot "scripts\patch-unity-export.ps1")
} else {
    Write-Error "unityLibrary not found. Export Unity before building the APK."
}

Push-Location $mobileRoot
try {
    if (-not (Test-Path "node_modules")) {
        $env:Path = "E:\AIgalgame\tools\node-v20;" + $env:Path
        npm install --no-package-lock --legacy-peer-deps
    }

    Push-Location android
    .\gradlew.bat assembleDebug
    Pop-Location
}
finally {
    Pop-Location
}

Write-Host "Debug APK:"
Write-Host (Join-Path $mobileRoot "android\app\build\outputs\apk\debug\app-debug.apk")
