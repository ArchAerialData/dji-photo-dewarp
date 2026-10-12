"""Real Windows PowerShell 5.1 bootstrap probes, without installing anything."""
import os
import json
from pathlib import Path
import shutil
import sys
import unittest

from tools.validation_support import cleanup_scratch, create_scratch, run_bounded


@unittest.skipUnless(os.name == "nt", "Windows bootstrap")
class BootstrapTests(unittest.TestCase):
    def test_powershell_arguments_failures_and_timeouts(self):
        root = Path(__file__).resolve().parents[1]
        powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        result = run_bounded(
            [powershell, "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
             root / "tests/test_bootstrap.ps1", "-Python", sys.executable],
            cwd=root, timeout=45, stall_timeout=30)
        self.assertIsNone(result["timeout"], result)
        self.assertEqual(result["exit_code"], 0, result)
        self.assertIn('"passed":  true', "\n".join(result["tail"]))

    def test_setup_branches_without_installing_system_software(self):
        """Run the actual orchestration with only native installers replaced."""
        root = Path(__file__).resolve().parents[1]
        parent = root / ".work"
        parent.mkdir(exist_ok=True)
        scratch, token = create_scratch(parent)
        powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        try:
            scripts = scratch / "scripts"
            scripts.mkdir()
            shutil.copy2(root / "scripts/Setup.ps1", scripts / "Setup.ps1")
            mock = r'''
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$script:DewarpRoot = Split-Path -Parent $PSScriptRoot
$script:installed = @{}
function Test-DewarpEnvironment { return $env:DEWARP_TEST_CASE -eq 'ready' }
function Update-DewarpPath { }
function Add-DewarpExifToolPath { }
function Find-DewarpPython {
    if ($env:DEWARP_TEST_CASE -ne 'missing' -or $script:installed.ContainsKey('Python.Python.3.13')) { return 'MockPython.exe' }
    return $null
}
function Find-DewarpExifTool {
    if ($env:DEWARP_TEST_CASE -ne 'missing' -or $script:installed.ContainsKey('OliverBetz.ExifTool')) { return 'MockExifTool.exe' }
    return $null
}
function Get-Command { return [pscustomobject]@{ Source = 'MockWinget.exe' } }
function Get-DewarpManifestHash { return 'verified-mock-manifest' }
function Invoke-BootstrapCommand {
    param($FilePath, $Arguments, $WallSeconds, $StallSeconds, $LogPath)
    @{executable=$FilePath; arguments=@($Arguments); wall=$WallSeconds; stall=$StallSeconds} | ConvertTo-Json -Compress |
        Add-Content -LiteralPath (Join-Path $script:DewarpRoot 'commands.jsonl')
    if ($env:DEWARP_TEST_CASE -eq 'fail') { throw 'simulated native installer failure' }
    if ($FilePath -eq 'MockWinget.exe') { $script:installed[$Arguments[2]] = $true }
    return [pscustomobject]@{ ExitCode=0; Output='OK' }
}
'''
            (scripts / "Bootstrap.ps1").write_text(mock, encoding="utf-8")
            wrapper = scripts / "TestCase.ps1"
            wrapper.write_text("param([string]$Case)\n$env:DEWARP_TEST_CASE=$Case\n& (Join-Path $PSScriptRoot 'Setup.ps1')\n", encoding="utf-8")
            for case in ("missing", "existing", "ready", "fail"):
                with self.subTest(case=case):
                    commands_path = scratch / "commands.jsonl"
                    commands_path.unlink(missing_ok=True)
                    state_path = scratch / ".local/environment.json"
                    state_path.unlink(missing_ok=True)
                    result = run_bounded([powershell, "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
                                          "-File", wrapper, "-Case", case], cwd=scratch, timeout=20, stall_timeout=15)
                    self.assertIsNone(result["timeout"], result)
                    commands = [json.loads(line) for line in commands_path.read_text().splitlines()] if commands_path.exists() else []
                    self.assertTrue(all(c["wall"] > 0 and c["stall"] > 0 for c in commands))
                    if case == "fail":
                        self.assertNotEqual(result["exit_code"], 0, result)
                        self.assertFalse(state_path.exists())
                        continue
                    self.assertEqual(result["exit_code"], 0, result)
                    if case == "ready":
                        self.assertEqual(commands, [])
                        continue
                    self.assertTrue(state_path.exists())
                    installs = [c for c in commands if c["executable"] == "MockWinget.exe"]
                    self.assertEqual(len(installs), 2 if case == "missing" else 0)
                    if installs:
                        self.assertEqual([c["arguments"][2] for c in installs], ["Python.Python.3.13", "OliverBetz.ExifTool"])
                        for command in installs:
                            arguments = command["arguments"]
                            self.assertEqual(arguments[arguments.index("--scope") + 1], "user")
                            self.assertIn("--exact", arguments)
                            self.assertIn("--disable-interactivity", arguments)
                    pip = next(c for c in commands if "pip" in c["arguments"])
                    self.assertIn("--constraint", pip["arguments"])
                    self.assertIn("--only-binary=:all:", pip["arguments"])
        finally:
            cleanup_scratch(scratch, parent, token)


if __name__ == "__main__":
    unittest.main()
