param(
    [string]$SourceProject = "",
    [switch]$Download
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$app = Join-Path $root "android\app"
$libs = Join-Path $app "libs"
$assets = Join-Path $app "src\main\assets"

New-Item -ItemType Directory -Force -Path $libs | Out-Null
New-Item -ItemType Directory -Force -Path $assets | Out-Null

$sherpaVersion = "1.13.2"
$aarName = "sherpa-onnx-static-link-onnxruntime-$sherpaVersion.aar"
$streamingDirName = "sherpa-onnx-streaming-paraformer-bilingual-zh-en"
$senseDirName = "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17"

function Copy-IfPresent($From, $To) {
    if (-not (Test-Path $From)) {
        return $false
    }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $To) | Out-Null
    Copy-Item -Force -Recurse $From $To
    Write-Host "copied  $To"
    return $true
}

function Download-File($Url, $Path, $MinBytes = 1) {
    if (Test-Path $Path) {
        $existing = Get-Item $Path
        if ($existing.Length -ge $MinBytes) {
            Write-Host "exists  $Path"
            return
        }
        Remove-Item -Force $Path
    }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Path) | Out-Null
    Write-Host "fetch   $Url"
    if (Get-Command curl.exe -ErrorAction SilentlyContinue) {
        & curl.exe -L -f --retry 3 --retry-delay 2 -o $Path $Url
    } else {
        Invoke-WebRequest -Uri $Url -OutFile $Path -UseBasicParsing -MaximumRedirection 10
    }
    $downloaded = Get-Item $Path
    if ($downloaded.Length -lt $MinBytes) {
        throw "Downloaded file is too small: $Path ($($downloaded.Length) bytes)"
    }
}

$copiedAar = $false
$copiedStreaming = $false
$copiedSense = $false

if (-not [string]::IsNullOrWhiteSpace($SourceProject)) {
    $sourceApp = Join-Path $SourceProject "android\app"
    $copiedAar = Copy-IfPresent (Join-Path $sourceApp "libs\$aarName") (Join-Path $libs $aarName)
    $copiedStreaming = Copy-IfPresent (Join-Path $sourceApp "src\main\assets\$streamingDirName") (Join-Path $assets $streamingDirName)
    $copiedSense = Copy-IfPresent (Join-Path $sourceApp "src\main\assets\$senseDirName") (Join-Path $assets $senseDirName)
}

if ($copiedAar -and $copiedStreaming -and $copiedSense) {
    Write-Host "done    Android local ASR assets are ready"
    return
}

if (-not $Download) {
    throw "Could not copy all ASR assets. Pass -SourceProject with an existing setup or re-run with -Download."
}

Download-File `
    "https://downloads.sourceforge.net/project/sherpa-onnx.mirror/v$sherpaVersion/$aarName" `
    (Join-Path $libs $aarName) `
    20000000

$streamingDir = Join-Path $assets $streamingDirName
Download-File "https://huggingface.co/csukuangfj/sherpa-onnx-streaming-paraformer-bilingual-zh-en/resolve/main/encoder.int8.onnx?download=true" (Join-Path $streamingDir "encoder.int8.onnx") 100000000
Download-File "https://huggingface.co/csukuangfj/sherpa-onnx-streaming-paraformer-bilingual-zh-en/resolve/main/decoder.int8.onnx?download=true" (Join-Path $streamingDir "decoder.int8.onnx") 50000000
Download-File "https://huggingface.co/csukuangfj/sherpa-onnx-streaming-paraformer-bilingual-zh-en/resolve/main/tokens.txt?download=true" (Join-Path $streamingDir "tokens.txt") 1000

$senseDir = Join-Path $assets $senseDirName
$senseModel = Join-Path $senseDir "model.int8.onnx"
$senseTokens = Join-Path $senseDir "tokens.txt"
if (-not ((Test-Path $senseModel) -and (Test-Path $senseTokens))) {
    $external = Join-Path $root "tools\external"
    New-Item -ItemType Directory -Force -Path $external | Out-Null
    $archive = Join-Path $external "$senseDirName.tar.bz2"
    Download-File "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/$senseDirName.tar.bz2" $archive 100000000
    tar.exe -xjf $archive -C $assets `
        "$senseDirName/model.int8.onnx" `
        "$senseDirName/tokens.txt"
}

Write-Host "done    Android local ASR assets are ready"
