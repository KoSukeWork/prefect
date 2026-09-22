$ErrorActionPreference = 'Stop'
$envPath = Join-Path $PSScriptRoot '.env'
if (Test-Path -LiteralPath $envPath) {
    Write-Host '.env already exists; existing credentials preserved.'
    exit 0
}
function New-Password {
    $bytes = New-Object byte[] 24
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    return [System.BitConverter]::ToString($bytes).Replace('-', '').ToLowerInvariant()
}
$content = @"
POSTGRES_PASSWORD=$(New-Password)
PREFECT_PASSWORD=$(New-Password)
WORKBENCH_PORT=8090
WORKBENCH_PUBLIC_URL=http://localhost:8090
WORKER_LIMIT=4
WORKBENCH_TAG=local
"@
[System.IO.File]::WriteAllText($envPath, $content, [System.Text.UTF8Encoding]::new($false))
Write-Host "Created $envPath. Credentials are stored locally; do not commit this file."
