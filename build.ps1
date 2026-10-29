param(
    [string]$PythonExe = 'python',
    [string]$DistDirectory = '',
    [switch]$Clean
)
$ErrorActionPreference = 'Stop'
$sourceRoot = [System.IO.Path]::GetFullPath($PSScriptRoot)
if (-not $DistDirectory) {
    $DistDirectory = Join-Path $sourceRoot 'dist'
} elseif (-not [System.IO.Path]::IsPathRooted($DistDirectory)) {
    $DistDirectory = Join-Path $sourceRoot $DistDirectory
}
$DistDirectory = [System.IO.Path]::GetFullPath($DistDirectory)
$buildDirectory = Join-Path $sourceRoot 'build'

foreach ($name in @('ffmpeg', 'ffprobe')) {
    if (-not (Get-Command $name -ErrorAction SilentlyContinue)) {
        throw "$name must be installed and available on PATH."
    }
}
foreach ($name in @('app.py', 'cli.py', 'sdk_worker.py', 'source_format.py', 'app.ico', 'version_info.txt')) {
    if (-not (Test-Path -LiteralPath (Join-Path $sourceRoot $name) -PathType Leaf)) {
        throw "Required source file is missing: $name"
    }
}

$buildArgs = @('-m', 'PyInstaller', '--noconfirm', '--distpath', $DistDirectory,
               '--workpath', $buildDirectory)
if ($Clean) { $buildArgs += '--clean' }
$buildArgs += (Join-Path $sourceRoot 'frameholdfinder.spec')
& $PythonExe @buildArgs
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed.' }

$releaseRoot = Join-Path $DistDirectory 'FrameHoldFinder'
foreach ($pattern in @('README*.md', '*LICENSE*', '*NOTICE*', 'THIRD_PARTY*')) {
    Get-ChildItem -LiteralPath $sourceRoot -Filter $pattern -File | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $releaseRoot $_.Name) -Force
    }
}
$docs = Join-Path $sourceRoot 'docs'
if (Test-Path -LiteralPath $docs -PathType Container) {
    & $PythonExe -c "import shutil,sys; shutil.copytree(sys.argv[1],sys.argv[2],dirs_exist_ok=True)" $docs (Join-Path $releaseRoot 'docs')
    if ($LASTEXITCODE -ne 0) { throw 'Copying release documentation failed.' }
}
# Never distribute a developer's SDK interpreter path. Users select their own
# installed environment in Settings, or through CLI sdk-check --python --save.
[System.IO.File]::WriteAllText((Join-Path $releaseRoot 'sdk_settings.json'), ('{}' + [Environment]::NewLine), [System.Text.UTF8Encoding]::new($false))
Write-Output "Portable package: $releaseRoot"
