# DJI Photo Dewarp

Internal Windows application for correcting DJI JPEGs from their embedded lens
calibration, with a folder queue, safe output naming, metadata preservation and
live progress. Source photos are never overwritten by batch processing.

## Start

Clone or extract the project into a writable local folder, then double-click
**`Dewarp.cmd`**, the launcher in the root. First launch runs setup; later launches
use this checkout's isolated `.venv` environment. Windows 10/11 x64 is the target.
Use Python 3.10–3.13; setup installs Python 3.13 for the current user if needed.

Setup installs the tested Python dependencies from `app/constraints.txt` and
checks ExifTool. If it is missing, setup uses WinGet to install
`OliverBetz.ExifTool` for the current user. A pip ExifTool wrapper would still
need that executable. Internet access and WinGet (Microsoft App Installer) are
needed for missing dependencies. Existing working Python and ExifTool are reused.
Setup does not change global Python packages.

For explicit setup or diagnostics, run from the project folder:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\Setup.ps1
.\Dewarp.cmd --console
.\Dewarp.cmd --help
```

Setup and GUI diagnostics are saved under `.local/logs`. Downloads/installations
have finite wall and inactivity deadlines; setup errors identify the log. If a
checkout is moved, rerun setup to repair its environment. Do not copy `.venv`
between machines. Runtime environments, logs and local scratch are Git-ignored.

## Use

Add folders or drag several into the queue, choose an output parent, then click
**Start batch**. All JPG/JPEG subfolders are scanned. A chosen output parent
contains the original input folder names and relative photo paths. Blank output
uses `Input name - Dewarped Photos` inside each input. Existing output names
get numbered suffixes; previous outputs are excluded from recursive scans. A dedicated processing screen shows the current
folder and whole batch, outcomes and elapsed time. Cancel stays at the bottom
left; Start or Back to setup stays at the bottom right. **File → Settings**
saves processing defaults for your user; XMP preservation and edge cropping
are enabled by default. Return to setup after a run without reopening the app.

The GUI follows the current user's Windows light/dark preference, including
native title bars and dialogs. Qt owns system fonts, colors and contrast-theme
handling; the View menu adds optional text enlargement.
Browsing buttons are blue, Start is green, and unavailable actions are grey.
Windows high contrast retains native colors.
[Current workflow and layout verification](../docs/gui-screen-redesign.md)

```powershell
.\Dewarp.cmd "D:\Photos\Gorgon LGL" "D:\Photos\Flight B" --output "E:\Dewarped" --copy-xmp --report
```

See the [user guide](../docs/user-guide.md),
[measured performance](../docs/performance.md),
[theme verification](../docs/system-theme-audit.md), and
[development guide](../docs/development/README.md).
The [overhaul findings and verification](../docs/packaging-overhaul-audit.md)
record the setup, launch, theme and accessibility checks.

## Project layout

| Location | Purpose |
| --- | --- |
| `Dewarp.cmd` | Windows setup and GUI/CLI launcher |
| `app/` | Python package configuration, dependency constraints and source |
| `app/src/dewarp/core/` | Image correction, metadata and batch execution |
| `app/src/dewarp/gui/` | Qt window, layout and system appearance |
| `scripts/` | Setup, process supervision and stable alias launcher |
| `tests/` | Synthetic regression checks |
| `tools/` | Bounded checks, accessibility audits and opt-in benchmarks |
| `docs/` | User/developer guidance and retained audit evidence |
| `archive/` | Historical standalone scripts; not current launchers |

This is an internal project; no license or public publication is added.
The README lives here to keep the root launcher obvious; GitHub
[recognizes `.github/README.md`](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-readmes).
