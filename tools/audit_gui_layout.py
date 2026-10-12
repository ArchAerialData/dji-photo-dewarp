"""Reproducible Tk geometry audit, without changing display settings or photos.

Run from the repository: python tools/audit_gui_layout.py --output docs/gui-audit.json
The transparent, mapped windows exercise actual Tk geometry. Requested client
sizes deliberately override the application's minimum to model limited desktops.
This is a geometry check, not a keyboard or screen-reader certification
or a substitute for testing real per-monitor Windows DPI transitions.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import platform
import sys
import time
import tkinter as tk
from tkinter import font as tkfont, ttk

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app" / "src"))


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def rect(widget):
    return (widget.winfo_rootx(), widget.winfo_rooty(),
            widget.winfo_width(), widget.winfo_height())


def containment(widget):
    """Check the entire widget against every parent's actual client bounds."""
    x, y, w, h = rect(widget)
    problems = []
    if not widget.winfo_ismapped():
        problems.append("unmapped")
    parent = widget.master
    while parent is not None:
        px, py, pw, ph = rect(parent)
        sides = []
        if x < px:
            sides.append("left")
        if y < py:
            sides.append("top")
        if x + w > px + pw:
            sides.append("right")
        if y + h > py + ph:
            sides.append("bottom")
        if sides:
            problems.append(f"{parent.winfo_class()}:{','.join(sides)}")
        parent = parent.master
    return {"visible": not problems, "rect": [x, y, w, h], "clipped_by": problems}


def label_text(widget):
    try:
        variable = str(widget.cget("textvariable"))
        if variable:
            return str(widget.getvar(variable))
        return str(widget.cget("text"))
    except tk.TclError:
        return ""


def scroll_ancestors(widget):
    found = []
    parent = widget.master
    while parent is not None:
        if isinstance(parent, tk.Canvas):
            found.append(parent)
        parent = parent.master
    return list(reversed(found))


def reveal(widget, root):
    """Move only ancestor scroll areas; do not alter layout or widget sizes."""
    for canvas in scroll_ancestors(widget):
        region = canvas.bbox("all")
        if region:
            position = canvas.canvasy(widget.winfo_rooty() - canvas.winfo_rooty())
            target = position - (canvas.winfo_height() - widget.winfo_height()) / 2
            total_height = max(1, region[3] - region[1])
            canvas.yview_moveto(max(0, target - region[1]) / total_height)
            root.update_idletasks()


def set_data(app, running):
    # Synthetic paths are display values only; the audit performs no I/O to them.
    folder = Path("C:/Survey Projects/Extremely Long Customer Organization Name/2026")
    folder /= "Aerial inspection and comprehensive photogrammetry mission with extended descriptive details"
    folder /= "Gorgon LGL - Southern Section With An Exceptionally Long Input Folder Name"
    app.folders = [folder, folder.parent / "Queued next"]
    for index in range(2):
        app.tree.insert("", "end", iid=str(index), values=(app.folders[index].name,
            "Processing" if running and index == 0 else "Queued", 99999, 100000,
            f"{app.folders[index].name} - Dewarped Photos"))
    app.tree.selection_set("0")
    app.output_root.set("C:/All Dewarped Output Photos/Customer Archive/Long Output Root")
    app._update_selection()
    app.overview_text.set("999 completed   ·   999 queued   ·   1 processing   ·   999 with issues   ·   999 cancelled   ·   999 empty")
    app._started_at = time.monotonic()
    app._set_running(running)
    if running:
        app.current_text.set(f"Folder 1000 of 9999 · {folder.name}")
        app.current_file_text.set("Latest: DJI_20261010153000_1000000_A_" + "ExtendedMissionIdentifier_" * 8 + ".JPG")
        app._set_current_counts(dict(applied=999999, already=999999, skipped=999999,
                                    failed=999999, remaining=999999))
        app.batch_text.set("Batch · 99.9% · 9,999,999 / 10,000,000 photos processed")
        app.time_text.set("Elapsed: 100:00:00   •   Rate: 999.9 photos/min   •   ETA: 100:00:00")
        app.current_bar["value"] = 99.9
        app.batch_bar["value"] = 99.9


