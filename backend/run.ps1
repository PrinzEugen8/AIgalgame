$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
if (!(Test-Path ".venv")) {
    python -m venv .venv
}
.\.venv\Scripts\python -m pip install -r requirements.txt
$hostValue = if ($env:AIGALGAME_HOST) { $env:AIGALGAME_HOST } else { "0.0.0.0" }
$portValue = if ($env:AIGALGAME_PORT) { $env:AIGALGAME_PORT } else { "8899" }
$logLevel = if ($env:AIGALGAME_LOG_LEVEL) { $env:AIGALGAME_LOG_LEVEL.ToLowerInvariant() } else { "info" }
.\.venv\Scripts\python -m uvicorn app.main:app --host $hostValue --port $portValue --reload --log-level $logLevel
