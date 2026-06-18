$NodeDir = "E:\AIgalgame\tools\node-v20"

if (-not (Test-Path "$NodeDir\node.exe")) {
    Write-Error "Node 20 not found at $NodeDir. Run scripts\download-node20.cjs first."
}

$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
$parts = @()
if ($userPath) {
    $parts = $userPath -split ';' | Where-Object { $_ -and ($_ -ne $NodeDir) }
}
$newPath = @($NodeDir) + $parts
$newPathText = ($newPath -join ';').TrimEnd(';')
[Environment]::SetEnvironmentVariable("Path", $newPathText, "User")
$env:Path = "$NodeDir;" + ($env:Path -split ';' | Where-Object { $_ -and ($_ -ne $NodeDir) } | ForEach-Object { $_ } | Select-Object -Unique) -join ';'

Write-Host "Node path pinned to $NodeDir"
& "$NodeDir\node.exe" -v
& "$NodeDir\npm.cmd" -v
