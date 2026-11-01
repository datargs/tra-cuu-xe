param(
    [switch]$Force
)
$ErrorActionPreference = "Stop"

$repo = Split-Path -Parent $MyInvocation.MyCommand.Path
$logDir = Join-Path $repo "logs"
$logPath = Join-Path $logDir "deploy_server.log"
$python = Join-Path $repo "venv\Scripts\python.exe"
$startScript = Join-Path $repo "start_web_background.bat"
$runtimeDir = Join-Path $repo "runtime"

New-Item -ItemType Directory -Force -Path $logDir | Out-Null
New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null

function Write-DeployLog {
    param([string]$Message)
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Add-Content -LiteralPath $logPath -Value $line -Encoding UTF8
}

function Invoke-Git {
    param([Parameter(Mandatory)][string[]]$Arguments)
    $previousErrorAction = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $output = & $script:git @Arguments 2>&1
    $exitCode = $LASTEXITCODE
    $ErrorActionPreference = $previousErrorAction
    foreach ($line in $output) {
        Write-DeployLog ("git: " + [string]$line)
    }
    if ($exitCode -ne 0) {
        throw "git command failed with exit code $exitCode"
    }
    return @($output)
}

$gitCommand = Get-Command git.exe -ErrorAction SilentlyContinue
if ($gitCommand) {
    $git = $gitCommand.Source
} elseif (Test-Path "C:\Program Files\Git\cmd\git.exe") {
    $git = "C:\Program Files\Git\cmd\git.exe"
} else {
    throw "Git was not found."
}

$mutex = New-Object System.Threading.Mutex($false, "Global\TraCuuXeAutoDeploy")
if (-not $mutex.WaitOne(0)) {
    exit 0
}

try {
    Set-Location $repo
    Write-DeployLog "Checking origin/main in $repo"

    $trackedChanges = @(& $git -c core.quotepath=false status --porcelain=v1 --untracked-files=no)
    if ($trackedChanges.Count -gt 0) {
        Write-DeployLog "Skipped: tracked local changes are present."
        $trackedChanges | ForEach-Object { Write-DeployLog ([string]$_) }
        exit 2
    }

    Invoke-Git @("fetch", "origin", "main", "--quiet") | Out-Null
    $localHead = ((& $git rev-parse HEAD 2>$null) | Select-Object -First 1).Trim()
    $remoteHead = ((& $git rev-parse origin/main 2>$null) | Select-Object -First 1).Trim()
    if (-not $localHead -or -not $remoteHead) {
        throw "Unable to resolve local or remote HEAD."
    }
    if ($localHead -eq $remoteHead -and -not $Force) {
        Write-DeployLog "No update needed at $localHead"
        exit 0
    }

    $changedFiles = @(& $git diff --name-only $localHead $remoteHead -- requirements.txt 2>$null)
    Invoke-Git @("pull", "--ff-only", "origin", "main") | Out-Null
    $newHead = ((& $git rev-parse HEAD 2>$null) | Select-Object -First 1).Trim()
    Write-DeployLog "Updated $localHead to $newHead"

    if ($changedFiles.Count -gt 0) {
        if (-not (Test-Path $python)) {
            throw "Virtual environment Python was not found at $python"
        }
        Write-DeployLog "requirements.txt changed; installing dependencies"
        $pipOutput = & $python -m pip install -r (Join-Path $repo "requirements.txt") 2>&1
        $pipExit = $LASTEXITCODE
        $pipOutput | ForEach-Object { Write-DeployLog ("pip: " + [string]$_) }
        if ($pipExit -ne 0) {
            throw "Dependency installation failed with exit code $pipExit"
        }
    }

    if (-not (Test-Path $startScript)) {
        throw "Start script was not found at $startScript"
    }
    Write-DeployLog "Restarting web and tunnel"
    Start-Process -FilePath $env:ComSpec `
        -ArgumentList @("/d", "/c", "`"$startScript`"") `
        -WorkingDirectory $repo `
        -WindowStyle Hidden | Out-Null

    Start-Sleep -Seconds 5
    try {
        $health = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:8501/healthz" -TimeoutSec 10
        Write-DeployLog "Health check returned HTTP $($health.StatusCode)"
    } catch {
        Write-DeployLog "Health check failed: $($_.Exception.Message)"
        throw
    }
} catch {
    Write-DeployLog "Deployment failed: $($_.Exception.Message)"
    exit 1
} finally {
    $mutex.ReleaseMutex()
    $mutex.Dispose()
}