def inspect(root, app, dpi, font_scale, size, running):
    root.update_idletasks()
    initial_critical = {name: containment(getattr(app, name))
                        for name in ("start_button", "cancel_button")}
    hidden_actions = []
    for widget in descendants(root):
        if widget.winfo_class() not in ("TButton", "TCheckbutton", "TEntry", "TCombobox", "TSpinbox"):
            continue
        reveal(widget, root)
        result = containment(widget)
        if not result["visible"]:
            hidden_actions.append({"widget": str(widget), "class": widget.winfo_class(),
                                   "text": label_text(widget)[:90], **result})
    # Footer actions must remain visible at both scroll extremes.
    for canvas in (w for w in descendants(root) if isinstance(w, tk.Canvas)):
        canvas.yview_moveto(1)
    root.update_idletasks()
    bottom_critical = {name: containment(getattr(app, name))
                       for name in ("start_button", "cancel_button")}
    label_issues = []
    for widget in descendants(root):
        if widget.winfo_class() != "TLabel" or not label_text(widget):
            continue
        reveal(widget, root)
        result = containment(widget)
        width, height = widget.winfo_width(), widget.winfo_height()
        horizontal = any("left" in p or "right" in p for p in result["clipped_by"])
        # A wrapped label's requested size is the correct content dimensions.
        if horizontal or width < widget.winfo_reqwidth() or height < widget.winfo_reqheight():
            label_issues.append({"text": label_text(widget)[:100], "rect": result["rect"],
                "requested": [widget.winfo_reqwidth(), widget.winfo_reqheight()],
                "clipped_by": result["clipped_by"]})
    style = ttk.Style(root)
    tree_style = str(app.tree.cget("style")) or "Treeview"
    tree_font = style.lookup(tree_style, "font") or "TkDefaultFont"
    line_height = tkfont.Font(root=root, font=tree_font).metrics("linespace")
    row_height = int(style.lookup(tree_style, "rowheight") or 20)
    for canvas in (w for w in descendants(root) if isinstance(w, tk.Canvas)):
        canvas.yview_moveto(0)
    root.update_idletasks()
    return {"dpi": dpi, "tk_scaling": round(float(root.tk.call("tk", "scaling")), 4),
        "text_scale": font_scale, "requested_client": list(size),
        "actual_client": [root.winfo_width(), root.winfo_height()],
        "state": "running" if running else "idle", "critical_initial": initial_critical,
        "critical_scrolled_bottom": bottom_critical, "unreachable_actions": hidden_actions,
        "label_issues": label_issues, "tree_row_height": row_height,
        "tree_font_linespace": line_height, "tree_text_fits": row_height >= line_height + 4,
        "queue_has_horizontal_scroll": bool(app.tree.cget("xscrollcommand"))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True,
                        help="Original Tk GUI module to audit (for baseline reproduction after Qt migration)")
    parser.add_argument("--quick", action="store_true", help="Only 96/192/288 DPI and 640x480/1100x820")
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location("dewarp_tk_audit_target", args.source)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load GUI source: {args.source}")
    target = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(target)
    DewarpApp = target.DewarpApp
    results = []
    callback_errors = []
    calibration = tk.Tk()
    calibration.withdraw()
    calibration.tk.call("tk", "scaling", 96 / 72)
    baseline_font_sizes = {name: tkfont.nametofont(name, root=calibration).cget("size")
                           for name in tkfont.names(calibration)}
    calibration.destroy()
    dpis = (96, 192, 288) if args.quick else (96, 120, 144, 192, 240, 288)
    sizes = ((640, 480), (1100, 820)) if args.quick else ((640, 480), (800, 600), (1024, 768), (1100, 820))
    for dpi in dpis:
        for text_scale in (1.0, 1.5):
            root = tk.Tk()
            root.withdraw()
            root.attributes("-alpha", 0.0)
            root.tk.call("tk", "scaling", dpi / 72)
            # Windows Tk derives native font point sizes from the interpreter's
            # current scaling. Use one 96-DPI baseline across interpreters so
            # changing DPI cannot silently shrink the next case's point sizes.
            for name in tkfont.names(root):
                font = tkfont.nametofont(name, root=root)
                size = baseline_font_sizes[name]
                font.configure(size=round(size * text_scale))
            root.report_callback_exception = lambda kind, value, tb: callback_errors.append(f"{kind.__name__}: {value}")
            app = DewarpApp(root)
            declared_minimum = list(root.minsize())
            root.minsize(1, 1)
            root.deiconify()
            for size in sizes:
                root.geometry(f"{size[0]}x{size[1]}+0+0")
                for running in (False, True):
                    app.tree.delete(*app.tree.get_children())
                    set_data(app, running)
                    # Configure events can schedule reflow after the first idle.
                    for _ in range(4):
                        root.update()
                    case = inspect(root, app, dpi, text_scale, size, running)
                    case["declared_minimum"] = declared_minimum
                    results.append(case)
            for callback in root.tk.call("after", "info"):
                root.after_cancel(callback)
            root.destroy()
    failures = sum(not item["visible"] for case in results for position in
        ("critical_initial", "critical_scrolled_bottom") for item in case[position].values())
    payload = {"method": "Actual Tk widget rectangles, every ancestor clipping boundary, transparent mapped windows; no image processing or display setting changes.",
        "limitations": ["tk scaling simulates font/widget sizing; not a real monitor transition test.",
            "Client dimensions override the application minimum to emulate constrained desktops.",
            "Does not certify Windows Narrator/UI Automation or high-contrast rendering."],
        "environment": {"python": platform.python_version(), "platform": platform.platform(),
                        "tk": tk.TkVersion, "baseline_named_font_sizes": baseline_font_sizes,
                        "gui_sha256": hashlib.sha256(args.source.read_bytes()).hexdigest()},
        "summary": {"cases": len(results), "critical_visibility_failures": failures,
            "cases_with_unreachable_actions": sum(bool(case["unreachable_actions"]) for case in results),
            "cases_with_label_issues": sum(bool(case["label_issues"]) for case in results),
            "cases_with_short_tree_rows": sum(not case["tree_text_fits"] for case in results),
            "callback_errors": callback_errors}, "cases": results}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload["summary"], indent=2))
    return int(bool(failures or callback_errors or payload["summary"]["cases_with_unreachable_actions"] or payload["summary"]["cases_with_short_tree_rows"]))


if __name__ == "__main__":
    raise SystemExit(main())
