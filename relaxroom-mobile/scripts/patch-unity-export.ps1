$ErrorActionPreference = "Stop"

function Patch-SherpaAar {
    param([string]$AarPath)

    if (-not (Test-Path $AarPath)) {
        Write-Host "Skip missing AAR: $AarPath"
        return
    }

    $tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("sherpa-patch-" + [guid]::NewGuid().ToString("N"))
    $extractDir = Join-Path $tempRoot "extracted"
    New-Item -ItemType Directory -Force -Path $extractDir | Out-Null

    try {
        Copy-Item $AarPath (Join-Path $tempRoot "bundle.zip")
        Expand-Archive -Path (Join-Path $tempRoot "bundle.zip") -DestinationPath $extractDir -Force

        $manifestPath = Join-Path $extractDir "AndroidManifest.xml"
        if (-not (Test-Path $manifestPath)) {
            Write-Host "No manifest in $AarPath"
            return
        }

        $manifest = Get-Content $manifestPath -Raw
        if ($manifest -match 'package=') {
            Write-Host "Already patched: $AarPath"
            return
        }

        $manifest = $manifest -replace '<manifest xmlns:android="http://schemas.android.com/apk/res/android">',
            '<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="com.aigalgame.sherpa.asr.assets">'
        Set-Content -Path $manifestPath -Value $manifest -Encoding UTF8

        if (Test-Path $AarPath) {
            Remove-Item $AarPath -Force
        }

        Push-Location $extractDir
        try {
            $zipPath = Join-Path $tempRoot "bundle.zip"
            if (Test-Path $zipPath) {
                Remove-Item $zipPath -Force
            }
            Compress-Archive -Path * -DestinationPath $zipPath -Force
            Move-Item -Force $zipPath $AarPath
        } finally {
            Pop-Location
        }

        Write-Host "Patched AAR manifest: $AarPath"
    } finally {
        Remove-Item -Recurse -Force $tempRoot -ErrorAction SilentlyContinue
    }
}

function Patch-UnityLibraryGradle {
    param([string]$GradlePath)

    if (-not (Test-Path $GradlePath)) {
        return
    }

        $content = Get-Content $GradlePath -Raw
        $content = $content -replace '(?m)^\s*ndkPath\s+".*"\s*\r?\n', ''
        [System.IO.File]::WriteAllText($GradlePath, $content.TrimStart([char]0xFEFF))
    Write-Host "Removed hardcoded ndkPath from unityLibrary/build.gradle"
}

function Patch-UnityLibraryManifest {
    param([string]$ManifestPath)

    if (-not (Test-Path $ManifestPath)) {
        Write-Host "Skip missing manifest: $ManifestPath"
        return
    }

    $manifest = Get-Content $ManifestPath -Raw
    $manifest = [regex]::Replace(
        $manifest,
        '<intent-filter>[\s\S]*?android\.intent\.category\.LAUNCHER[\s\S]*?</intent-filter>',
        '')
    $manifest = $manifest.Replace(
        'android:name="unity.launch-fullscreen" android:value="True"',
        'android:name="unity.launch-fullscreen" android:value="False"')
    $manifest = $manifest.Replace(
        'android:hardwareAccelerated="false"',
        'android:hardwareAccelerated="true"')
    [System.IO.File]::WriteAllText($ManifestPath, $manifest)
    Write-Host "Patched unityLibrary AndroidManifest.xml for RN embedding"
}

function Patch-UnityLibraryBootConfig {
    param([string]$BootConfigPath)

    if (-not (Test-Path $BootConfigPath)) {
        Write-Host "Skip missing boot.config: $BootConfigPath"
        return
    }

    $bootConfig = Get-Content $BootConfigPath -Raw
    $bootConfig = $bootConfig.Replace('androidStartInFullscreen=1', 'androidStartInFullscreen=0')
    [System.IO.File]::WriteAllText($BootConfigPath, $bootConfig)
    Write-Host "Patched unityLibrary boot.config androidStartInFullscreen=0"
}

$repoRoot = Split-Path $PSScriptRoot -Parent
$aiRoot = Split-Path $repoRoot -Parent
$sourceAar = Join-Path $aiRoot "RelaxRoomAI\Assets\Plugins\Android\aigalgame-sherpa-asr-assets.aar"
$exportAar = Join-Path $repoRoot "unity\builds\android\unityLibrary\libs\aigalgame-sherpa-asr-assets.aar"
$unityGradle = Join-Path $repoRoot "unity\builds\android\unityLibrary\build.gradle"
$unityManifest = Join-Path $repoRoot "unity\builds\android\unityLibrary\src\main\AndroidManifest.xml"
$unityBootConfig = Join-Path $repoRoot "unity\builds\android\unityLibrary\src\main\assets\bin\Data\boot.config"

if (Test-Path $sourceAar) {
    Patch-SherpaAar $sourceAar
}
Patch-SherpaAar $exportAar
Patch-UnityLibraryGradle $unityGradle
Patch-UnityLibraryManifest $unityManifest
Patch-UnityLibraryBootConfig $unityBootConfig
