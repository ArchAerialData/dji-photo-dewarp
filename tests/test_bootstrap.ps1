# Functional bootstrap checks. Never invokes installers or accesses photo fixtures.
param([Parameter(Mandatory=$true)][string]$Python)
. (Join-Path (Split-Path -Parent $PSScriptRoot) 'scripts\Bootstrap.ps1')
$checks = New-Object 'System.Collections.Generic.List[string]'
function Assert-That([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw "FAILED: $Message" }
    $checks.Add($Message)
}
$probe = Invoke-BootstrapCommand $Python @('-c', 'import json,sys; print(json.dumps(sys.argv[1:]))', 'folder with spaces', 'quote"inside', 'C:\ends with slash\', '', 'amp&and|pipe', 'a\\"b') -Quiet
$parsed = ConvertFrom-Json -InputObject $probe.Output
Assert-That ($parsed.Count -eq 6) 'Native argument count includes empty strings'
Assert-That ($parsed[0] -ceq 'folder with spaces') 'Spaces preserve argument boundaries'
Assert-That ($parsed[1] -ceq 'quote"inside') 'Literal quote survives CRT escaping'
Assert-That ($parsed[2] -ceq 'C:\ends with slash\') 'Trailing backslash is preserved'
Assert-That ($parsed[3] -ceq '') 'Empty argument is preserved'
Assert-That ($parsed[4] -ceq 'amp&and|pipe') 'Shell metacharacters are passed without execution'
Assert-That ($parsed[5] -ceq 'a\\"b') 'Backslashes before quotes are preserved'
$failure = Invoke-BootstrapCommand $Python @('-c', 'import sys; print("expected failure"); sys.exit(7)') -Quiet -AllowFailure
Assert-That ($failure.ExitCode -eq 7 -and $failure.Output.Trim() -eq 'expected failure') 'Exit code and diagnostics are retained'
$threw = $false
try { Invoke-BootstrapCommand $Python @('-c', 'raise SystemExit(9)') -Quiet | Out-Null } catch { $threw = $true }
Assert-That $threw 'Unexpected native failure stops setup'
$stall = Invoke-BootstrapCommand $Python @('-c', 'import time; time.sleep(30)') -WallSeconds 10 -StallSeconds 1 -Quiet -AllowFailure
Assert-That ($stall.ExitCode -eq 124 -and $stall.Timeout -eq 'no-output timeout' -and $stall.ElapsedSeconds -lt 8) 'Stalled child has a bounded no-output deadline'
$wall = Invoke-BootstrapCommand $Python @('-u', '-c', 'import time; [(print(i, flush=True),time.sleep(.2)) for i in range(100)]') -WallSeconds 1 -StallSeconds 10 -Quiet -AllowFailure
Assert-That ($wall.ExitCode -eq 124 -and $wall.Timeout -eq 'wall timeout' -and $wall.ElapsedSeconds -lt 8) 'Output cannot extend the wall deadline'
$child = Invoke-BootstrapCommand $Python @('-u', '-c', 'import subprocess,sys,time; p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(30)"]); print(p.pid,flush=True); time.sleep(30)') -WallSeconds 10 -StallSeconds 1 -Quiet -AllowFailure
$childId = [int]$child.Output.Trim()
Assert-That (-not (Get-Process -Id $childId -ErrorAction SilentlyContinue)) 'Timeout terminates the owned descendant process'
Assert-That ([bool](Find-DewarpPython)) 'Existing supported Python is discovered'
Assert-That ([bool](Find-DewarpExifTool)) 'Existing ExifTool is discovered without installation'
@{ checks = $checks; count = $checks.Count; powershell = $PSVersionTable.PSVersion.ToString(); passed = $true } | ConvertTo-Json -Depth 3
