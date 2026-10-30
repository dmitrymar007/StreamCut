param([string]$Version = '0.3.1', [string]$MakeNsis = '')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path $PSScriptRoot)
$python = Join-Path (Get-Location) '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Prepare .venv using scripts/install.ps1 first' }
if (-not $MakeNsis) {
    $command = Get-Command makensis.exe -ErrorAction SilentlyContinue
    if ($command) { $MakeNsis = $command.Source }
    else { $MakeNsis = Join-Path (Get-Location) 'local\release-tools\nsis\nsis-3.12\makensis.exe' }
}
if (-not (Test-Path -LiteralPath $MakeNsis)) { throw 'Install NSIS 3.12 and pass -MakeNsis <absolute path>' }
& $python -m pytest -q --basetemp=local/pytest-build
if ($LASTEXITCODE) { throw 'Tests failed' }
& $python -m ruff check .
if ($LASTEXITCODE) { throw 'Lint failed' }
& $python scripts/collect_notices.py
if ($LASTEXITCODE) { throw 'Notice collection failed' }
& $python -m PyInstaller --noconfirm --distpath "dist/release-$Version" --workpath "build/release-$Version" StreamCut.spec
if ($LASTEXITCODE) { throw 'Binary build failed' }
$app = Join-Path (Get-Location) "dist\release-$Version\StreamCut"
$output = Join-Path (Get-Location) "release\StreamCut-Setup-$Version-windows-x64.exe"
New-Item -ItemType Directory -Force -Path (Split-Path $output) | Out-Null
& $MakeNsis /INPUTCHARSET UTF8 "/DVERSION=$Version" "/DAPPDIR=$app" "/DOUTPUT=$output" scripts/windows-installer.nsi
if ($LASTEXITCODE) { throw 'Installer build failed' }
Get-FileHash -LiteralPath $output -Algorithm SHA256
