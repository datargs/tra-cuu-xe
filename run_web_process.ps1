$ErrorActionPreference = "Stop"

$repo = Split-Path -Parent $MyInvocation.MyCommand.Path
$runtimeDir = Join-Path $repo "runtime"
$pidPath = Join-Path $runtimeDir "web.pid"
$logDir = Join-Path $repo "logs"
$logPath = Join-Path $logDir "web-out.log"
$python = Join-Path $repo "venv\Scripts\python.exe"

New-Item -ItemType Directory -Force -Path $runtimeDir, $logDir | Out-Null
if (-not (Test-Path $python)) {
    throw "Virtual environment Python was not found at $python"
}

Set-Content -LiteralPath $pidPath -Value $PID -Encoding ASCII
try {
    & $python -m waitress --listen=127.0.0.1:8501 --threads=8 app:app >> $logPath 2>&1
    exit $LASTEXITCODE
} finally {
    if (Test-Path $pidPath) {
        Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
    }
}
