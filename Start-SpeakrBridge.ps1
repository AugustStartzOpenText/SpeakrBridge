[CmdletBinding()]
param(
    [string]$SpeakrPath = (Join-Path $PSScriptRoot "..\Speakr"),
    [string]$SpeakrUrl = "http://127.0.0.1:7000",
    [string]$BridgeUrl = "http://127.0.0.1:8080/scoping",
    [string]$QueueUrl = "http://127.0.0.1:8080/queue"
)

$ErrorActionPreference = "Stop"

$projectRoot = $PSScriptRoot
$speakrRoot = [System.IO.Path]::GetFullPath($SpeakrPath)

if (-not (Test-Path -LiteralPath $speakrRoot -PathType Container)) {
    throw "Speakr folder not found: $speakrRoot. Pass its location with -SpeakrPath."
}

$composeFile = @("compose.yml", "compose.yaml", "docker-compose.yml", "docker-compose.yaml") |
    ForEach-Object { Join-Path $speakrRoot $_ } |
    Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } |
    Select-Object -First 1

if (-not $composeFile) {
    throw "No Docker Compose file was found in $speakrRoot."
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker was not found on PATH. Start Docker Desktop and try again."
}

$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
$pythonExe = if (Test-Path -LiteralPath $venvPython -PathType Leaf) {
    $venvPython
} else {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if (-not $pythonCommand) {
        throw "Python was not found. Create .venv or add Python to PATH."
    }
    $pythonCommand.Source
}

$edgeCommand = Get-Command msedge.exe -ErrorAction SilentlyContinue
$edgeExe = if ($edgeCommand) {
    $edgeCommand.Source
} else {
    @(
        (Join-Path ${env:ProgramFiles(x86)} "Microsoft\Edge\Application\msedge.exe"),
        (Join-Path $env:ProgramFiles "Microsoft\Edge\Application\msedge.exe")
    ) | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
}

if (-not $edgeExe) {
    throw "Microsoft Edge was not found."
}

Write-Host "Starting Speakr containers..."
& docker compose --file $composeFile up --detach
if ($LASTEXITCODE -ne 0) {
    throw "Speakr failed to start (docker compose exit code $LASTEXITCODE)."
}

$bridgeCommand = "Set-Location -LiteralPath '$($projectRoot.Replace("'", "''"))'; & '$($pythonExe.Replace("'", "''"))' '.\main.py'"
Start-Process powershell.exe -ArgumentList @(
    "-NoExit",
    "-ExecutionPolicy", "Bypass",
    "-Command", $bridgeCommand
)

Write-Host "Opening Speakr, the queue, and the bridge in Edge..."
Start-Process -FilePath $edgeExe -ArgumentList @(
    "--new-window",
    $SpeakrUrl,
    $QueueUrl,
    $BridgeUrl
)

Write-Host "Speakr and SpeakrBridge have been launched."
