# Inspect only descendants of the actual root CMD launcher. No photo processing.
param([Parameter(Mandatory=$true)][string]$OutputFile)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class DewarpLaunchNative {
    [DllImport("dwmapi.dll")] public static extern int DwmGetWindowAttribute(IntPtr hwnd, int attribute, out int value, int size);
    [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr hwnd, uint message, IntPtr w, IntPtr l);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hwnd);
    [DllImport("kernel32.dll")] public static extern bool GetExitCodeProcess(IntPtr process, out uint exitCode);
}
'@
$expectedPython = [IO.Path]::GetFullPath((Join-Path $repo '.venv\Scripts\pythonw.exe'))
$logs = Join-Path $repo '.local\logs'
$beforeLogs = @(Get-ChildItem -LiteralPath $logs -Filter 'gui-*.error.log' | ForEach-Object FullName)
$owned = New-Object 'System.Collections.Generic.HashSet[int]'
$ancestry = New-Object 'System.Collections.Generic.List[object]'
$gui = $null
$venvLauncher = $null
$launcher = $null
$evidence = [ordered]@{ captured_utc=[DateTime]::UtcNow.ToString('o'); cwd=(Get-Location).Path;
    launcher=(Join-Path $repo 'Dewarp.cmd'); checks=@(); photos_accessed=$false; passed=$false }
