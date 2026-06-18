$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$patchDir = Join-Path $root "patches\react-native-unity"
$moduleDir = Join-Path $root "node_modules\@azesmway\react-native-unity\android\src\main\java\com\azesmwayreactnativeunity"

if (-not (Test-Path $moduleDir)) {
    Write-Host "Skip: @azesmway/react-native-unity not installed."
    exit 0
}

$files = @("UPlayer.java", "ReactNativeUnity.java", "ReactNativeUnityViewManager.java", "RelaxRoomStartupNativeLog.java")
foreach ($file in $files) {
    $source = Join-Path $patchDir $file
    $target = Join-Path $moduleDir $file
    if (-not (Test-Path $source)) {
        Write-Host "Skip: missing patch source $file"
        continue
    }
    Copy-Item -Force $source $target
    Write-Host "Patched $file"
}

Write-Host "Patched @azesmway/react-native-unity for Unity 6 Android view API."
