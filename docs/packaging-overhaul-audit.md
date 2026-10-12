# Appearance and packaging overhaul

Scope: system appearance, portable setup, package organization and launch paths.
The real-photo fixture was not processed for these changes. Processing math,
JPEG settings and metadata handling are unchanged; the relocated engine retains
SHA-256 `ee374ea47d04add43668af21a2f7dca64c2eb8a2b73ca13461a649d407838082`.
Previous photo comparisons and timings remain in the
[fixture audit](fixture-overhaul-audit.md).

## Findings and changes

| Finding | Resolution and evidence |
| --- | --- |
| Legacy Windows Vista style painted a light GUI despite the current user's dark preference. | Select Qt's system-aware Windows style, with Fusion fallback. Preserve native fonts, colors and chrome. [Theme audit](system-theme-audit.md) |
| Appearance must cover native dialogs and window controls. | Verify real HWND dark-mode attributes for main/modal/message windows and inspect the composed current-user dark title bar. No registry writes or custom caption controls. [62 checks](system-theme-audit.json) |
| New style exposes a one-pixel scroll-boundary issue in six minimum-window cases. | Reveal the full styled control rectangle, not only Qt's input-method cursor region. Added a regression; all 288 cases now pass. [Initial evidence](gui-audit-theme-before-scroll-fix.json), [final matrix](gui-audit-after.json) |
| Root contained multiple launchers, implementation files and dependency lists. | Root `Dewarp.cmd` is the visible launcher; Python package in `app`, installers in `scripts`, historical files in `archive`, guidance in `docs`, README in `.github`. Git metadata is the necessary root dotfile exception. |
| Installation depended on this machine's global Python packages. | Isolated `.venv`, package metadata and exact tested dependency constraints. Setup probes working Python and ExifTool before using WinGet; it logs failures and has explicit timeouts. |
| Broad dependency ranges could silently replace validated image-processing libraries. | Windows setup installs `app/constraints.txt`; both constraints and package metadata determine whether setup needs repair. |
| A caller's unrelated `dewarp.py` could shadow `python -m dewarp`. | Launch through the isolated interpreter mode; retain the caller directory for relative input paths. Verify from a separate directory containing a deliberate shadow module. |
| Windows PowerShell 5.1 could fail the final setup marker when `Get-FileHash` was unavailable through the inherited module path. | Use .NET SHA-256 directly. Retain the failed initial install log and successful repaired run evidence. |
| Setup process supervision originally had a gap between process start and job assignment. | Start the owned process suspended, attach it to its kill-on-close job, then resume. Timeout tests verify descendant cleanup. |
| Existing Git Bash alias referenced the old root source path. | Update only that alias to `scripts/dewarp.py`, which selects this checkout's environment. [External-directory alias verification](alias-packaged.json) |
| A naive alias subprocess bridge would kill the CLI on Ctrl+C before it finished graceful cancellation. | Wait through the bridge's console interrupt while the child finishes its own cancellation handler; regression verifies that the bridge does not kill or terminate it. |
| The historical Tk audit default pointed to the now-Qt GUI. | Require an explicit historical `--source`; current GUI audits use the package source. |

## Verification and scope

Final checks on October 10, 2026:

| Check | Result | Evidence |
| --- | --- | --- |
| Synthetic processing, GUI, theme, bootstrap and cleanup regressions | 56 passed, 18.607 seconds test runtime; 19.386 seconds whole process | [Result](validation-packaged.json), [log](validation-packaged.log) |
| DPI/text/window/state matrix | 288 passed, no clipped critical buttons or unreachable actions; 16 oversized requests were normally clamped by Windows | [Matrix](gui-audit-after.json) |
| Native accessibility | 21 idle + 21 running controls passed | [Idle](accessibility-after.json), [running](accessibility-running.json) |
| Terminal GUI states | 32 passed | [Audit runner](gui-audit-packaged.json) |
| Native appearance | 62 API/palette checks passed; current-user dark chrome visually verified | [Theme audit](system-theme-audit.md) |
| Windows bootstrap | 14 native checks plus four mocked setup branches passed inside the synthetic suite | [Test implementation](../tests/test_bootstrap.ps1), [suite log](validation-packaged.log) |
| Package/CLI portability | 9 passed, including copied-source wheel build, independent installation, exact dependency versions, outside-directory launch and shadow-module isolation | [Portable audit](benchmarks/portable-install-audit.json) |
| Actual isolated dependency installation | Passed, 10.93 seconds after the recorded hash-probe fix | [Setup success](benchmarks/setup-install-final.json) |
| Repeat setup | Passed in 1.22 seconds, no dependency changes | [Idempotence](benchmarks/setup-idempotent.json) |
| Existing Git Bash alias | Passed from outside the project | [Alias result](alias-packaged.json) |
| Actual root launcher, no arguments | 10 checks passed in 3.31 seconds: visible GUI/Start button, current-user dark native chrome, launcher and graceful GUI exit code 0, empty error log | [GUI launch](benchmarks/launcher-gui-audit.json), [bounded run and cleanup](benchmarks/launcher-gui-run.json) |

Every check has finite timeouts. Setup tests use synthetic inputs and mock only
the missing-dependency installer branches, since this machine already has Python
and ExifTool. Existing system installations are not reinstalled for testing.
The no-argument launcher audit traces the owned CMD/PowerShell/venv redirector
ancestry to the actual Qt process, retaining its native handle for exit-code
verification. Initial redirector/title-encoding/handle-lifetime assumptions in
the new audit were corrected; no production change was needed for GUI launch.

The [theme report](system-theme-audit.md) distinguishes actual current-user dark
appearance from process-local light/dark overrides. Actual OS light/contrast
changes, spoken screen-reader use and physical monitor transitions remain manual
checks. These limits are retained rather than claiming complete accessibility
certification from automated checks.

No GitHub publication, remote creation, license addition or unrelated parent
repository modification was performed. This is prepared for internal use.

Setup follows Microsoft's [WinGet installation options](https://learn.microsoft.com/en-us/windows/package-manager/winget/install)
and the verified [ExifTool user-scope manifest](https://github.com/microsoft/winget-pkgs/blob/master/manifests/o/OliverBetz/ExifTool/13.41/OliverBetz.ExifTool.installer.yaml).
The launcher's [Python isolated mode](https://docs.python.org/3.13/using/cmdline.html#cmdoption-I)
prevents caller-directory and `PYTHONPATH` imports from replacing the application.
