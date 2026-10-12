# Working on the dewarper

Be concise and pragmatic. Cite local evidence or primary sources for claims,
verify behavior directly, and favor maintainable changes.

## Reusable real-photo fixture

The user-provided fixture is `K:\Temp-Dewarp-Test-Fixture\Original Input JPGs`.
It contains three photo folders plus flight-data sidecars. Treat the entire
original tree as read-only. Never process in place, rename, edit metadata, or
save test outputs under that tree. Use verified copies in uniquely owned
scratch space, remove generated photos/outputs after tests, and verify original
file paths, sizes, modification times, and SHA-256 hashes before finishing.

Real-photo runs are opt-in and appropriate only for changes that affect image
processing or workflows. Do not run a full fixture for cosmetic GUI changes,
documentation changes, or routine tasks. Prefer a representative sample first;
use the full fixture only when its size or content is relevant to the change.
`python tools/test_real_fixture.py` samples twelve evenly spaced JPEGs per
folder. `--full` is an explicit additional choice, never the routine default.

Preserve per-folder and overall timings, settings, source-code hashes, and
validation results in `docs/benchmarks/`. Keep original before/after manifests.
Retain JSON/log evidence, not generated photos. Never broadly delete the
fixture parent; cleanup must verify the exact owned scratch path and marker.

## Bounded checks

- Synthetic suite: `python tools/run_checks.py` (120 seconds wall, 60 seconds
  without output). It does not access the real fixture.
- Native GUI audits: `python tools/run_gui_audits.py` (240 seconds wall and
  180 seconds without output per audit). Run these serially with GUI tests to
  avoid competing keyboard focus. They do not process photos.
- Real-fixture runner: 600 seconds wall and 60 seconds without progress per
  processing scenario by default; hashing, staging, verification, and cleanup
  also have bounded subprocesses. Adjust explicit limits only for justified
  larger runs. Report any timeout, failed verification, or incomplete cleanup.
- For other test/benchmark commands, use `tools.validation_support.run_bounded`
  with explicit wall and no-output deadlines rather than an unbounded launch.
  On Windows it owns a kill-on-close Job Object for its process tree.

Preserve the existing `dewarp` alias and stable `scripts/dewarp.py` entry point. GUI
changes must retain fixed Start/Cancel controls, keyboard access, native
accessibility interfaces, and the documented scaling behavior. Automated API
checks do not substitute for an actual screen-reader or contrast-theme session.

## Package and appearance

Keep `Dewarp.cmd` as the only visible root file, with Git metadata as the
necessary dotfile exception. Runtime environments and logs stay ignored. Use
`.venv/Scripts/python.exe` for checks. Keep Windows setup dependencies pinned in
`app/constraints.txt`. Appearance belongs to Qt system styles, with the user's
requested blue browsing and green positive-action button accents. Keep these
styles scoped to the buttons, disabled actions grey, and clear accent styles
when Windows high contrast is active. Do not write OS themes or replace native
titlebar controls. This project is internal;
no license or publication is requested.
