$ErrorActionPreference = 'Stop'
$sourceRoot = Split-Path $PSScriptRoot
$targetRoot = 'D:\narezki\StreamCut'
Get-ChildItem -LiteralPath $sourceRoot -Force | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination $targetRoot -Recurse -Force
}
