param(
    [string]$CodexHome = "$env:USERPROFILE\.codex-vscode",
    [string]$Workspace = "",
    [string]$CodePath = "",
    [string]$Model = "glm-5.1",
    [string]$ProxyHost = "127.0.0.1",
    [string]$ProxyPort = "8787"
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $scriptDir
. (Join-Path $scriptDir "codex_home_launcher_common.ps1")

function Find-CodePath {
    $command = Get-Command code -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }

    $candidates = @(
        "$env:LOCALAPPDATA\Programs\Microsoft VS Code\Code.exe",
        "$env:PROGRAMFILES\Microsoft VS Code\Code.exe",
        "${env:PROGRAMFILES(X86)}\Microsoft VS Code\Code.exe"
    )

    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path $candidate)) {
            return $candidate
        }
    }

    return $null
}

$homeInfo = Initialize-CodexHome -CodexHome $CodexHome -Model $Model -ProxyHost $ProxyHost -ProxyPort $ProxyPort
$env:CODEX_HOME = $homeInfo.Home
Copy-UserEnvironmentVariableToProcess -Name "CODEX_GLM_PROXY_KEY"

$resolvedCodePath = $CodePath
if (-not $resolvedCodePath) {
    $resolvedCodePath = Find-CodePath
}

if (-not $Workspace) {
    $Workspace = $projectRoot
}

Write-Host ""
Write-Host "Launching VS Code with isolated CODEX_HOME."
Write-Host "CODEX_HOME: $env:CODEX_HOME"
Write-Host "Config:     $($homeInfo.Config)"
Write-Host "Workspace:  $Workspace"

if (-not $resolvedCodePath) {
    Write-Host ""
    Write-Host "Could not auto-detect VS Code executable."
    Write-Host "Re-run with:"
    Write-Host '  .\scripts\launch_vscode_with_codex_home.ps1 -CodePath "C:\Path\To\Code.exe"'
    exit 2
}

Write-Host "Executable: $resolvedCodePath"
Write-Host ""
Write-Host "If VS Code is already running, fully quit it first; otherwise it may reuse an old process without the new CODEX_HOME."

Start-Process -FilePath $resolvedCodePath -ArgumentList @($Workspace)
