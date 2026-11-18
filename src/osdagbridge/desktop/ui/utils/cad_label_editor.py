"""
Inline editing of 2D CAD dimension labels (double-click to edit).

This module is the *canvas side* of the feature.  It knows nothing about the
bridge model: it only

  1. records where each dimension label was painted (``register_dimension_label``),
  2. detects a double-click on one of those labels,
  3. shows a single temporary ``QLineEdit`` over the label, and
  4. hands the typed text to a *handler* that validates and commits it.

The handler (see ``cad_parameter_sync.CadParameterSync``) is the *model side*:
it validates the text with the project's existing validators and writes the
value back through the normal input pathway.

A label is identified by the input key of the parameter it shows (e.g.
``KEY_SPAN``).  Which keys are editable / locked is declared centrally in
``core/utils/common.py`` (``CAD_EDITABLE_DIMENSION_KEYS`` and
``CAD_LOCKED_DIMENSION_KEYS``).

Editor behaviour
----------------
* Enter          -> validate; commit and close if valid, otherwise show the
                    message and keep the editor open.
* Click outside  -> same as Enter (commit if valid, otherwise keep it open so
                    the value can be corrected - like a spreadsheet cell).
* Escape         -> cancel; nothing is changed.
* Only one editor can exist at any time, across all canvases.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Protocol

from PySide6.QtCore import QEvent, QObject, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QLineEdit, QToolTip, QWidget

from osdagbridge.core.utils.common import (
    CAD_EDITABLE_DIMENSION_KEYS,
    CAD_LOCKED_DIMENSION_KEYS,
)

# Extra pixels around a painted label that still count as a hit.
LABEL_HIT_PADDING = 3.0

_EDITOR_MIN_WIDTH = 84
_EDITOR_MAX_WIDTH = 150
_EDITOR_HEIGHT = 24

_EDITOR_STYLE = """
    QLineEdit#cad_dimension_editor {
        background: #ffffff;
        color: #000000;
        border: 1px solid #90AF13;
        border-radius: 2px;
        padding: 1px 4px;
        selection-background-color: #90AF13;
        selection-color: #ffffff;
    }
    QLineEdit#cad_dimension_editor[error="true"] {
        border: 1px solid #d32f2f;
    }
