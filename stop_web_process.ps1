$ErrorActionPreference = "SilentlyContinue"

$repo = Split-Path -Parent $MyInvocation.MyCommand.Path
$runner = [regex]::Escape((Join-Path $repo "run_web_process.ps1"))
$matches = Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -and (
        $_.CommandLine -match "waitress.*app:app" -or
        $_.CommandLine -match $runner
    )
}

foreach ($process in $matches) {
    Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
}
