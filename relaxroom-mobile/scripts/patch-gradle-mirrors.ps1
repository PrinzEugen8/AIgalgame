$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$pluginRoot = Join-Path $root "node_modules\@react-native\gradle-plugin"
$patchDir = Join-Path $root "patches\gradle-plugin"

if (-not (Test-Path $pluginRoot)) {
    Write-Host "Skip: @react-native/gradle-plugin not installed yet."
    exit 0
}

$files = @{
    "settings.gradle.kts" = Join-Path $pluginRoot "settings.gradle.kts"
    "react-native-gradle-plugin.build.gradle.kts" = Join-Path $pluginRoot "react-native-gradle-plugin\build.gradle.kts"
    "settings-plugin.build.gradle.kts" = Join-Path $pluginRoot "settings-plugin\build.gradle.kts"
}

foreach ($entry in $files.GetEnumerator()) {
    $source = Join-Path $patchDir $entry.Key
    $target = $entry.Value
    if (-not (Test-Path $source)) {
        Write-Host "Skip: missing patch $($entry.Key)"
        continue
    }
    Copy-Item -Force $source $target
    Write-Host "Patched $($entry.Key)"
}

Write-Host "Applied Aliyun Gradle mirrors to @react-native/gradle-plugin"