"""


# ──────────────────────────────────────────────────────────────────────────────
# Handler contract
# ──────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class EditResult:
    """Outcome of validating a typed value."""

    ok: bool
    message: str = ""          # user-facing reason when ``ok`` is False
    value: Optional[float] = None
    text: str = ""             # normalised text that will be written to the UI
    payload: Any = None        # handler-private data carried from validate -> commit


class DimensionEditHandler(Protocol):
    """What a canvas needs from the model side."""

    def can_edit(self, key: str) -> bool: ...
    def locked_reason(self, key: str) -> str: ...
    def tooltip(self, key: str) -> str: ...
    def current_text(self, key: str) -> str: ...
    def validate(self, key: str, text: str) -> EditResult: ...
    def commit(self, key: str, result: EditResult) -> None: ...
    def show_error(self, message: str) -> None: ...


# ──────────────────────────────────────────────────────────────────────────────
# The temporary editor widget
# ──────────────────────────────────────────────────────────────────────────────
class DimensionLineEdit(QLineEdit):
    """Line edit that reports Enter / Escape / focus loss as signals."""

    commit_requested = Signal()
    cancel_requested = Signal()
    focus_lost = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("cad_dimension_editor")
        self.setAlignment(Qt.AlignCenter)
        self.setFont(QFont("Arial", 9))
        self.setStyleSheet(_EDITOR_STYLE)
        self.textEdited.connect(self._clear_error)

    def set_error(self, on: bool) -> None:
        self.setProperty("error", bool(on))
        self.style().unpolish(self)
        self.style().polish(self)

    def _clear_error(self, *_):
        if self.property("error"):
            self.set_error(False)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            event.accept()
            self.commit_requested.emit()
            return
        if event.key() == Qt.Key_Escape:
            event.accept()
            self.cancel_requested.emit()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        # Window deactivation or a popup (context menu, message box) is not a
        # "click outside"; the editor keeps waiting for the user.
        if event.reason() in (Qt.ActiveWindowFocusReason, Qt.PopupFocusReason):
            return
        self.focus_lost.emit()


class _OutsideClickFilter(QObject):
    """
    Application-wide filter, installed only while an editor is open, that reports
    a mouse press anywhere outside the editor.  Qt's focus-out alone is not
    enough: clicking a widget that does not take focus (the canvas, a toolbar
    button, a scroll bar) never removes focus from the editor.  The press is
    never consumed - the click still does what it normally does.
    """

    def __init__(self, editor: QWidget, on_outside_click):
        super().__init__(editor)
        self._editor = editor
        self._on_outside_click = on_outside_click

    def eventFilter(self, obj, event):
        if event.type() == QEvent.MouseButtonPress and isinstance(obj, QWidget):
            if QApplication.activePopupWidget() is None:        # ignore the editor's own menu
                if obj is not self._editor and not self._editor.isAncestorOf(obj):
                    self._on_outside_click()
        return False


# ──────────────────────────────────────────────────────────────────────────────
# Mixin for the CAD canvases
# ──────────────────────────────────────────────────────────────────────────────
class DimensionEditMixin:
    """
    Adds label hit-testing and inline editing to a ``QWidget`` canvas.

    Usage (the canvas class)::

        class MyCanvas(DimensionEditMixin, QWidget):
            def __init__(self, parent=None):
                super().__init__(parent)
                self.init_dimension_editing()

            def paintEvent(self, event):
                self.begin_dimension_label_pass()      # forget last frame's labels
                ...                                    # draw; every editable/locked
                ...                                    # label calls register_dimension_label

    Editing stays disabled until ``set_dimension_edit_handler`` is called, so
    canvases that are only previews (report images, dialogs) are unaffected.
    """

    # The widget that currently owns the (single) editor, application-wide.
    _active_editor_owner: "Optional[DimensionEditMixin]" = None

    # ── setup ────────────────────────────────────────────────────────────────
    def init_dimension_editing(self) -> None:
        self._dimension_label_hits: list[tuple[QRectF, str]] = []
        self._dimension_edit_handler: Optional[DimensionEditHandler] = None
        self._dimension_editor: Optional[DimensionLineEdit] = None
        self._dimension_edit_key: Optional[str] = None
        self._dimension_edit_original: str = ""
        self._dimension_edit_busy: bool = False      # a commit/validation is in progress
        self._dimension_resolve_pending: bool = False
        self._dimension_click_filter: Optional[_OutsideClickFilter] = None

    def set_dimension_edit_handler(self, handler: Optional[DimensionEditHandler]) -> None:
        """Enable (handler) or disable (None) double-click editing on this canvas."""
        if handler is None:
            self.cancel_dimension_edit()
        self._dimension_edit_handler = handler

    # ── label registry (filled while painting) ───────────────────────────────
    def begin_dimension_label_pass(self) -> None:
        self._dimension_label_hits = []

    def register_dimension_label(self, rect: QRectF, key: Optional[str]) -> None:
        if key:
            self._dimension_label_hits.append((QRectF(rect), key))

    def dimension_label_at(self, pos: QPointF) -> Optional[tuple[QRectF, str]]:
        """Top-most label under ``pos`` (later painted == on top), or None."""
        pad = LABEL_HIT_PADDING
        for rect, key in reversed(self._dimension_label_hits):
            if rect.adjusted(-pad, -pad, pad, pad).contains(pos):
                return rect, key
        return None

    # ── events ───────────────────────────────────────────────────────────────
    def mouseDoubleClickEvent(self, event):
        handler = self._dimension_edit_handler
        if handler is None or event.button() != Qt.LeftButton:
            super().mouseDoubleClickEvent(event)
            return

        hit = self.dimension_label_at(event.position())
        if hit is None:
            # Not on a dimension label: behave exactly as before.
            super().mouseDoubleClickEvent(event)
            return

        rect, key = hit
        event.accept()

        if key in CAD_LOCKED_DIMENSION_KEYS or key not in CAD_EDITABLE_DIMENSION_KEYS:
            self._show_hint("Derived value \u2013 it cannot be edited here.", event)
            return
        if not handler.can_edit(key):
            self._show_hint(handler.locked_reason(key), event)
            return

        self.start_dimension_edit(key, rect)

    def hideEvent(self, event):
        self.cancel_dimension_edit()
        super().hideEvent(event)

    def _show_hint(self, text: str, event) -> None:
        if text:
            QToolTip.showText(event.globalPosition().toPoint(), text, self)

    # ── editor lifecycle ─────────────────────────────────────────────────────
    @property
    def dimension_editor(self) -> Optional[DimensionLineEdit]:
        """The open editor, or None (mainly for tests/diagnostics)."""
        return self._dimension_editor

    @property
    def dimension_edit_key(self) -> Optional[str]:
        return self._dimension_edit_key

    def start_dimension_edit(self, key: str, label_rect: QRectF) -> Optional[DimensionLineEdit]:
        """Open the inline editor for ``key`` over ``label_rect``."""
        handler = self._dimension_edit_handler
        if handler is None:
            return None

        # Only one editor at a time - in this canvas and in any other.
        self.cancel_dimension_edit()
        owner = DimensionEditMixin._active_editor_owner
        if owner is not None and owner is not self:
            owner.cancel_dimension_edit()

        original = handler.current_text(key) or ""

        editor = DimensionLineEdit(self)
        editor.setToolTip(handler.tooltip(key))
        editor.setText(original)
        editor.setGeometry(self._editor_geometry(label_rect))
        editor.commit_requested.connect(self._on_editor_commit_requested)
        editor.cancel_requested.connect(self.cancel_dimension_edit)
        editor.focus_lost.connect(self._on_editor_focus_lost)

        click_filter = _OutsideClickFilter(editor, self._on_editor_focus_lost)
        QApplication.instance().installEventFilter(click_filter)
        self._dimension_click_filter = click_filter

        self._dimension_editor = editor
        self._dimension_edit_key = key
        self._dimension_edit_original = original
        DimensionEditMixin._active_editor_owner = self

        editor.show()
        editor.raise_()
        editor.setFocus(Qt.OtherFocusReason)
        editor.selectAll()          # typing replaces the old value immediately
        return editor

    def cancel_dimension_edit(self) -> None:
        """Close the editor without committing anything (Escape)."""
        editor = getattr(self, "_dimension_editor", None)
        if editor is None:
            return
        self._close_editor()
        self.update()

    def _close_editor(self) -> None:
        editor = self._dimension_editor
        click_filter = self._dimension_click_filter
        self._dimension_editor = None
        self._dimension_edit_key = None
        self._dimension_edit_original = ""
        self._dimension_click_filter = None
        if click_filter is not None:
            QApplication.instance().removeEventFilter(click_filter)
        if DimensionEditMixin._active_editor_owner is self:
            DimensionEditMixin._active_editor_owner = None
        if editor is not None:
            editor.blockSignals(True)       # hiding fires focusOut - ignore it
            editor.hide()
            editor.deleteLater()

    def _editor_geometry(self, rect: QRectF) -> QRect:
        width = int(max(_EDITOR_MIN_WIDTH, min(rect.width() + 24, _EDITOR_MAX_WIDTH)))
        center = rect.center()
        x = int(center.x() - width / 2)
        y = int(center.y() - _EDITOR_HEIGHT / 2)
        x = max(2, min(x, self.width() - width - 2))
        y = max(2, min(y, self.height() - _EDITOR_HEIGHT - 2))
        return QRect(x, y, width, _EDITOR_HEIGHT)

    # ── commit paths ─────────────────────────────────────────────────────────
    def _on_editor_commit_requested(self) -> None:
        self._try_commit()

    def _on_editor_focus_lost(self) -> None:
        """Focus left the editor, or the user pressed the mouse outside it."""
        # Resolve after the current event so a message box opened by an invalid
        # value is not shown from inside a focus/mouse event.  One click can be
        # reported several times (focus-out + one press per ancestor widget),
        # so only one resolve is ever queued.
        if self._dimension_resolve_pending:
            return
        self._dimension_resolve_pending = True
        QTimer.singleShot(0, self._resolve_focus_loss)

    def _resolve_focus_loss(self) -> None:
        self._dimension_resolve_pending = False
        editor = self._dimension_editor
        if editor is None or self._dimension_edit_busy:
            return
        self._try_commit()

    def _try_commit(self) -> bool:
        """Validate and commit. Returns True when the editor was closed."""
        editor, key, handler = self._dimension_editor, self._dimension_edit_key, self._dimension_edit_handler
        if editor is None or key is None or handler is None or self._dimension_edit_busy:
            return False

        self._dimension_edit_busy = True        # a message box below must not re-enter
        try:
            text = editor.text().strip()

            # Nothing was changed: close quietly, no recalculation.
            if text and text == self._dimension_edit_original.strip():
                self._close_editor()
                self.update()
                return True

            result = handler.validate(key, text)
            if not result.ok:
                editor.set_error(True)
                handler.show_error(result.message)
                if self._dimension_editor is editor:    # still open: let the user fix it
                    editor.setFocus(Qt.OtherFocusReason)
                    editor.selectAll()
                return False

            self._close_editor()
            try:
                handler.commit(key, result)
            except Exception as exc:                    # never leave a half-applied edit silent
                handler.show_error(f"Could not apply the new value: {exc}")
            self.update()
            return True
        finally:
            self._dimension_edit_busy = False
