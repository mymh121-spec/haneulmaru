$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$taskPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) {
    Write-Host 'First run: creating a local .venv (Python 3.10+ required).'
    if (Get-Command py -ErrorAction SilentlyContinue) { & py -3 -m venv (Join-Path $PSScriptRoot '.venv') }
    elseif (Get-Command python -ErrorAction SilentlyContinue) { & python -m venv (Join-Path $PSScriptRoot '.venv') }
    else { throw 'Install Python 3.10+ from python.org, then run again.' }
    if ($LASTEXITCODE -ne 0) { throw 'Virtual environment creation failed.' }
}
& $taskPython -c "import reportlab, serial, bleak"
if ($LASTEXITCODE -ne 0) {
    & $taskPython -m pip install -r (Join-Path $PSScriptRoot 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed; check internet connection.' }
}
$tunnelExe = Join-Path $PSScriptRoot 'tools/cloudflared/cloudflared.exe'
$tunnelHash = (Get-Content -LiteralPath (Join-Path $PSScriptRoot 'tools/cloudflared/SHA256.txt') -Raw).Trim()
if (-not (Test-Path -LiteralPath $tunnelExe)) {
    Write-Host 'Downloading cloudflared 2026.10.0 from official GitHub release...'
    $tunnelDownload = $tunnelExe + '.download'
    Invoke-WebRequest -Uri 'https://github.com/cloudflare/cloudflared/releases/download/2026.10.0/cloudflared-windows-amd64.exe' -OutFile $tunnelDownload
    if ((Get-FileHash -LiteralPath $tunnelDownload -Algorithm SHA256).Hash -ne $tunnelHash) { throw 'Cloudflared checksum mismatch; download was not executed.' }
    Move-Item -LiteralPath $tunnelDownload -Destination $tunnelExe
}
if ((Get-FileHash -LiteralPath $tunnelExe -Algorithm SHA256).Hash -ne $tunnelHash) { throw 'Cloudflared checksum mismatch; executable was not started.' }
& $taskPython -X utf8 (Join-Path $PSScriptRoot 'experiments/haneulmaru_pro/stream_server.py')
