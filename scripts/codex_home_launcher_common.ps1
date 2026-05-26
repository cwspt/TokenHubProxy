function Initialize-CodexHome {
    param(
        [Parameter(Mandatory = $true)][string]$CodexHome,
        [string]$Model = "glm-5.1",
        [string]$ProxyHost = "127.0.0.1",
        [string]$ProxyPort = "8787"
    )

    $resolvedHome = [Environment]::ExpandEnvironmentVariables($CodexHome)
    if (-not (Test-Path $resolvedHome)) {
        New-Item -ItemType Directory -Path $resolvedHome -Force | Out-Null
        Write-Host "Created CODEX_HOME: $resolvedHome"
    }

    $targetConfig = Join-Path $resolvedHome "config.toml"
    if (-not (Test-Path $targetConfig)) {
        $config = @"
model_provider = "glm_tokenhub_proxy"
model = "$Model"
model_reasoning_effort = "medium"
model_verbosity = "medium"
model_context_window = 64000
model_max_output_tokens = 8192

[model_providers.glm_tokenhub_proxy]
name = "GLM 5.1 via Tencent TokenHub Proxy"
base_url = "http://$ProxyHost`:$ProxyPort/v1"
wire_api = "responses"
env_key = "CODEX_GLM_PROXY_KEY"
stream_idle_timeout_ms = 300000
stream_max_retries = 3
request_max_retries = 2
"@
        Set-Content -Path $targetConfig -Value $config -Encoding UTF8
        Write-Host "Created default isolated Codex config:"
        Write-Host "  $targetConfig"
    }
    else {
        Write-Host "Using existing config:"
        Write-Host "  $targetConfig"
    }

    return [pscustomobject]@{
        Home = $resolvedHome
        Config = $targetConfig
    }
}

function Copy-UserEnvironmentVariableToProcess {
    param([Parameter(Mandatory = $true)][string]$Name)

    if ([Environment]::GetEnvironmentVariable($Name, [EnvironmentVariableTarget]::Process)) {
        return
    }

    $userValue = [Environment]::GetEnvironmentVariable($Name, [EnvironmentVariableTarget]::User)
    if ($userValue) {
        Set-Item -Path "Env:$Name" -Value $userValue
    }
}
