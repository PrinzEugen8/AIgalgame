param(
    [switch]$SherpaOnly
)

$ErrorActionPreference = "Stop"

$SherpaRuntimeAarName = "sherpa-onnx-static-link-onnxruntime-1.13.2.aar"
$SherpaAssetsAarName = "aigalgame-sherpa-asr-assets.aar"
$SenseVoiceModelDir = "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17"
$KotlinStdlib = "org.jetbrains.kotlin:kotlin-stdlib:1.9.24"

function Patch-SherpaAarManifest {
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

function Resolve-SenseVoiceModelSource {
    param([string]$AiRoot)

    $candidates = @(
        (Join-Path $AiRoot "RelaxRoomAI\Assets\Plugins\Android\$SherpaAssetsAarName"),
        (Join-Path $AiRoot "android\app\src\main\assets\$SenseVoiceModelDir"),
        (Join-Path $AiRoot "RelaxRoomAI\Assets\StreamingAssets\SherpaOnnx\$SenseVoiceModelDir")
    )

    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) {
            return $candidate
        }
    }

    return $null
}

function Build-SherpaAsrAssetsAar {
    param(
        [string]$OutputPath,
        [string]$ModelSource
    )

    $modelFile = "model.int8.onnx"
    $tokensFile = "tokens.txt"
    $tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("sherpa-assets-" + [guid]::NewGuid().ToString("N"))
    $stagingDir = Join-Path $tempRoot "staging"
    $assetsDir = Join-Path $stagingDir ("assets\" + $SenseVoiceModelDir)

    try {
        New-Item -ItemType Directory -Force -Path $assetsDir | Out-Null

        $manifest = @'
<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="com.aigalgame.sherpa.asr.assets" />
'@
        Set-Content -Path (Join-Path $stagingDir "AndroidManifest.xml") -Value $manifest -Encoding UTF8

        if ($ModelSource -like "*.aar") {
            $extractDir = Join-Path $tempRoot "source-aar"
            Copy-Item $ModelSource (Join-Path $tempRoot "source.zip")
            Expand-Archive -Path (Join-Path $tempRoot "source.zip") -DestinationPath $extractDir -Force
            $sourceOnnx = Join-Path $extractDir ("assets\" + $SenseVoiceModelDir + "\" + $modelFile)
            $sourceTokens = Join-Path $extractDir ("assets\" + $SenseVoiceModelDir + "\" + $tokensFile)
            if (-not (Test-Path $sourceOnnx) -or -not (Test-Path $sourceTokens)) {
                throw "Existing assets AAR is missing SenseVoice model files: $ModelSource"
            }
            Copy-Item $sourceOnnx (Join-Path $assetsDir $modelFile)
            Copy-Item $sourceTokens (Join-Path $assetsDir $tokensFile)
        } else {
            $sourceOnnx = Join-Path $ModelSource $modelFile
            $sourceTokens = Join-Path $ModelSource $tokensFile
            if (-not (Test-Path $sourceOnnx) -or -not (Test-Path $sourceTokens)) {
                throw "SenseVoice model files are missing under: $ModelSource"
            }
            Copy-Item $sourceOnnx (Join-Path $assetsDir $modelFile)
            Copy-Item $sourceTokens (Join-Path $assetsDir $tokensFile)
        }

        $zipPath = Join-Path $tempRoot "bundle.zip"
        if (Test-Path $OutputPath) {
            Remove-Item $OutputPath -Force
        }
        Push-Location $stagingDir
        try {
            Compress-Archive -Path * -DestinationPath $zipPath -Force
        } finally {
            Pop-Location
        }

        $outputDir = Split-Path $OutputPath -Parent
        if (-not (Test-Path $outputDir)) {
            New-Item -ItemType Directory -Force -Path $outputDir | Out-Null
        }
        Move-Item -Force $zipPath $OutputPath
        Write-Host "Built Sherpa assets AAR: $OutputPath"
    } finally {
        Remove-Item -Recurse -Force $tempRoot -ErrorAction SilentlyContinue
    }
}

function Test-ShouldRebuildAssetsAar {
    param(
        [string]$OutputPath,
        [string]$ModelSource
    )

    if (-not (Test-Path $OutputPath)) {
        return $true
    }

    $outputTime = (Get-Item $OutputPath).LastWriteTimeUtc
    if ($ModelSource -like "*.aar") {
        return (Get-Item $ModelSource).LastWriteTimeUtc -gt $outputTime
    }

    $modelFile = Join-Path $ModelSource "model.int8.onnx"
    $tokensFile = Join-Path $ModelSource "tokens.txt"
    return ((Get-Item $modelFile).LastWriteTimeUtc -gt $outputTime) -or
        ((Get-Item $tokensFile).LastWriteTimeUtc -gt $outputTime)
}

function Ensure-SherpaLibraries {
    param(
        [string]$AiRoot,
        [string]$AppLibsDir
    )

    if (-not (Test-Path $AppLibsDir)) {
        New-Item -ItemType Directory -Force -Path $AppLibsDir | Out-Null
    }

    $runtimeSource = Join-Path $AiRoot ("RelaxRoomAI\Assets\Plugins\Android\" + $SherpaRuntimeAarName)
    if (-not (Test-Path $runtimeSource)) {
        throw "Sherpa runtime AAR is missing: $runtimeSource"
    }

    $runtimeTarget = Join-Path $AppLibsDir $SherpaRuntimeAarName
    if (-not (Test-Path $runtimeTarget) -or
        (Get-Item $runtimeSource).LastWriteTimeUtc -gt (Get-Item $runtimeTarget).LastWriteTimeUtc) {
        Copy-Item -Force $runtimeSource $runtimeTarget
        Write-Host "Copied Sherpa runtime AAR -> $runtimeTarget"
    } else {
        Write-Host "Sherpa runtime AAR up to date: $runtimeTarget"
    }

    $modelSource = Resolve-SenseVoiceModelSource -AiRoot $AiRoot
    if (-not $modelSource) {
        throw "SenseVoice model source not found. Expected android assets or $SherpaAssetsAarName."
    }

    $assetsTarget = Join-Path $AppLibsDir $SherpaAssetsAarName
    if (Test-ShouldRebuildAssetsAar -OutputPath $assetsTarget -ModelSource $modelSource) {
        Build-SherpaAsrAssetsAar -OutputPath $assetsTarget -ModelSource $modelSource
    } else {
        Write-Host "Sherpa assets AAR up to date: $assetsTarget"
    }
}

function Patch-UnityLibraryGradle {
    param([string]$GradlePath)

    if (-not (Test-Path $GradlePath)) {
        return
    }

    $content = Get-Content $GradlePath -Raw
    $content = $content.Replace('useLegacyPackaging true', 'useLegacyPackaging false')
    $content = [regex]::Replace($content, '(?m)^(\s*)minSdk\s+\d+\s*$', '$1minSdk 24')
    $content = [regex]::Replace($content, '(?m)^(\s*)minSdkVersion\s+\d+\s*$', '$1minSdkVersion 24')
    $content = $content -replace '(?m)^\s*ndkPath\s+".*"\s*\r?\n', ''
    $content = $content -replace "(?m)^\s*implementation fileTree\(dir: 'libs', include: \['\*\.aar'\]\)\s*\r?\n", ''
    $content = $content -replace "(?m)^\s*implementation\(name: 'sherpa-onnx-static-link-onnxruntime-[^']+', ext:'aar'\)\s*\r?\n", ''
    $content = $content -replace "(?m)^\s*implementation\(name: `"sherpa-onnx-static-link-onnxruntime-[^`"]+`", ext:`"aar`"\)\s*\r?\n", ''
    $content = $content -replace "(?m)^\s*implementation 'org\.jetbrains\.kotlin:kotlin-stdlib:[^']+'\s*\r?\n", ''
    [System.IO.File]::WriteAllText($GradlePath, $content.TrimStart([char]0xFEFF))
    Write-Host "Patched unityLibrary/build.gradle for RN embedding"
}

function Patch-AppGradle {
    param([string]$GradlePath)

    if (-not (Test-Path $GradlePath)) {
        throw "App build.gradle not found: $GradlePath"
    }

    $content = Get-Content $GradlePath -Raw
    $content = $content -replace "(?m)^\s*implementation files\('.*$([regex]::Escape($SherpaRuntimeAarName))'\)\s*\r?\n", ''
    $content = $content -replace "(?m)^\s*implementation files\('.*$([regex]::Escape($SherpaAssetsAarName))'\)\s*\r?\n", ''
    $content = $content -replace "(?m)^\s*implementation fileTree\(dir: 'libs', include: \['\*\.aar'\]\)\s*\r?\n", ''
    $content = $content -replace "(?m)^\s*implementation '$KotlinStdlib'\s*\r?\n", ''

    if ($content -notmatch "fileTree\(dir: 'libs', include: \['\*\.aar'\]\)") {
        $marker = "    def unityLibraryDir = file(""../../unity/builds/android/unityLibrary"")"
        $insert = @"

    implementation fileTree(dir: 'libs', include: ['*.aar'])
    implementation '$KotlinStdlib'
"@
        if ($content -match [regex]::Escape($marker)) {
            $content = $content.Replace($marker, $marker + $insert)
        } else {
            $content = $content -replace "(dependencies \{[\r\n]+)",
                "`$1$insert`r`n"
        }
    }

    [System.IO.File]::WriteAllText($GradlePath, $content.TrimStart([char]0xFEFF))
    Write-Host "Patched app/build.gradle for Sherpa AAR dependencies"
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
        'android:extractNativeLibs="true"',
        'android:extractNativeLibs="false"')
    $manifest = $manifest.Replace(
        'android:name="unity.splash-enable" android:value="True"',
        'android:name="unity.splash-enable" android:value="False"')
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
$appLibsDir = Join-Path $repoRoot "android\app\libs"
$appGradle = Join-Path $repoRoot "android\app\build.gradle"

Ensure-SherpaLibraries -AiRoot $aiRoot -AppLibsDir $appLibsDir
Patch-AppGradle -GradlePath $appGradle

$unityGradle = Join-Path $repoRoot "unity\builds\android\unityLibrary\build.gradle"
Patch-UnityLibraryGradle $unityGradle

if ($SherpaOnly) {
    return
}

$unityManifest = Join-Path $repoRoot "unity\builds\android\unityLibrary\src\main\AndroidManifest.xml"
$unityBootConfig = Join-Path $repoRoot "unity\builds\android\unityLibrary\src\main\assets\bin\Data\boot.config"
$unityGradle = Join-Path $repoRoot "unity\builds\android\unityLibrary\build.gradle"

Patch-UnityLibraryManifest $unityManifest
Patch-UnityLibraryBootConfig $unityBootConfig
Patch-UnityLibraryGradle $unityGradle
