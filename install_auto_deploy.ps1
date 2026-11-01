$ErrorActionPreference = "Stop"

$repo = Split-Path -Parent $MyInvocation.MyCommand.Path
$taskName = "TraCuuXeAutoDeploy"
$scriptPath = Join-Path $repo "deploy_server.ps1"

if (-not (Test-Path $scriptPath)) {
    throw "Deployment script was not found at $scriptPath"
}

$action = New-ScheduledTaskAction `
    -Execute "PowerShell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`""
$trigger = New-ScheduledTaskTrigger `
    -Once `
    -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 2) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Highest

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Description "Pull TraCuuXe main and restart web when a new commit is available" `
    -Force | Out-Null

Start-ScheduledTask -TaskName $taskName
& PowerShell.exe -NoProfile -ExecutionPolicy Bypass -File $scriptPath -Force
Write-Output "Installed and started scheduled task: $taskName"
Write-Output "Repository: $repo"
