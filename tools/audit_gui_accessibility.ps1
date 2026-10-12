<# Read the actual GUI's Windows UIA tree in a disposable instance.
   Example: pwsh -File tools/audit_gui_accessibility.ps1 -State setup -OutputFile docs/gui-screen-accessibility-setup.json
   Requires the project's Python environment and Windows. No photos are processed.
#>
param(
    [string]$Python = 'python',
    [string]$OutputFile = '.work/accessibility-after.json',
    [switch]$RunningState,
    [ValidateSet('setup', 'running', 'completed', 'settings')]
    [string]$State = 'setup'
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$targetFile = Join-Path $PSScriptRoot '_gui_accessibility_target.py'
$temporaryBase = Join-Path ([IO.Path]::GetTempPath()) ('dewarp-a11y-' + [Guid]::NewGuid().ToString('N'))
$readyFile = "$temporaryBase.json"
$stopFile = "$temporaryBase.stop"
$errorFile = "$temporaryBase.stderr"
$arguments = @(('"{0}"' -f $targetFile), ('"{0}"' -f $readyFile))
if ($RunningState) { $arguments += '--running-state' }
else { $arguments += @('--state', $State) }
$child = Start-Process -FilePath $Python -ArgumentList $arguments -WindowStyle Hidden -PassThru -RedirectStandardError $errorFile
try {
    for ($attempt = 0; $attempt -lt 80 -and -not (Test-Path -LiteralPath $readyFile); $attempt++) {
        if ($child.HasExited) { break }
        Start-Sleep -Milliseconds 100
    }
    if (-not (Test-Path -LiteralPath $readyFile)) {
        $details = if (Test-Path -LiteralPath $errorFile) { Get-Content -LiteralPath $errorFile -Raw } else { '' }
        throw "Audit target did not become ready. $details"
    }
    $environment = Get-Content -LiteralPath $readyFile -Raw | ConvertFrom-Json
    $window = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$environment.hwnd)
    $descendants = $window.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
    $elements = @(foreach ($element in $descendants) {
        $current = $element.Current
        $range = $null
        $value = $null
        $patternObject = $null
        if ($element.TryGetCurrentPattern([System.Windows.Automation.RangeValuePattern]::Pattern, [ref]$patternObject)) {
            $range = [ordered]@{value=$patternObject.Current.Value; minimum=$patternObject.Current.Minimum; maximum=$patternObject.Current.Maximum}
        }
        if ($element.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern, [ref]$patternObject)) {
            $value = $patternObject.Current.Value
        }
        [ordered]@{name=$current.Name; type=$current.ControlType.ProgrammaticName; class=$current.ClassName;
            keyboard_focusable=$current.IsKeyboardFocusable; enabled=$current.IsEnabled; offscreen=$current.IsOffscreen;
            patterns=@($element.GetSupportedPatterns() | ForEach-Object {$_.ProgrammaticName}); value=$value; range=$range}
    })
    $checks = @(foreach ($expected in $environment.expected_controls) {
        $matches = @($elements | Where-Object { $_.name -eq $expected.name -and $_.type -eq $expected.type })
        $passed = $matches.Count -eq 1
        if ($passed) {
            $actual = $matches[0]
            if ($expected.PSObject.Properties.Name -contains 'pattern') {
                $passed = ($actual.patterns -contains $expected.pattern) -and ($actual.enabled -eq $expected.enabled) -and ($actual.keyboard_focusable -eq $expected.keyboard_focusable)
            }
            if ($expected.PSObject.Properties.Name -contains 'value') { $passed = $passed -and ($actual.value -eq $expected.value) }
            if ($expected.PSObject.Properties.Name -contains 'range') {
                $passed = $passed -and ($actual.range.value -eq $expected.range.value) -and ($actual.range.minimum -eq $expected.range.minimum) -and ($actual.range.maximum -eq $expected.range.maximum)
            }
        }
        [ordered]@{attribute=$expected.attribute; name=$expected.name; passed=[bool]$passed; matches=$matches.Count}
    })
    $passedCount = @($checks | Where-Object passed).Count
    $importantNames = @($environment.expected_controls | ForEach-Object {$_.name})
    $evidenceElements = @($elements | Where-Object {$_.name -in $importantNames -or $_.type -in @('ControlType.Header', 'ControlType.DataItem')})
    $result = [ordered]@{captured_utc=[DateTime]::UtcNow.ToString('o'); environment=$environment;
        checks=$checks; passed=$passedCount -eq $checks.Count; observed_descendant_count=$elements.Count; elements=$evidenceElements;
        limitation='UIA metadata and pattern checks; screen-reader speech and actual OS contrast themes are not tested.'}
    $absoluteOutput = [IO.Path]::GetFullPath($OutputFile)
    [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($absoluteOutput)) | Out-Null
    $result | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $absoluteOutput -Encoding utf8
    Write-Output "$passedCount/$($checks.Count) UIA control checks passed. Evidence: $absoluteOutput"
    if ($passedCount -ne $checks.Count) { throw 'GUI accessibility checks failed; see the JSON evidence.' }
} finally {
    # Signal only this test process; it also has a 30-second safety timer.
    Set-Content -LiteralPath $stopFile -Value 'stop' -Encoding ascii
    $exited = $child.WaitForExit(5000)
    if ($exited) {
        foreach ($temporaryFile in @($readyFile, $stopFile, $errorFile, "$temporaryBase.pending")) {
            if (Test-Path -LiteralPath $temporaryFile) { Remove-Item -LiteralPath $temporaryFile }
        }
    }
}
