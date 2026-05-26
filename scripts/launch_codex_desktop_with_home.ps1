param(
    [string]$CodexHome = "$env:USERPROFILE\.codex-desktop",
    [string]$CodexDesktopPath = "",
    [string]$Model = "glm-5.1",
    [string]$ProxyHost = "127.0.0.1",
    [string]$ProxyPort = "8787"
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $scriptDir "codex_home_launcher_common.ps1")

function Find-CodexDesktopPath {
    $candidates = @(
        "$env:LOCALAPPDATA\Programs\Codex\Codex.exe",
        "$env:LOCALAPPDATA\Programs\Codex Desktop\Codex Desktop.exe",
        "$env:LOCALAPPDATA\Programs\codex\Codex.exe",
        "$env:LOCALAPPDATA\OpenAI Codex\Codex.exe",
        "$env:PROGRAMFILES\Codex\Codex.exe",
        "$env:PROGRAMFILES\Codex Desktop\Codex Desktop.exe",
        "${env:PROGRAMFILES(X86)}\Codex\Codex.exe",
        "${env:PROGRAMFILES(X86)}\Codex Desktop\Codex Desktop.exe"
    )

    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path $candidate)) {
            return $candidate
        }
    }

    $command = Get-Command codex -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }

    return $null
}

$homeInfo = Initialize-CodexHome -CodexHome $CodexHome -Model $Model -ProxyHost $ProxyHost -ProxyPort $ProxyPort
$env:CODEX_HOME = $homeInfo.Home
Copy-UserEnvironmentVariableToProcess -Name "CODEX_GLM_PROXY_KEY"

$resolvedDesktopPath = $CodexDesktopPath
if (-not $resolvedDesktopPath) {
    $resolvedDesktopPath = Find-CodexDesktopPath
}

Write-Host ""
Write-Host "Launching Codex Desktop with isolated CODEX_HOME."
Write-Host "CODEX_HOME: $env:CODEX_HOME"
Write-Host "Config:     $($homeInfo.Config)"

if (-not $resolvedDesktopPath) {
    Write-Host ""
    Write-Host "Could not auto-detect Codex Desktop executable."
    Write-Host "Re-run with:"
    Write-Host '  .\scripts\launch_codex_desktop_with_home.ps1 -CodexDesktopPath "C:\Path\To\Codex.exe"'
    exit 2
}

Write-Host "Executable: $resolvedDesktopPath"
Write-Host ""
Write-Host "If Codex Desktop is already running, fully quit it before using this launcher."

Start-Process -FilePath $resolvedDesktopPath -WorkingDirectory (Split-Path -Parent $resolvedDesktopPath)
