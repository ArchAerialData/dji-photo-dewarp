<#
Install/repair dependencies for this checkout, without changing system Python packages.
Official package manifests: github.com/microsoft/winget-pkgs/tree/master/manifests/o/OliverBetz/ExifTool
                          github.com/microsoft/winget-pkgs/tree/master/manifests/p/Python/Python/3.13
WinGet user scope: learn.microsoft.com/windows/package-manager/winget/install
#>
[CmdletBinding()]
param()
. (Join-Path $PSScriptRoot 'Bootstrap.ps1')

$logDirectory = Join-Path $script:DewarpRoot '.local\logs'
New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null
$logPath = Join-Path $logDirectory ('setup-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.log')
$lockPath = Join-Path $script:DewarpRoot '.local\setup.lock'
$setupLock = $null
try {
    # An interrupted setup releases the exclusive handle automatically.
    $setupLock = [IO.File]::Open($lockPath, 'OpenOrCreate', 'ReadWrite', 'None')
    Write-Host "Dewarp setup: $script:DewarpRoot"
    Write-Host "Log: $logPath"
    if (Test-DewarpEnvironment) {
        'Environment already ready; no dependencies changed.' | Tee-Object -FilePath $logPath | Write-Host
        return
    }
    Update-DewarpPath
    $python = Find-DewarpPython
    if (-not $python) {
        $winget = Get-Command winget.exe -CommandType Application -ErrorAction SilentlyContinue
        if (-not $winget) { throw '64-bit Python 3.10-3.13 and WinGet were not found. Install Microsoft App Installer (WinGet), or Python from https://www.python.org/downloads/windows/, then rerun Setup.ps1.' }
        Write-Host 'Installing Python 3.13 for the current Windows user...'
        Invoke-BootstrapCommand $winget.Source @('install', '--id', 'Python.Python.3.13', '--exact', '--source', 'winget', '--scope', 'user', '--silent', '--accept-source-agreements', '--accept-package-agreements', '--disable-interactivity') -WallSeconds 900 -StallSeconds 300 -LogPath $logPath | Out-Null
        Update-DewarpPath
        $python = Find-DewarpPython
        if (-not $python) { throw 'Python installation finished, but no usable 64-bit Python 3.10-3.13 was found. See the setup log.' }
    }
    Write-Host "Python: $python"
    $exiftool = Find-DewarpExifTool
    if (-not $exiftool) {
        $winget = Get-Command winget.exe -CommandType Application -ErrorAction SilentlyContinue
        if (-not $winget) { throw 'ExifTool and WinGet were not found. Install Microsoft App Installer (WinGet), or install ExifTool and add it to PATH, then rerun Setup.ps1.' }
        Write-Host 'Installing ExifTool for the current Windows user...'
        Invoke-BootstrapCommand $winget.Source @('install', '--id', 'OliverBetz.ExifTool', '--exact', '--source', 'winget', '--scope', 'user', '--silent', '--accept-source-agreements', '--accept-package-agreements', '--disable-interactivity') -WallSeconds 900 -StallSeconds 300 -LogPath $logPath | Out-Null
        $exiftool = Find-DewarpExifTool
        if (-not $exiftool) { throw 'ExifTool installation finished, but exiftool.exe is not usable from PATH. Add its installed directory to your user PATH and rerun Setup.ps1. See the setup log.' }
        Add-DewarpExifToolPath $exiftool
    }
    Write-Host "ExifTool: $exiftool"
    $venv = Join-Path $script:DewarpRoot '.venv'
    $venvPython = Join-Path $venv 'Scripts\python.exe'
    # venv safely repairs an incomplete environment; no source/photo files are removed.
    Invoke-BootstrapCommand $python @('-m', 'venv', $venv) -WallSeconds 180 -StallSeconds 120 -LogPath $logPath | Out-Null
    Invoke-BootstrapCommand $venvPython @('-m', 'pip', 'install', '--disable-pip-version-check', '--no-input', '--timeout', '30', '--retries', '2', '--only-binary=:all:', '--constraint', (Join-Path $script:DewarpRoot 'app\constraints.txt'), '-e', (Join-Path $script:DewarpRoot 'app')) -WallSeconds 900 -StallSeconds 180 -LogPath $logPath | Out-Null
    Invoke-BootstrapCommand $venvPython @('-c', 'import sys, cv2, numpy, PIL, piexif, PySide6.QtWidgets, dewarp.cli; print("Dependency imports OK; Python " + sys.version.split()[0])') -WallSeconds 30 -StallSeconds 30 -LogPath $logPath | Out-Null
    $state = @{ root = $script:DewarpRoot; manifest_hash = Get-DewarpManifestHash; python = $venvPython;
        exiftool = $exiftool; installed_at = (Get-Date).ToString('o') }
    $state | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $script:DewarpRoot '.local\environment.json') -Encoding UTF8
    Write-Host 'Setup complete. Open Dewarp.cmd to start the GUI.'
} catch {
    $message = $_.Exception.Message
    Add-Content -LiteralPath $logPath -Value "SETUP FAILED: $message"
    Write-Error "Setup failed: $message`nLog: $logPath"
    throw
} finally {
    if ($setupLock) { $setupLock.Dispose() }
}
