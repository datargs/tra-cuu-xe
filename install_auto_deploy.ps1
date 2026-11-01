$ErrorActionPreference = "Stop"

$repo = Split-Path -Parent $MyInvocation.MyCommand.Path
$taskName = "TraCuuXeAutoDeploy"
$scriptPath = Join-Path $repo "deploy_server.ps1"

if (-not (Test-Path $scriptPath)) {
    throw "Deployment script was not found at $scriptPath"
}

$taskRun = "PowerShell.exe -NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`""
& schtasks.exe /Create /F /TN $taskName /SC MINUTE /MO 2 /RU SYSTEM /RL HIGHEST /TR $taskRun | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Unable to register scheduled task $taskName"
}

& schtasks.exe /Run /TN $taskName | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Unable to start scheduled task $taskName"
}
& PowerShell.exe -NoProfile -ExecutionPolicy Bypass -File $scriptPath -Force
Write-Output "Installed and started scheduled task: $taskName"
Write-Output "Repository: $repo"
