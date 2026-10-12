# Shared by Setup.ps1 and Launch.ps1. Compatible with Windows PowerShell 5.1.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$script:DewarpRoot = Split-Path -Parent $PSScriptRoot

function ConvertTo-NativeArgument {
    param([AllowEmptyString()][string]$Value)
    # Windows CommandLineToArgvW/CRT rules, including trailing backslashes.
    '"' + ([regex]::Replace(([regex]::Replace($Value, '(\\*)"', '$1$1\"')), '(\\+)$', '$1$1')) + '"'
}

function Invoke-BootstrapCommand {
    param([string]$FilePath, [string[]]$Arguments = @(), [int]$WallSeconds = 30,
        [int]$StallSeconds = 30, [string]$LogPath, [switch]$Quiet, [switch]$AllowFailure)
    if (-not ('Dewarp.Bootstrap.ProcessRunner' -as [type])) {
        Add-Type -Path (Join-Path $PSScriptRoot 'ProcessRunner.cs')
    }
    $argumentString = (@($Arguments | ForEach-Object { ConvertTo-NativeArgument $_ }) -join ' ')
    $result = [Dewarp.Bootstrap.ProcessRunner]::Run($FilePath, $argumentString,
        $script:DewarpRoot, $WallSeconds, $StallSeconds, $LogPath, [bool]$Quiet)
    if (-not $AllowFailure -and $result.ExitCode -ne 0) {
        $reason = if ($result.Timeout) { $result.Timeout } else { "exit $($result.ExitCode)" }
        throw "Command failed ($reason): $FilePath $argumentString`n$($result.Output)"
    }
    return $result
}

function Update-DewarpPath {
    # Preserve caller additions and pick up PATH changes made by a new installer.
    $paths = @($env:Path, [Environment]::GetEnvironmentVariable('Path', 'Machine'),
        [Environment]::GetEnvironmentVariable('Path', 'User'),
        (Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Links'))
    $env:Path = (($paths -join ';') -split ';' | Where-Object { $_ } | Select-Object -Unique) -join ';'
}

function Find-DewarpPython {
    $candidates = New-Object 'System.Collections.Generic.List[string]'
    foreach ($name in @('python.exe', 'python3.exe')) {
        foreach ($command in @(Get-Command $name -CommandType Application -ErrorAction SilentlyContinue)) {
            # Do not invoke the Microsoft Store placeholder (it can open the Store).
            if ($command.Source -notlike '*\Microsoft\WindowsApps\*') { $candidates.Add($command.Source) }
        }
    }
    foreach ($key in @('HKCU:\Software\Python\PythonCore', 'HKLM:\Software\Python\PythonCore',
            'HKLM:\Software\WOW6432Node\Python\PythonCore')) {
        if (Test-Path -LiteralPath $key) {
            foreach ($version in @(Get-ChildItem -LiteralPath $key -ErrorAction SilentlyContinue | Sort-Object PSChildName -Descending)) {
                $install = Join-Path $version.PSPath 'InstallPath'
                if (Test-Path -LiteralPath $install) {
                    $path = (Get-Item -LiteralPath $install).GetValue('ExecutablePath')
                    if (-not $path) { $path = Join-Path (Get-Item -LiteralPath $install).GetValue('') 'python.exe' }
                    if ($path) { $candidates.Add($path) }
                }
            }
        }
    }
    foreach ($candidate in @($candidates | Select-Object -Unique)) {
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { continue }
        try {
            $probe = Invoke-BootstrapCommand $candidate @('-c', 'import sys; print(sys.executable); sys.exit(0 if (3, 10) <= sys.version_info < (3, 14) and sys.maxsize > 2**32 else 1)') -WallSeconds 15 -StallSeconds 15 -Quiet -AllowFailure
            if ($probe.ExitCode -eq 0) { return $probe.Output.Trim() }
        } catch { Write-Verbose "Python probe failed: $candidate" }
    }
    return $null
}

function Find-DewarpExifTool {
    Update-DewarpPath
    $candidates = @(Get-Command 'exiftool.exe' -CommandType Application -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source)
    $candidates += @((Join-Path $env:LOCALAPPDATA 'Programs\ExifTool\exiftool.exe'),
        (Join-Path $env:ProgramFiles 'ExifTool\exiftool.exe'))
    foreach ($candidate in @($candidates | Where-Object { $_ } | Select-Object -Unique)) {
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { continue }
        try {
            $probe = Invoke-BootstrapCommand $candidate @('-ver') -WallSeconds 15 -StallSeconds 15 -Quiet -AllowFailure
            # Perl may emit a harmless locale warning on stderr before its version.
            if ($probe.ExitCode -eq 0 -and $probe.Output -match '(?m)^\d+\.\d+\r?$') {
                $directory = Split-Path -Parent $candidate
                if ($directory -notin ($env:Path -split ';')) { $env:Path += ';' + $directory }
                return $candidate
            }
        } catch { Write-Verbose "ExifTool probe failed: $candidate" }
    }
    return $null
}

function Add-DewarpExifToolPath {
    param([string]$ExifTool)
    $directory = Split-Path -Parent $ExifTool
    $userPath = [string][Environment]::GetEnvironmentVariable('Path', 'User')
    $machinePath = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    if ($directory -notin (($userPath + ';' + $machinePath) -split ';')) {
        [Environment]::SetEnvironmentVariable('Path', (($userPath.TrimEnd(';') + ';' + $directory).TrimStart(';')), 'User')
    }
    Update-DewarpPath
}

function Get-DewarpManifestHash {
    # Both package metadata and validated pins trigger an environment repair.
    $parts = foreach ($relative in @('app\pyproject.toml', 'app\constraints.txt')) {
        # .NET also works when PowerShell was started with a restricted module path.
        $stream = [IO.File]::OpenRead((Join-Path $script:DewarpRoot $relative))
        $sha = [Security.Cryptography.SHA256]::Create()
        try { [BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-', '') }
        finally { $stream.Dispose(); $sha.Dispose() }
    }
    return ($parts -join ':')
}

function Test-DewarpEnvironment {
    $python = Join-Path $script:DewarpRoot '.venv\Scripts\python.exe'
    $stamp = Join-Path $script:DewarpRoot '.local\environment.json'
    if (-not (Test-Path -LiteralPath $python -PathType Leaf) -or -not (Test-Path -LiteralPath $stamp -PathType Leaf)) { return $false }
    try {
        $state = Get-Content -LiteralPath $stamp -Raw | ConvertFrom-Json
        if ($state.root -ne $script:DewarpRoot -or $state.manifest_hash -ne (Get-DewarpManifestHash)) { return $false }
        $probe = Invoke-BootstrapCommand $python @('-c', 'import sys, cv2, numpy, PIL, piexif, PySide6.QtWidgets, dewarp.cli; assert (3,10) <= sys.version_info < (3,14); assert sys.maxsize > 2**32; assert sys.prefix != sys.base_prefix') -WallSeconds 30 -StallSeconds 30 -Quiet -AllowFailure
        return ($probe.ExitCode -eq 0 -and [bool](Find-DewarpExifTool))
    } catch { return $false }
}
