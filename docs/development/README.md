# Development and validation

Read [the working rules](AGENTS.md) before editing or running real-photo tests.
The package uses `app/src/dewarp` with explicit core/GUI boundaries; `cli.py`
serves both the GUI and CLI. `scripts/dewarp.py` is the source-checkout bridge
used by this machine's existing Git Bash `dewarp` alias. It prefers `.venv` and
works independently of the current directory.
After this relocation, existing Git Bash sessions need `source ~/.bashrc` once
to reload the updated alias; new sessions load it automatically.

Run setup from the repository root, then use its interpreter for all checks:

```powershell
.\.venv\Scripts\python.exe tools/run_checks.py
.\.venv\Scripts\python.exe tools/run_gui_audits.py
.\.venv\Scripts\python.exe tools/audit_gui_theme.py
```

For packaging changes, `tools/audit_packaging.py` also verifies launch paths and
builds/installs a clean copied package into owned temporary storage. It needs the
prepared `.venv` and cleans up its wheel/source copies; it does not read photos.

The synthetic suite has a 120-second wall deadline and 60-second inactivity
deadline. GUI audits run serially with their own deadlines and must not compete
with other focus-sensitive GUI tests. They do not access the real-photo fixture.
Theme checks override appearance only inside the test application; they do not
change Windows settings. Native API/geometry checks do not certify a human
screen-reader session or physical mixed-DPI monitor transitions.

GUI tests inject a temporary `SettingsStore` INI file to keep the current user's
saved defaults untouched. Setup and processing are separate stacked pages;
audits must verify the active page, including disabled actions, rather than
counting hidden controls. The [current redesign audit](../gui-screen-redesign.md)
records persistence, navigation, geometry and Windows 10 symbol-font checks.

`app/pyproject.toml` declares compatibility ranges; `app/constraints.txt` pins
the environment installed by the Windows setup. Change pins deliberately and
rerun relevant synthetic, metadata and GUI checks. Keep image/metadata settings
stable unless a verified processing issue warrants a change. Build/install the
package using `pip install -c app/constraints.txt ./app`; the normal setup uses
editable installation so source changes take effect immediately.

This project is maintained in the private repository
https://github.com/ArchAerialData/dji-photo-dewarp. Clone it as its own checkout.
Setup does not initialize Git, create a remote or publish files. Historical audit records contain this
machine's paths and fixture metadata; they are retained as internal evidence.
Do not include ignored `.work`, `.local`, `.venv` or generated photos in a clone.

Historical reports keep their original filenames, source hashes and timings.
Source references named `dewarp_gui.py`, `dewarp_engine.py` and root `dewarp.py`
predate the package move; their current counterparts are `gui/window.py`,
`core/engine.py` and `cli.py`. The old synthetic speed benchmark additionally
requires a preserved original via `--original PATH`; that local baseline is
intentionally not distributed. It is not a routine test for appearance changes.
