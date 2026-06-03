param(
    [string]$NewPythonHome = "",
    [switch]$AutoDetect
)

$ErrorActionPreference = "Stop"

# Determine new Python home
if ($AutoDetect -or -not $NewPythonHome) {
    $py = Get-Command python -ErrorAction SilentlyContinue
    if (-not $py) {
        $py = Get-Command py -ErrorAction SilentlyContinue
    }
    if (-not $py) {
        throw "Cannot find Python in PATH. Run: .\scripts\setup_python_path.ps1 first, or pass -NewPythonHome explicitly."
    }
    $candidate = Split-Path -Parent $py.Source
    if ((Split-Path -Leaf $candidate) -eq "Scripts") {
        $candidate = Split-Path -Parent (Split-Path -Parent $candidate)
    }
    if (Test-Path (Join-Path $candidate "python.exe")) {
        $NewPythonHome = $candidate
    } else {
        throw "Detected python at '$($py.Source)' but cannot determine Python home directory. Pass -NewPythonHome explicitly."
    }
}

$pythonExe = Join-Path $NewPythonHome "python.exe"
if (-not (Test-Path $pythonExe)) {
    throw "python.exe not found at $NewPythonHome. Aborting."
}

$version = & $pythonExe -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
Write-Host "Found Python $version at $NewPythonHome" -ForegroundColor Cyan

# Locate project root
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $scriptDir
$venvDir = Join-Path $projectRoot ".venv"
$pyvenvCfg = Join-Path $venvDir "pyvenv.cfg"

if (-not (Test-Path $pyvenvCfg)) {
    throw "pyvenv.cfg not found at $pyvenvCfg. Is .venv present?"
}

# Fix pyvenv.cfg
Write-Host ""
Write-Host "[1/4] Fixing pyvenv.cfg..." -ForegroundColor Yellow

$cfgLines = Get-Content $pyvenvCfg
$cfgLines = $cfgLines | ForEach-Object {
    if ($_ -match '^home\s*=') { "home = $NewPythonHome" }
    elseif ($_ -match '^executable\s*=') { "executable = $pythonExe" }
    elseif ($_ -match '^command\s*=') { "command = $pythonExe -m venv $venvDir" }
    else { $_ }
}
Set-Content -Path $pyvenvCfg -Value $cfgLines -Encoding UTF8
Write-Host "  home       => $NewPythonHome" -ForegroundColor Green
Write-Host "  executable => $pythonExe" -ForegroundColor Green
Write-Host "  command    => $pythonExe -m venv $venvDir" -ForegroundColor Green

# Fix activate.bat
Write-Host ""
Write-Host "[2/4] Fixing activate.bat..." -ForegroundColor Yellow

$activateBat = Join-Path $venvDir "Scripts\activate.bat"
if (Test-Path $activateBat) {
    $batContent = Get-Content $activateBat -Raw
    $batContent = $batContent -replace 'set "VIRTUAL_ENV=.*"', "set ""VIRTUAL_ENV=$venvDir"""
    Set-Content -Path $activateBat -Value $batContent -Encoding ASCII
    Write-Host "  VIRTUAL_ENV => $venvDir" -ForegroundColor Green
}
else {
    Write-Host "  activate.bat not found, skipping." -ForegroundColor DarkGray
}

# Fix Activate.ps1
Write-Host ""
Write-Host "[3/4] Fixing Activate.ps1..." -ForegroundColor Yellow

$activatePs1 = Join-Path $venvDir "Scripts\Activate.ps1"
if (Test-Path $activatePs1) {
    $ps1Content = Get-Content $activatePs1 -Raw
    $ps1Content = $ps1Content -replace '\$VenvDir\s*=\s*"[^"]*"', "`$VenvDir = `"$venvDir`""
    Set-Content -Path $activatePs1 -Value $ps1Content -Encoding UTF8
    Write-Host "  VenvDir => $venvDir" -ForegroundColor Green
}
else {
    Write-Host "  Activate.ps1 not found, skipping." -ForegroundColor DarkGray
}

# Purge __pycache__ directories
Write-Host ""
Write-Host "[4/4] Purging __pycache__ directories..." -ForegroundColor Yellow

$pycDirs = Get-ChildItem -Path $venvDir -Recurse -Directory -Filter "__pycache__" -Force
if ($pycDirs.Count -gt 0) {
    $pycDirs | Remove-Item -Recurse -Force
    Write-Host "  Removed $($pycDirs.Count) __pycache__ directories" -ForegroundColor Green
}
else {
    Write-Host "  No __pycache__ found" -ForegroundColor DarkGray
}

# Verify
Write-Host ""
Write-Host "Verifying..." -ForegroundColor Yellow

$venvPython = Join-Path $venvDir "Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    throw ".venv\Scripts\python.exe not found after fix."
}

$testResult = & $venvPython -c "import sys; print(sys.executable)" 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host "  python.exe => $testResult" -ForegroundColor Green
    & $venvPython --version
}
else {
    Write-Host "  FAILED: $testResult" -ForegroundColor Red
    Write-Host "  Try: Remove-Item -Recurse -Force .venv; .\scripts\start_proxy.ps1" -ForegroundColor Yellow
    exit 1
}

$importCheck = & $venvPython -c "import httpx, fastapi, uvicorn; print('Core deps OK')" 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host "  $importCheck" -ForegroundColor Green
}
else {
    Write-Host "  Import check failed: $importCheck" -ForegroundColor Red
    Write-Host "  Try: .\.venv\Scripts\python -m pip install -r requirements.txt" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Done! .venv is now portable." -ForegroundColor Green
