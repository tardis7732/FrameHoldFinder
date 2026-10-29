param(
    [string]$PythonExe = 'python',
    [string]$DistDirectory = '',
    [switch]$Clean
)
$ErrorActionPreference = 'Stop'
$repositoryRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$sourceRoot = Join-Path $repositoryRoot 'src'
$packagingRoot = Join-Path $repositoryRoot 'packaging'
# Resolve explicit interpreter paths relative to the caller before invoking any
# build steps. Bare names continue to use the caller's active environment/PATH.
if ($PythonExe -match '[\\/]') {
    $PythonExe = (Resolve-Path -LiteralPath $PythonExe -ErrorAction Stop).ProviderPath
} else {
    $PythonExe = (Get-Command $PythonExe -CommandType Application -ErrorAction Stop).Source
}
if (-not $DistDirectory) {
    $DistDirectory = Join-Path $repositoryRoot 'dist'
} elseif (-not [System.IO.Path]::IsPathRooted($DistDirectory)) {
    $DistDirectory = Join-Path $repositoryRoot $DistDirectory
}
$DistDirectory = [System.IO.Path]::GetFullPath($DistDirectory)
$buildDirectory = Join-Path $repositoryRoot 'build'

foreach ($name in @('ffmpeg', 'ffprobe')) {
    if (-not (Get-Command $name -ErrorAction SilentlyContinue)) {
        throw "$name must be installed and available on PATH."
    }
}
foreach ($name in @('app.py', 'cli.py', 'sdk_worker.py', 'source_format.py')) {
    if (-not (Test-Path -LiteralPath (Join-Path $sourceRoot $name) -PathType Leaf)) {
        throw "Required source file is missing: src/$name"
    }
}
foreach ($name in @('frameholdfinder.spec', 'app.ico', 'version_info.txt')) {
    if (-not (Test-Path -LiteralPath (Join-Path $packagingRoot $name) -PathType Leaf)) {
        throw "Required packaging file is missing: packaging/$name"
    }
}

$buildArgs = @('-m', 'PyInstaller', '--noconfirm', '--distpath', $DistDirectory,
               '--workpath', $buildDirectory)
if ($Clean) { $buildArgs += '--clean' }
$buildArgs += (Join-Path $packagingRoot 'frameholdfinder.spec')
& $PythonExe @buildArgs
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed.' }

$releaseRoot = Join-Path $DistDirectory 'FrameHoldFinder'
foreach ($pattern in @('README*.md', '*LICENSE*', '*NOTICE*', 'THIRD_PARTY*')) {
    Get-ChildItem -LiteralPath $repositoryRoot -Filter $pattern -File | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $releaseRoot $_.Name) -Force
    }
}
$docs = Join-Path $repositoryRoot 'docs'
if (Test-Path -LiteralPath $docs -PathType Container) {
    & $PythonExe -c "import shutil,sys; shutil.copytree(sys.argv[1],sys.argv[2],dirs_exist_ok=True)" $docs (Join-Path $releaseRoot 'docs')
    if ($LASTEXITCODE -ne 0) { throw 'Copying release documentation failed.' }
}
# Never distribute a developer's SDK interpreter path. Users select their own
# installed environment in Settings, or through CLI sdk-check --python --save.
[System.IO.File]::WriteAllText((Join-Path $releaseRoot 'sdk_settings.json'), ('{}' + [Environment]::NewLine), [System.Text.UTF8Encoding]::new($false))
Write-Output "Portable package: $releaseRoot"
