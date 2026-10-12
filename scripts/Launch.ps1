# Dewarp.cmd uses built-in Windows PowerShell; --console keeps GUI diagnostics visible.
# Any other arguments go unchanged to the normal dewarp CLI.
. (Join-Path $PSScriptRoot 'Bootstrap.ps1')
$appArguments = @($args)
$consoleMode = $false
if ($appArguments.Count -gt 0 -and $appArguments[0] -eq '--console') {
    $consoleMode = $true
    $appArguments = @($appArguments | Select-Object -Skip 1)
}
try {
    if (-not (Test-DewarpEnvironment)) { & (Join-Path $PSScriptRoot 'Setup.ps1') }
    Update-DewarpPath
    if ($consoleMode -or $appArguments.Count -gt 0) {
        # User-directed CLI/interactive GUI runs have no artificial processing deadline.
        & (Join-Path $script:DewarpRoot '.venv\Scripts\python.exe') -I -m dewarp @appArguments
        exit $LASTEXITCODE
    }
    $logDirectory = Join-Path $script:DewarpRoot '.local\logs'
    New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
    $errorLog = Join-Path $logDirectory "gui-$stamp.error.log"
    $gui = Start-Process -FilePath (Join-Path $script:DewarpRoot '.venv\Scripts\pythonw.exe') -ArgumentList @('-I', '-m', 'dewarp') -WorkingDirectory $script:DewarpRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logDirectory "gui-$stamp.out.log") -RedirectStandardError $errorLog -PassThru
    if ($gui.WaitForExit(1500) -and $gui.ExitCode -ne 0) {
        throw "The GUI exited during startup. See $errorLog`n$(Get-Content -LiteralPath $errorLog -Raw)"
    }
} catch {
    Write-Host "Dewarp could not start: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
