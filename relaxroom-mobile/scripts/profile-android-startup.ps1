# Profiles RelaxRoom Android cold start via adb logcat + optional simpleperf.
# Usage:
#   .\scripts\profile-android-startup.ps1 -Package com.aigalgame.relaxroom
#   .\scripts\profile-android-startup.ps1 -Package com.aigalgame.relaxroom -ForceStop -DurationSec 90

param(
    [string]$Package = "com.aigalgame.relaxroom",
    [switch]$ForceStop,
    [int]$DurationSec = 90,
    [string]$OutputDir = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
if ([string]::IsNullOrWhiteSpace($OutputDir)) {
    $OutputDir = Join-Path $root "startup-profile"
}
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$logPath = Join-Path $OutputDir "logcat-startup-$timestamp.txt"
$startupPath = Join-Path $OutputDir "relaxroom-startup-$timestamp.txt"

Write-Host "Output: $OutputDir"
Write-Host "Package: $Package"

adb devices
if ($ForceStop) {
    adb shell am force-stop $Package
    Start-Sleep -Seconds 2
}

adb logcat -c
$logJob = Start-Job -ScriptBlock {
    param($path)
    adb logcat -v threadtime *:S RelaxRoomStartup:I ReactNativeUnity:D RelaxRoom:I Unity:I ActivityManager:I | Tee-Object -FilePath $path
} -ArgumentList $logPath

Start-Sleep -Seconds 1
adb shell monkey -p $Package -c android.intent.category.LAUNCHER 1 | Out-Null
Write-Host "Launched $Package; capturing ${DurationSec}s of logcat..."

Start-Sleep -Seconds $DurationSec
Stop-Job $logJob -ErrorAction SilentlyContinue
Receive-Job $logJob | Out-Null
Remove-Job $logJob -Force -ErrorAction SilentlyContinue

if (Test-Path $logPath) {
    Select-String -Path $logPath -Pattern "\[RelaxRoomStartup\]" | ForEach-Object { $_.Line } | Set-Content $startupPath
    Write-Host "RelaxRoomStartup lines: $startupPath"
}

Write-Host @"

Next steps (Android Studio):
1. Run -> Profile -> Choose Release APK with debug symbols if available.
2. Cold start the app; stop recording after stable_frame.
3. Inspect main thread for: dlopen, il2cpp::vm, MetadataCache, UnityInitApplication.

Optional simpleperf (device with perf enabled):
  adb shell simpleperf record -p `$(adb shell pidof $Package) --duration $DurationSec -o /data/local/tmp/startup.perf.data
  adb pull /data/local/tmp/startup.perf.data $OutputDir\startup-$timestamp.perf.data

"@
