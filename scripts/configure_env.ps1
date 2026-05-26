param(
    [ValidateSet("Process", "User")]
    [string]$Scope = "Process",

    [string]$TokenHubBaseUrl = "https://tokenhub.tencentmaas.com/plan/v3/chat/completions",
    [string]$TokenHubModel = "glm-5.1",
    [string]$ProxyHost = "127.0.0.1",
    [string]$ProxyPort = "8787",
    [string]$ProxyRequestTimeoutSeconds = "600",

    [ValidateSet("true", "false")]
    [string]$EnableToolCalls = "false",

    [switch]$PersistCodexKey,
    [switch]$ShowValues
)

$ErrorActionPreference = "Stop"

function ConvertFrom-SecureStringToPlainText {
    param([Parameter(Mandatory = $true)][securestring]$SecureValue)

    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecureValue)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
}

function New-ProxyKey {
    $bytes = [byte[]]::new(32)
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $rng.GetBytes($bytes)
    }
    finally {
        $rng.Dispose()
    }
    return [Convert]::ToBase64String($bytes).TrimEnd("=").Replace("+", "-").Replace("/", "_")
}

function Normalize-SecretValue {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Value
    )

    $normalized = $Value.Trim()
    if ($normalized.Length -ge 2) {
        $first = $normalized.Substring(0, 1)
        $last = $normalized.Substring($normalized.Length - 1, 1)
        if (($first -eq '"' -and $last -eq '"') -or ($first -eq "'" -and $last -eq "'")) {
            $normalized = $normalized.Substring(1, $normalized.Length - 2).Trim()
        }
    }

    if ($Name -eq "TOKENHUB_API_KEY" -and $normalized.StartsWith("Bearer ", [StringComparison]::OrdinalIgnoreCase)) {
        $normalized = $normalized.Substring(7).Trim()
        Write-Host "Removed leading 'Bearer ' from TOKENHUB_API_KEY."
    }

    return $normalized
}

function Get-ExistingEnvValue {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$TargetScope
    )

    if ($TargetScope -eq "User") {
        return [Environment]::GetEnvironmentVariable($Name, [EnvironmentVariableTarget]::User)
    }

    return [Environment]::GetEnvironmentVariable($Name, [EnvironmentVariableTarget]::Process)
}

function Set-ProxyEnvValue {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Value,
        [Parameter(Mandatory = $true)][string]$TargetScope
    )

    if ($TargetScope -eq "User") {
        [Environment]::SetEnvironmentVariable($Name, $Value, [EnvironmentVariableTarget]::User)
    }

    Set-Item -Path "Env:$Name" -Value $Value
}

function Read-SecretValue {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$TargetScope
    )

    $existing = Get-ExistingEnvValue -Name $Name -TargetScope $TargetScope
    if ($existing) {
        $answer = Read-Host "$Name already exists in $TargetScope scope. Press Enter to keep it, or type 'replace'"
        if ($answer -ne "replace") {
            return Normalize-SecretValue -Name $Name -Value $existing
        }
    }

    $secureValue = Read-Host "Enter $Name" -AsSecureString
    $plainValue = Normalize-SecretValue -Name $Name -Value (ConvertFrom-SecureStringToPlainText -SecureValue $secureValue)
    if ([string]::IsNullOrWhiteSpace($plainValue)) {
        throw "$Name cannot be empty."
    }
    return $plainValue
}

function Read-OrGenerateProxyKey {
    param([Parameter(Mandatory = $true)][string]$TargetScope)

    $existing = Get-ExistingEnvValue -Name "CODEX_GLM_PROXY_KEY" -TargetScope $TargetScope
    if ($existing) {
        $answer = Read-Host "CODEX_GLM_PROXY_KEY already exists in $TargetScope scope. Press Enter to keep it, type 'replace', or type 'generate'"
        if ($answer -eq "replace") {
            $secureValue = Read-Host "Enter CODEX_GLM_PROXY_KEY" -AsSecureString
            $plainValue = Normalize-SecretValue -Name "CODEX_GLM_PROXY_KEY" -Value (ConvertFrom-SecureStringToPlainText -SecureValue $secureValue)
            if ([string]::IsNullOrWhiteSpace($plainValue)) {
                throw "CODEX_GLM_PROXY_KEY cannot be empty."
            }
            return [pscustomobject]@{
                Value = $plainValue
                Source = "manual"
            }
        }
        if ($answer -eq "generate") {
            return [pscustomobject]@{
                Value = New-ProxyKey
                Source = "generated"
            }
        }
        return [pscustomobject]@{
            Value = $existing
            Source = "existing"
        }
    }

    $answer = Read-Host "Press Enter to generate CODEX_GLM_PROXY_KEY, or type a custom value"
    if ([string]::IsNullOrWhiteSpace($answer)) {
        return [pscustomobject]@{
            Value = New-ProxyKey
            Source = "generated"
        }
    }
    return [pscustomobject]@{
        Value = Normalize-SecretValue -Name "CODEX_GLM_PROXY_KEY" -Value $answer
        Source = "manual"
    }
}

$tokenHubApiKey = Read-SecretValue -Name "TOKENHUB_API_KEY" -TargetScope $Scope
$codexProxyKeyResult = Read-OrGenerateProxyKey -TargetScope $Scope
$codexProxyKey = $codexProxyKeyResult.Value

$values = [ordered]@{
    TOKENHUB_API_KEY = $tokenHubApiKey
    TOKENHUB_BASE_URL = $TokenHubBaseUrl
    TOKENHUB_MODEL = $TokenHubModel
    CODEX_GLM_PROXY_KEY = $codexProxyKey
    PROXY_HOST = $ProxyHost
    PROXY_PORT = $ProxyPort
    PROXY_REQUEST_TIMEOUT_SECONDS = $ProxyRequestTimeoutSeconds
    ENABLE_TOOL_CALLS = $EnableToolCalls
}

foreach ($entry in $values.GetEnumerator()) {
    Set-ProxyEnvValue -Name $entry.Key -Value $entry.Value -TargetScope $Scope
}

if ($PersistCodexKey -and $Scope -ne "User") {
    [Environment]::SetEnvironmentVariable(
        "CODEX_GLM_PROXY_KEY",
        $codexProxyKey,
        [EnvironmentVariableTarget]::User
    )
}

Write-Host ""
Write-Host "Configured TokenHub proxy environment variables."
Write-Host "Primary scope: $Scope"
if ($PersistCodexKey -and $Scope -ne "User") {
    Write-Host "CODEX_GLM_PROXY_KEY was also written to User scope for Codex Desktop."
}
if ($codexProxyKeyResult.Source -eq "generated") {
    Write-Host ""
    Write-Host "Generated CODEX_GLM_PROXY_KEY:"
    Write-Host $codexProxyKey
}
Write-Host ""
Write-Host "Next commands:"
Write-Host "  .\.venv\Scripts\python scripts\probe_tokenhub.py"
Write-Host "  .\.venv\Scripts\python -m uvicorn proxy_app.main:app --host $ProxyHost --port $ProxyPort"
Write-Host ""
Write-Host "Sensitive values are hidden. Use -ShowValues only for local debugging."

if ($ShowValues) {
    Write-Host ""
    foreach ($entry in $values.GetEnumerator()) {
        if ($entry.Key -in @("TOKENHUB_API_KEY", "CODEX_GLM_PROXY_KEY")) {
            Write-Host "$($entry.Key)=<hidden>"
        }
        else {
            Write-Host "$($entry.Key)=$($entry.Value)"
        }
    }
}
