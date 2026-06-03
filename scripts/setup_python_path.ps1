param(
    [string]$PythonBase = "",
    [switch]$AutoDetect
)

$ErrorActionPreference = "Stop"

if ($AutoDetect -or -not $PythonBase) {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if (-not $pythonCommand) {
        $pythonCommand = Get-Command py -ErrorAction SilentlyContinue
    }
    if (-not $pythonCommand) {
        throw "Cannot find Python in PATH. Pass -PythonBase with the directory that contains python.exe."
    }
    $PythonBase = Split-Path -Parent $pythonCommand.Source
    if ((Split-Path -Leaf $PythonBase) -eq "Scripts") {
        $PythonBase = Split-Path -Parent (Split-Path -Parent $PythonBase)
    }
}

$PythonScripts = Join-Path $PythonBase "Scripts"

if (-not (Test-Path (Join-Path $PythonBase "python.exe"))) {
    throw "python.exe not found at $PythonBase. Please verify the path."
}

$currentPath = [Environment]::GetEnvironmentVariable("Path", "User")
$entries = $currentPath -split ";" | Where-Object { $_ -ne "" }

$alreadyHasBase = $false
$alreadyHasScripts = $false
foreach ($entry in $entries) {
    if ($entry -ieq $PythonBase) {
        $alreadyHasBase = $true
    }
    if ($entry -ieq $PythonScripts) {
        $alreadyHasScripts = $true
    }
}

if ($alreadyHasBase -and $alreadyHasScripts) {
    Write-Host "[OK] Both directories are already in user PATH." -ForegroundColor Green
}
else {
    if (-not $alreadyHasBase) {
        $entries = @($PythonBase) + $entries
        Write-Host "[+] Added $PythonBase" -ForegroundColor Cyan
    }
    if (-not $alreadyHasScripts) {
        $idx = [Array]::IndexOf($entries, $PythonBase)
        if ($idx -ge 0 -and $idx -lt ($entries.Length - 1)) {
            $entries = $entries[0..$idx] + @($PythonScripts) + $entries[($idx + 1)..($entries.Length - 1)]
        }
        elseif ($idx -ge 0) {
            $entries = $entries + @($PythonScripts)
        }
        else {
            $entries = @($PythonScripts) + $entries
        }
        Write-Host "[+] Added $PythonScripts" -ForegroundColor Cyan
    }
    [Environment]::SetEnvironmentVariable("Path", ($entries -join ";"), "User")
    Write-Host "[OK] User PATH updated." -ForegroundColor Green
}

$env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")

Write-Host ""
Write-Host "Verification:" -ForegroundColor Yellow
$py = Get-Command python -ErrorAction SilentlyContinue
if ($py) {
    Write-Host "  python => $($py.Source)" -ForegroundColor Green
    & python --version
}
else {
    Write-Host "  python not found in current session. Open a new window and run: python --version" -ForegroundColor Red
}

$pip = Get-Command pip -ErrorAction SilentlyContinue
if ($pip) {
    Write-Host "  pip    => $($pip.Source)" -ForegroundColor Green
    & pip --version
}
else {
    Write-Host "  pip not found yet (may appear after first venv creation)." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Done. If verification failed, open a new PowerShell window." -ForegroundColor Yellow