function Check([string]$Name, [bool]$Passed) {
    $evidence.checks += [ordered]@{name=$Name; passed=$Passed}
    if (-not $Passed) { throw "Failed: $Name" }
    Write-Output "PASS: $Name"
}
try {
    # Keep this wrapper alive after CMD exits so its bounded Job Object still owns GUI.
    $cmdArguments = '/d /s /c ""' + (Join-Path $repo 'Dewarp.cmd') + '""'
    $launcher = Start-Process -FilePath $env:ComSpec -ArgumentList $cmdArguments -WorkingDirectory (Get-Location).Path -WindowStyle Hidden -PassThru
    [void]$owned.Add($launcher.Id)
    $deadline = [DateTime]::UtcNow.AddSeconds(25)
    while ([DateTime]::UtcNow -lt $deadline -and -not $gui) {
        $snapshot = @(Get-CimInstance Win32_Process -Filter "Name='cmd.exe' OR Name='powershell.exe' OR Name='pythonw.exe'")
        foreach ($process in $snapshot) {
            if ($owned.Contains([int]$process.ParentProcessId) -and $owned.Add([int]$process.ProcessId)) {
                $ancestry.Add([ordered]@{pid=$process.ProcessId; parent=$process.ParentProcessId; executable=$process.ExecutablePath; command=$process.CommandLine})
            }
        }
        $match = @($snapshot | Where-Object { $owned.Contains([int]$_.ProcessId) -and $_.ExecutablePath -eq $expectedPython })
        if ($match.Count -eq 1) { $venvLauncher = Get-Process -Id $match[0].ProcessId }
        # Windows venv pythonw.exe is a redirector. Its base interpreter child owns HWND.
        if ($venvLauncher) {
            foreach ($candidate in @($snapshot | Where-Object { $owned.Contains([int]$_.ProcessId) -and $_.Name -eq 'pythonw.exe' -and $_.CommandLine.Contains($expectedPython) })) {
                $native = Get-Process -Id $candidate.ProcessId -ErrorAction SilentlyContinue
                if ($native -and $native.MainWindowHandle -ne 0) { $gui = $native; break }
            }
        }
        if (-not $gui) { Start-Sleep -Milliseconds 100 }
    }
    Check 'GUI process belongs to the actual CMD descendant tree' ([bool]$gui -and [bool]$venvLauncher)
    $evidence.owned_ancestry = @($ancestry.ToArray())
    $deadline = [DateTime]::UtcNow.AddSeconds(15)
    while ([DateTime]::UtcNow -lt $deadline) {
        $gui.Refresh()
        if ($gui.HasExited) { throw "GUI exited before creating its window: $($gui.ExitCode)" }
        if ($gui.MainWindowHandle -ne 0) { break }
        Start-Sleep -Milliseconds 100
    }
    $hwnd = $gui.MainWindowHandle
    $guiProcessHandle = $gui.Handle
    $evidence.gui = [ordered]@{pid=$gui.Id; path=$gui.Path; hwnd=$hwnd.ToInt64(); title=$gui.MainWindowTitle}
    Check 'Native GUI window is visible' ($hwnd -ne 0 -and [DewarpLaunchNative]::IsWindowVisible($hwnd))
    Check 'Expected main-window title' ($gui.MainWindowTitle.StartsWith('DJI Photo Dewarp') -and $gui.MainWindowTitle.EndsWith('Batch Processor'))
    $window = [System.Windows.Automation.AutomationElement]::FromHandle($hwnd)
    $named = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::NameProperty, 'Start batch')
    $matches = $window.FindAll([System.Windows.Automation.TreeScope]::Descendants, $named)
    $start = @($matches | Where-Object { $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Button })
    Check 'Start batch is exposed once as a native accessible button' ($start.Count -eq 1)
    $bounds = $start[0].Current.BoundingRectangle
    $windowBounds = $window.Current.BoundingRectangle
    $evidence.start_button = [ordered]@{name=$start[0].Current.Name; offscreen=$start[0].Current.IsOffscreen; enabled=$start[0].Current.IsEnabled;
        x=$bounds.X; y=$bounds.Y; width=$bounds.Width; height=$bounds.Height}
    Check 'Start button is visible inside the launched window' (-not $start[0].Current.IsOffscreen -and $windowBounds.Contains($bounds))
    $registry = Get-ItemProperty -LiteralPath 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize'
    $light = [int]$registry.AppsUseLightTheme
    $dark = 0
    $result = [DewarpLaunchNative]::DwmGetWindowAttribute($hwnd, 20, [ref]$dark, 4)
    $evidence.theme = [ordered]@{apps_use_light_theme=$light; dwm_immersive_dark_mode=$dark; dwm_hresult=$result}
    Check 'Native title-bar theme matches current Windows app appearance' ($result -eq 0 -and $dark -eq (1 - $light))
    Check 'Root launcher exits successfully' ($launcher.WaitForExit(5000) -and $launcher.ExitCode -eq 0)
    [void][DewarpLaunchNative]::PostMessage($hwnd, 0x0010, [IntPtr]::Zero, [IntPtr]::Zero)
    $closed = $gui.WaitForExit(5000)
    $exitCode = [uint32]::MaxValue
    $hasExitCode = [DewarpLaunchNative]::GetExitCodeProcess($guiProcessHandle, [ref]$exitCode)
    $evidence.gui.exit_code = $exitCode
    Check 'Owned GUI closes gracefully without processing' ($closed -and $hasExitCode -and $exitCode -eq 0)
    $afterLogs = @(Get-ChildItem -LiteralPath $logs -Filter 'gui-*.error.log' | Where-Object { $_.FullName -notin $beforeLogs })
    $evidence.error_logs = @($afterLogs | ForEach-Object { [ordered]@{path=$_.FullName; bytes=$_.Length; content=(Get-Content -LiteralPath $_.FullName -Raw)} })
    Check 'New GUI error log exists and is empty' ($afterLogs.Count -eq 1 -and $afterLogs[0].Length -eq 0)
    $evidence.gui_process_removed = -not [bool](Get-Process -Id $gui.Id -ErrorAction SilentlyContinue)
    Check 'No owned GUI process remains' $evidence.gui_process_removed
    $evidence.passed = $true
} catch {
    $evidence.error = $_.Exception.Message
    throw
} finally {
    if ($gui -and -not $gui.HasExited -and $gui.MainWindowHandle -ne 0) {
        [void][DewarpLaunchNative]::PostMessage($gui.MainWindowHandle, 0x0010, [IntPtr]::Zero, [IntPtr]::Zero)
        [void]$gui.WaitForExit(5000)
    }
    $evidence | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $OutputFile -Encoding UTF8
}
