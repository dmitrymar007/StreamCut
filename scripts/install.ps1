$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path $PSScriptRoot)
if (-not (Test-Path '.venv\Scripts\python.exe')) {
    python -m venv .venv
    if ($LASTEXITCODE) { throw 'Cannot create Python virtual environment' }
}
& '.\.venv\Scripts\python.exe' -m pip install -r requirements-lock.txt
if ($LASTEXITCODE) { throw 'Dependency installation failed' }
& '.\.venv\Scripts\python.exe' -m pip install -e .
if ($LASTEXITCODE) { throw 'StreamCut installation failed' }
& '.\.venv\Scripts\python.exe' scripts/setup_media.py
if ($LASTEXITCODE) { throw 'Media runtime installation failed' }
Write-Host 'StreamCut is ready. Launch using the .cmd file in the project folder.'
