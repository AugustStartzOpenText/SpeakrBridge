$ErrorActionPreference = "Stop"

$ServiceName = "SpeakrBridge"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonExe = (Get-Command python).Source
$MainScript = Join-Path $ProjectRoot "main.py"

if (-not (Get-Command nssm -ErrorAction SilentlyContinue)) {
  throw "NSSM is required and must be on PATH."
}

nssm install $ServiceName $PythonExe $MainScript
nssm set $ServiceName AppDirectory $ProjectRoot
nssm set $ServiceName AppStdout (Join-Path $ProjectRoot "service-stdout.log")
nssm set $ServiceName AppStderr (Join-Path $ProjectRoot "service-stderr.log")

Write-Host "SpeakrBridge service installed."
Write-Host "Configure the service to run as the interactive user before starting it."

