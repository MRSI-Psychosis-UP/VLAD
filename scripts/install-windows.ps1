# Installs the Linux runtime in WSL and registers a Windows command by default.
[CmdletBinding()]
param(
    [string]$Distribution,
    [switch]$AddToPath = $true,
    [switch]$NoAddToPath,
    [switch]$ReportsOnly,
    [switch]$SkipSystem,
    [switch]$SkipFsl,
    [switch]$NoCheck,
    [switch]$Yes,
    [string]$Python,
    [string]$FslDir
)
$ErrorActionPreference = 'Stop'
if ($NoAddToPath) { $AddToPath = $false }
if ($env:OS -ne 'Windows_NT') {
    throw 'Run this script from Windows PowerShell. In Linux or macOS, use ./install.sh.'
}
$wsl = Join-Path $env:SystemRoot 'System32\wsl.exe'
if (-not (Test-Path -LiteralPath $wsl -PathType Leaf)) {
    throw 'Install WSL with a Linux distribution before running this installer.'
}

$checkout = (Get-Item -LiteralPath (Join-Path $PSScriptRoot '..')).FullName
if ($checkout -match '^\\\\(?:wsl\.localhost|wsl\$)\\([^\\]+)\\(.+)$') {
    if ($Distribution -and $Distribution -ne $Matches[1]) {
        throw 'The requested WSL distribution differs from the one containing the checkout.'
    }
    $Distribution = $Matches[1]
    $linuxRoot = '/' + ($Matches[2] -replace '\\', '/')
} else {
    $distributionArgs = @()
    if ($Distribution) { $distributionArgs = @('--distribution', $Distribution) }
    $linuxRoot = & $wsl @distributionArgs --exec wslpath -a -u $checkout
    if ($LASTEXITCODE -ne 0 -or $linuxRoot -isnot [string] -or $linuxRoot -notlike '/*') {
        throw 'Could not locate the checkout in WSL. Verify that WSL and a Linux distribution are ready.'
    }
}
if (-not $Distribution) {
    $Distribution = & $wsl --exec printenv WSL_DISTRO_NAME
    if ($LASTEXITCODE -ne 0 -or $Distribution -isnot [string] -or -not $Distribution) {
        throw 'Could not identify the default WSL distribution. Run again with -Distribution.'
    }
}

$installArgs = @()
if (-not $AddToPath) { $installArgs += '--no-add-to-path' }
if ($ReportsOnly) { $installArgs += '--reports-only' }
if ($SkipSystem)  { $installArgs += '--skip-system' }
if ($SkipFsl)     { $installArgs += '--skip-fsl' }
if ($NoCheck)     { $installArgs += '--no-check' }
if ($Yes)         { $installArgs += '--yes' }
if ($Python)      { $installArgs += @('--python', $Python) }
if ($FslDir)      { $installArgs += @('--fsl-dir', $FslDir) }

$linuxLauncher = "$($linuxRoot.TrimEnd('/'))/vlad"
if ($AddToPath) {
    # Keep config in Unicode environment values so the batch launcher can
    # remain ASCII even when the checkout or distribution name contains Unicode.
    if ($linuxLauncher -match '["\r\n]' -or $Distribution -match '["\r\n]') {
        throw 'Windows PATH setup requires a checkout path and WSL distribution name without quotes or line breaks.'
    }
    $bin = Join-Path $env:LOCALAPPDATA 'VLAD\bin'
    $launcher = Join-Path $bin 'vlad.cmd'
    $marker = 'rem Managed by VLAD install-windows.ps1'
    if (Test-Path -LiteralPath $launcher) {
        $existing = Get-Item -LiteralPath $launcher -Force
        if ($existing.PSIsContainer -or ($existing.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
            $marker -cnotin (Get-Content -LiteralPath $launcher)) {
            throw "An existing command is at $launcher. Choose a different location for that command before registering VLAD."
        }
    }
}

& $wsl --distribution $Distribution --cd $linuxRoot --exec bash ./install.sh @installArgs
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
if (-not $AddToPath) {
    return
}

[void][IO.Directory]::CreateDirectory($bin)
$scriptLines = [string[]]@(
    '@echo off'
    $marker
    'setlocal DisableDelayedExpansion'
    'if not defined VLAD_WSL_DISTRO exit /b 1'
    'if not defined VLAD_WSL_LAUNCHER exit /b 1'
    '"%SystemRoot%\System32\wsl.exe" --distribution "%VLAD_WSL_DISTRO%" --exec "%VLAD_WSL_LAUNCHER%" %*'
    'exit /b %errorlevel%'
)
[IO.File]::WriteAllLines($launcher, $scriptLines, [Text.Encoding]::ASCII)
foreach ($setting in @{VLAD_WSL_DISTRO = $Distribution; VLAD_WSL_LAUNCHER = $linuxLauncher}.GetEnumerator()) {
    [Environment]::SetEnvironmentVariable($setting.Key, $setting.Value, 'User')
    [Environment]::SetEnvironmentVariable($setting.Key, $setting.Value, 'Process')
}
foreach ($scope in @('User', 'Process')) {
    $value = [Environment]::GetEnvironmentVariable('Path', $scope)
    $entries = @($value -split ';' | ForEach-Object {
        [Environment]::ExpandEnvironmentVariables($_.Trim().Trim('"')).TrimEnd('\')
    })
    if ($bin -notin $entries) {
        $next = if ($value) { $value.TrimEnd(';') + ';' + $bin } else { $bin }
        [Environment]::SetEnvironmentVariable('Path', $next, $scope)
    }
}
$resolved = Get-Command vlad -ErrorAction SilentlyContinue
if ($resolved -and $resolved.Path -ne $launcher) {
    Write-Warning "An existing 'vlad' command takes precedence. This installation is at $launcher."
}
Write-Host 'VLAD is on your Windows user PATH. Open a new terminal and run vlad --help.'
