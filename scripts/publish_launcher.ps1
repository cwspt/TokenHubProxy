param(
    [string]$Configuration = "Release",
    [string]$Runtime = "win-x64",
    [string]$OutputDir = "",
    [switch]$SelfContained,
    [switch]$NoSingleFile,
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $scriptDir
$launcherProject = Join-Path $projectRoot "tools\TokenHubProxyLauncher\TokenHubProxyLauncher.csproj"

if (-not $OutputDir) {
    $OutputDir = Join-Path $projectRoot "dist\TokenHubResponsesProxyLauncher"
}
$OutputDir = [IO.Path]::GetFullPath($OutputDir)

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [string]$WorkingDirectory = $projectRoot
    )

    Push-Location $WorkingDirectory
    try {
        & $FilePath @Arguments
        if ($LASTEXITCODE -ne 0) {
            throw "Command failed with exit code $LASTEXITCODE`: $FilePath $($Arguments -join ' ')"
        }
    }
    finally {
        Pop-Location
    }
}

function Copy-DirectoryClean {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination
    )

    if (Test-Path $Destination) {
        Remove-Item -LiteralPath $Destination -Recurse -Force
    }
    New-Item -ItemType Directory -Path $Destination -Force | Out-Null

    Get-ChildItem -LiteralPath $Source -Force | ForEach-Object {
        if ($_.PSIsContainer -and $_.Name -eq "__pycache__") {
            return
        }
        if (-not $_.PSIsContainer -and ($_.Extension -eq ".pyc" -or $_.Extension -eq ".pyo")) {
            return
        }
        Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $Destination $_.Name) -Recurse -Force
    }
}

function Copy-FileIfExists {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination
    )

    if (Test-Path $Source) {
        Copy-Item -LiteralPath $Source -Destination $Destination -Force
    }
}

function Remove-DirectoryForPublish {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path $Path)) {
        return
    }

    try {
        Remove-Item -LiteralPath $Path -Recurse -Force
    }
    catch {
        throw "Could not clean publish output directory '$Path'. Close any running launcher/proxy from that directory, then retry. Original error: $($_.Exception.Message)"
    }
}

Write-Host "Publishing TokenHub proxy launcher"
Write-Host "Project root: $projectRoot"
Write-Host "Output dir:   $OutputDir"
Write-Host ""

if (-not $SkipTests) {
    $venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
    if (Test-Path $venvPython) {
        Write-Host "Running Python tests..."
        Invoke-Checked -FilePath $venvPython -Arguments @("-m", "unittest", "discover", "-s", "tests", "-v")

        Write-Host "Running Python compileall..."
        Invoke-Checked -FilePath $venvPython -Arguments @("-m", "compileall", "proxy_app", "scripts", "tests")
    }
    else {
        Write-Host "Skipping Python tests because .venv\Scripts\python.exe was not found."
    }
}

Write-Host "Building WPF launcher..."
Invoke-Checked -FilePath "dotnet" -Arguments @("build", $launcherProject, "-c", $Configuration)

Remove-DirectoryForPublish -Path $OutputDir
New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null

$publishArgs = @(
    "publish",
    $launcherProject,
    "-c", $Configuration,
    "-r", $Runtime,
    "-o", $OutputDir,
    "--self-contained", $SelfContained.ToString().ToLowerInvariant()
)

if (-not $NoSingleFile) {
    $publishArgs += "-p:PublishSingleFile=true"
}

Write-Host "Publishing WPF launcher..."
Invoke-Checked -FilePath "dotnet" -Arguments $publishArgs

Write-Host "Copying proxy runtime files..."
Copy-DirectoryClean -Source (Join-Path $projectRoot "proxy_app") -Destination (Join-Path $OutputDir "proxy_app")
Copy-DirectoryClean -Source (Join-Path $projectRoot "scripts") -Destination (Join-Path $OutputDir "scripts")
Copy-FileIfExists -Source (Join-Path $projectRoot "requirements.txt") -Destination (Join-Path $OutputDir "requirements.txt")
Copy-FileIfExists -Source (Join-Path $projectRoot "README.md") -Destination (Join-Path $OutputDir "README.md")
Copy-FileIfExists -Source (Join-Path $projectRoot "AGENTS.md") -Destination (Join-Path $OutputDir "AGENTS.md")

if (Test-Path (Join-Path $projectRoot "docs")) {
    Copy-DirectoryClean -Source (Join-Path $projectRoot "docs") -Destination (Join-Path $OutputDir "docs")
}

$runScript = @'
@echo off
setlocal
cd /d "%~dp0"
TokenHubProxyLauncher.exe
'@
Set-Content -Path (Join-Path $OutputDir "RunLauncher.cmd") -Value $runScript -Encoding ASCII

$notes = @"
TokenHub Responses Proxy Launcher

Run:
  RunLauncher.cmd

Requirements on the target machine:
  - Windows
  - Python 3.11+ available as python in PATH
  - .NET 8 Desktop Runtime, unless published with -SelfContained
  - Network access to install pip dependencies and reach TokenHub

This package includes:
  - TokenHubProxyLauncher.exe
  - proxy_app\
  - scripts\
  - requirements.txt
  - README.md

TOKENHUB_API_KEY is not stored by the launcher. Enter it in the UI when starting the proxy.
"@
Set-Content -Path (Join-Path $OutputDir "PACKAGE_README.txt") -Value $notes -Encoding UTF8

Write-Host ""
Write-Host "Publish complete."
Write-Host "Copy this folder to the target machine:"
Write-Host "  $OutputDir"
Write-Host ""
Write-Host "Target-machine entry point:"
Write-Host "  $OutputDir\RunLauncher.cmd"
