param([string]$Version = '0.3.1')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path $PSScriptRoot)
$gh = Get-Command gh.exe -ErrorAction SilentlyContinue
if ($gh) { $gh = $gh.Source }
else { $gh = Join-Path (Get-Location) 'local\release-tools\gh\bin\gh.exe' }
if (-not (Test-Path -LiteralPath $gh)) { throw 'Install GitHub CLI from https://cli.github.com/ first' }
& $gh auth status
if ($LASTEXITCODE) { throw 'Run gh auth login first, then repeat this script' }
if ((git branch --show-current) -ne 'main') { throw 'Publish from the verified main branch' }
if (git status --porcelain) { throw 'Commit or preserve local changes before publication' }
$repo = 'dmitrymar007/StreamCut'
$origin = git remote get-url origin
if ($origin -notmatch '^https://github\.com/dmitrymar007/StreamCut(\.git)?/?$|^git@github\.com:dmitrymar007/StreamCut(\.git)?$') {
    throw 'Origin does not point to dmitrymar007/StreamCut'
}
$files = @("StreamCut-Setup-$Version-windows-x64.exe", "StreamCut-$Version-windows-x64.zip",
    "StreamCut-$Version-source.zip", 'BUILDINFO.json', 'SHA256SUMS.txt')
foreach ($name in $files) {
    if (-not (Test-Path -LiteralPath "release\$name")) { throw "Missing release asset: $name" }
}
foreach ($line in Get-Content -LiteralPath 'release\SHA256SUMS.txt') {
    $parts = $line -split '\s+', 2
    $name = $parts[1].TrimStart('*')
    if ($name -notin $files) { throw "Unexpected checksum entry: $name" }
    if ((Get-FileHash -LiteralPath "release\$name" -Algorithm SHA256).Hash -ne $parts[0]) {
        throw "Checksum mismatch: $name"
    }
}
$build = Get-Content -LiteralPath 'release\BUILDINFO.json' -Raw | ConvertFrom-Json
$head = git rev-parse HEAD
if ($build.commit -ne $head) { throw 'Release files belong to a different source commit' }
$tag = "v$Version"
$existing = git tag --list $tag
if ($existing) {
    if ((git rev-list -n 1 $tag) -ne $head) { throw 'Existing tag points to a different commit' }
} else {
    git tag $tag
    if ($LASTEXITCODE) { throw 'Cannot create release tag' }
}
git push origin main $tag
if ($LASTEXITCODE) { throw 'Git push failed' }
$assets = $files | ForEach-Object { Join-Path (Get-Location) "release\$_" }
& $gh release create $tag @assets --repo $repo --verify-tag --latest `
    --title "StreamCut $Version - Windows / NVIDIA / CPU" --notes-file "docs\RELEASE-$Version.md"
if ($LASTEXITCODE) { throw 'Release creation failed; existing releases were not overwritten' }
& $gh release view $tag --repo $repo
