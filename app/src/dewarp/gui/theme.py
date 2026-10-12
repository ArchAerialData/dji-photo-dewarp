"""Follow the user's platform appearance, including native window decorations.

Windows' legacy Vista Qt style replaces the system palette with a light one.
Qt's Windows 11 style and Fusion support the platform's light/dark and contrast
palettes. Qt also updates DWM title bars when that effective palette changes;
duplicating that logic with registry polling or custom caption colors can break
contrast themes. Keep colors, fonts and window frames owned by the platform.
"""
from __future__ import annotations

import sys

from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication, QStyleFactory


def _prepare_icon_font() -> None:
    """Resolve Windows 11 style symbols on Windows 10 without installing fonts."""
    families = {name.casefold() for name in QFontDatabase.families()}
    if "segoe fluent icons" not in families and "segoe mdl2 assets" in families:
        # Qt's style disables font merging and otherwise falls back to Tahoma,
        # which has none of its private-use symbols. MDL2 contains this app's
        # arrows and binary/menu checkmarks. Do this before the style creates
        # QFont objects; leave installed Fluent and the user's text font alone.
        QFont.insertSubstitution("Segoe Fluent Icons", "Segoe MDL2 Assets")


def install_system_theme(application: QApplication) -> None:
    """Configure appearance once, before creating the first application window.

    Non-Windows desktops retain their own platform style. Do not set a palette,
    stylesheet, font, or an explicit light/dark scheme: Qt tracks user changes.
    The application attribute makes creating additional windows idempotent.
    """
    if getattr(application, "_dewarp_system_theme_installed", False):
        return
    if sys.platform == "win32":
        _prepare_icon_font()
        for name in ("windows11", "Fusion"):
            style = QStyleFactory.create(name)
            if style is not None:
                application.setStyle(style)
                break
        else:
            raise RuntimeError("Qt's Windows theme styles are unavailable. Re-run setup to repair PySide6.")
    # Keep the wrapper alive along with the application, rather than relying on
    # temporary Python ownership of the platform's shared hints object.
    application._dewarp_style_hints = application.styleHints()
    application._dewarp_style_hints.unsetColorScheme()
    application._dewarp_system_theme_installed = True
