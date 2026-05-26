param(
    [string]$TokenHubBaseUrl = "https://tokenhub.tencentmaas.com/plan/v3/chat/completions",
    [string]$TokenHubModel = "glm-5.1",
    [string]$ProxyHost = "127.0.0.1",
    [string]$ProxyPort = "8787",
    [string]$ProxyRequestTimeoutSeconds = "600",
    [switch]$SkipProbe,
    [switch]$NoPersistCodexKey,
    [switch]$ForceInstall
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $scriptDir
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
$configureScript = Join-Path $scriptDir "configure_env.ps1"
$probeScript = Join-Path $scriptDir "probe_tokenhub.py"
$requirementsPath = Join-Path $projectRoot "requirements.txt"

function Resolve-PythonCommand {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        return $python.Source
    }

    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        return $py.Source
    }

    throw "Python was not found. Install Python 3.11+ and ensure 'python' or 'py' is available in PATH."
}

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )

    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code $LASTEXITCODE`: $FilePath $($Arguments -join ' ')"
    }
}

Set-Location $projectRoot

Write-Host "TokenHub Responses proxy bootstrap"
Write-Host "Project root: $projectRoot"
Write-Host ""

if (-not (Test-Path $pythonPath)) {
    Write-Host "Creating virtual environment..."
    $pythonCommand = Resolve-PythonCommand
    Invoke-Checked -FilePath $pythonCommand -Arguments @("-m", "venv", ".venv")
}
else {
    Write-Host "Virtual environment already exists."
}

if ($ForceInstall -or -not (Test-Path (Join-Path $projectRoot ".venv\Lib\site-packages\httpx"))) {
    Write-Host "Installing dependencies..."
    Invoke-Checked -FilePath $pythonPath -Arguments @("-m", "pip", "install", "-r", $requirementsPath)
}
else {
    Write-Host "Dependencies look installed. Use -ForceInstall to reinstall."
}

Write-Host ""
Write-Host "Configuring environment variables..."
$configureArgs = @{
    Scope = "Process"
    TokenHubBaseUrl = $TokenHubBaseUrl
    TokenHubModel = $TokenHubModel
    ProxyHost = $ProxyHost
    ProxyPort = $ProxyPort
    ProxyRequestTimeoutSeconds = $ProxyRequestTimeoutSeconds
    EnableToolCalls = "false"
}
if (-not $NoPersistCodexKey) {
    $configureArgs.PersistCodexKey = $true
}
& $configureScript @configureArgs

if (-not $env:TOKENHUB_API_KEY) {
    throw "TOKENHUB_API_KEY is missing after environment configuration."
}
if (-not $env:CODEX_GLM_PROXY_KEY) {
    throw "CODEX_GLM_PROXY_KEY is missing after environment configuration."
}

$toolCallsEnabled = $false
if ($SkipProbe) {
    Write-Host ""
    Write-Host "Skipping TokenHub probe. ENABLE_TOOL_CALLS remains false."
}
else {
    Write-Host ""
    Write-Host "Probing TokenHub compatibility..."
    $probeOutput = & $pythonPath $probeScript 2>&1
    $probeExitCode = $LASTEXITCODE
    $probeOutput | ForEach-Object { Write-Host $_ }

    if ($probeExitCode -ne 0) {
        throw "TokenHub probe failed. Fix TOKENHUB_API_KEY, TOKENHUB_BASE_URL, or TOKENHUB_MODEL before starting the proxy."
    }

    $toolCallsEnabled = [bool](
        ($probeOutput | Select-String -SimpleMatch "non_stream_tool_calls: PASS") -and
        ($probeOutput | Select-String -SimpleMatch "stream_tool_calls: PASS")
    )

    if ($toolCallsEnabled) {
        $env:ENABLE_TOOL_CALLS = "true"
    }
    else {
        $env:ENABLE_TOOL_CALLS = "false"
        Write-Host ""
        Write-Host "Tool-call probe did not pass. Starting in text-only mode."
    }
}

Write-Host ""
Write-Host "Starting proxy..."
Write-Host "Health URL: http://$ProxyHost`:$ProxyPort/health"
Write-Host "Responses base URL for Codex: http://$ProxyHost`:$ProxyPort/v1"
Write-Host "Tool calls enabled: $env:ENABLE_TOOL_CALLS"
Write-Host ""
Write-Host "Keep this PowerShell window open. Press Ctrl+C to stop the proxy."
Write-Host ""

& $pythonPath -m uvicorn proxy_app.main:app --host $ProxyHost --port $ProxyPort
