"""
Model side of the 2D CAD double-click editing feature.

``CadParameterSync`` is the *handler* the CAD canvases call (see
``cad_label_editor``).  It does not own any data and has no rendering code:

  * **Validation** reuses ``BridgeInputValidator`` (the same rules the input
    dock and the Additional Inputs dialog apply).  Only checks the validator
    does not make (plain-decimal notation, NaN/inf, layout feasibility of a new
    girder spacing) are added here.

  * **Commit** reuses the existing pathways, so nothing new has to be kept in
    sync:

      - Span / Carriageway Width are *main dock* fields.  The dock's own
        ``QLineEdit`` is updated and ``InputDock._on_field_edited`` is called
        - exactly what happens when the user leaves that field - which updates
        ``input_dict`` and emits ``input_value_changed`` once.

      - Girder Spacing / Deck Thickness are *additional inputs*: they only
        exist in ``input_dict`` (the Additional Inputs dialog re-reads that dict
        every time it opens).  They are written there and the same
        ``input_value_changed`` signal is emitted.

    ``input_value_changed`` is connected by the template page (``CustomWindow``) to
    ``update_cad_from_inputs`` -> ``BridgeDualCADWidget.update_from_osdag_inputs``,
    i.e. the existing redraw mechanism for both 2D views.
"""

from __future__ import annotations

import copy
import math
import re
from typing import Optional

from PySide6.QtWidgets import QLineEdit

from osdagbridge.core.bridge_types.plate_girder.defaults import solve_extend_basic_input_dict
from osdagbridge.core.bridge_types.plate_girder.initial_sizing import BridgeConfigurationSolver
from osdagbridge.core.utils.common import (
    CAD_EDITABLE_DIMENSION_KEYS,
    DEFAULT_CRASH_BARRIER_WIDTH,
    DEFAULT_RAILING_WIDTH,
    KEY_CARRIAGEWAY_WIDTH,
    KEY_CB_WIDTH,
    KEY_FOOTPATH,
    KEY_MD_WIDTH,
    KEY_RL_WIDTH,
    KEY_SPAN,
    KEY_TS_DECK_OVERHANG,
    KEY_TS_DECK_THICKNESS,
    KEY_TS_FOOTPATH_WIDTH,
    KEY_TS_GIRDER_SPACING,
    KEY_TS_NO_OF_GIRDERS,
    KEY_TS_OVERALL_WIDTH,
)
from osdagbridge.desktop.ui.utils.cad_label_editor import EditResult

# key -> (display name, unit, decimals used when showing the stored value)
_PARAMETERS = {
    KEY_CARRIAGEWAY_WIDTH: ("Carriageway Width", "m", 2),
    KEY_TS_GIRDER_SPACING: ("Girder Spacing", "m", 2),
    KEY_TS_DECK_THICKNESS: ("Deck Thickness", "mm", 0),
    KEY_SPAN: ("Span Length", "m", 1),
}

# Parameters that are fields of the main input dock (the rest are additional inputs).
_BASIC_KEYS = (KEY_SPAN, KEY_CARRIAGEWAY_WIDTH)
_ADDITIONAL_KEYS = (KEY_TS_GIRDER_SPACING, KEY_TS_DECK_THICKNESS)

# Plain decimal notation, like the dock's QDoubleValidator(StandardNotation).
_NUMBER_RE = re.compile(r"[+-]?([0-9]+(\.[0-9]*)?|\.[0-9]+)", re.ASCII)
_CANONICAL_RE = re.compile(r"[0-9]+(\.[0-9]+)?", re.ASCII)

_MSG_NEEDS_BASIC = (
    "Enter a valid Span and Carriageway Width in the input dock before "
    "editing this dimension."
)
_MSG_LOCKED = "\U0001F512 Unlock to Edit"


class CadParameterSync:
    """Validates a value typed on a CAD label and commits it to the bridge inputs."""

    def __init__(self, input_dock):
        self.dock = input_dock

    # ── data access ──────────────────────────────────────────────────────────
    @property
    def input_dict(self) -> dict:
        """The one shared input dictionary (owned by the template page, ``CustomWindow``)."""
        return self.dock.parent.input_dict

    # ── DimensionEditHandler API ─────────────────────────────────────────────
    def can_edit(self, key: str) -> bool:
        return key in CAD_EDITABLE_DIMENSION_KEYS and not self.dock.is_locked

    def locked_reason(self, key: str) -> str:
        return _MSG_LOCKED if self.dock.is_locked else ""

    def tooltip(self, key: str) -> str:
        name, unit, _ = _PARAMETERS[key]
        return f"{name} ({unit}) \u2013 Enter to apply, Esc to cancel"

    def current_text(self, key: str) -> str:
        """Current value as the main UI shows it (empty when not entered yet)."""
        if key in _BASIC_KEYS:
            widget = self.dock._w(key)
            if isinstance(widget, QLineEdit):
                return widget.text().strip()
            value = self.input_dict.get(key)
            return "" if value is None else str(value).strip()

        value = self.input_dict.get(key)
        if value in (None, ""):
            return ""
        try:
            number = float(value)
        except (TypeError, ValueError):
            return str(value)
        decimals = _PARAMETERS[key][2]
        text = f"{number:.{decimals}f}"
        if abs(float(text) - number) > 1e-9:       # don't hide precision
            text = f"{number:.6g}"
        return text

    def validate(self, key: str, text: str) -> EditResult:
        """Check ``text`` without touching any state."""
        if key not in _PARAMETERS:
            return EditResult(False, "This dimension cannot be edited.")
        name = _PARAMETERS[key][0]
        raw = (text or "").strip()

        # 1. Must be a plain finite number (also covers empty / non-numeric).
        value = self._parse_number(raw)
        if value is None:
            message = self._validator_message(key, {**self.input_dict, key: raw})
            return EditResult(False, message or f"{name} must be a numeric value.")

        # 2. Additional inputs need a solved layout (span + carriageway width).
        solve_first = False
        work = self.input_dict
        if key in _ADDITIONAL_KEYS:
            solve_first = self._layout_needs_solve(work)
            if solve_first:
                work = copy.deepcopy(work)
                try:
                    solve_extend_basic_input_dict(work)
                except Exception:
                    return EditResult(False, _MSG_NEEDS_BASIC)

        # 3. The project's own range rules (reused, not re-implemented).
        trial = {**work, key: raw}
        message = self._validator_message(key, trial)
        if message:
            return EditResult(False, message)

        # 4. Zero / negative dimensions are never valid.
        if value <= 0:
            return EditResult(False, f"{name} must be greater than zero.")

        text_out = raw if _CANONICAL_RE.fullmatch(raw) else f"{value:.10g}"

        # 5. Work out what has to be written.
        updates: dict = {}
        if key == KEY_TS_GIRDER_SPACING:
            try:
                layout = self._solve_spacing(work, value)
            except ValueError as exc:
                return EditResult(False, str(exc))
            # Same four values AdditionalInputs.update_girder_layout() writes.
            updates = {
                KEY_TS_GIRDER_SPACING: layout.girder_spacing,
                KEY_TS_DECK_OVERHANG: layout.deck_overhang,
                KEY_TS_NO_OF_GIRDERS: layout.no_of_girders,
                KEY_TS_OVERALL_WIDTH: layout.overall_width,
            }
        elif key == KEY_TS_DECK_THICKNESS:
            # Same storage convention as AdditionalInputs._update_input_dict.
            updates = {key: int(value) if value.is_integer() else value}

        return EditResult(True, value=value, text=text_out,
                          payload={"solve_first": solve_first, "updates": updates})

    def commit(self, key: str, result: EditResult) -> None:
        """Apply a successfully validated edit and trigger the existing redraw."""
        if key in _BASIC_KEYS:
            self._commit_basic(key, result.text)
        else:
            self._commit_additional(result.payload)

    def show_error(self, message: str) -> None:
        """Show a validation message with the app's standard dialog."""
        from osdagbridge.desktop.ui.dialogs.custom_messagebox import (
            CustomMessageBox,
            MessageBoxType,
        )
        CustomMessageBox(
            title="Input Error",
            text=message,
            dialogType=MessageBoxType.Warning,
        ).exec()

    # ── commit paths ─────────────────────────────────────────────────────────
    def _commit_basic(self, key: str, text: str) -> None:
        widget = self.dock._w(key)
        if isinstance(widget, QLineEdit):
            # Set the field silently, then run the field's own "editing finished"
            # handler once: it updates input_dict and emits input_value_changed.
            widget.blockSignals(True)
            widget.setText(text)
            widget.blockSignals(False)
            self.dock.reset_error_state(widget)
            self.dock._on_field_edited(key, widget)
        else:
            self.dock._update_input_dict(key, text)
            self.dock.is_require_field_changed = True
            self.dock.input_value_changed.emit()

    def _commit_additional(self, payload: dict) -> None:
        data = self.input_dict
        if payload["solve_first"]:
            # Same call CustomWindow.update_cad_from_inputs makes for a pending change.
            solve_extend_basic_input_dict(data)
            self.dock.is_require_field_changed = False
        # is_require_field_changed is deliberately NOT set: it would make the
        # next refresh re-derive (and overwrite) the value that was just edited.
        data.update(payload["updates"])
        self.dock.input_value_changed.emit()

    # ── helpers ──────────────────────────────────────────────────────────────
    @staticmethod
    def _parse_number(raw: str) -> Optional[float]:
        if not _NUMBER_RE.fullmatch(raw):
            return None
        value = float(raw)
        return value if math.isfinite(value) else None

    def _validator_message(self, key: str, trial: dict) -> str:
        """Message from the existing validators, or '' when the value is valid."""
        validator = self.dock.validator
        if key in _BASIC_KEYS:
            result = validator.validate_basic_inputs(key, trial)
        else:
            result = validator.validate_additional_inputs(key, trial)
        return result[1] if result else ""

    def _layout_needs_solve(self, data: dict) -> bool:
        """True when the derived layout in ``data`` is missing or out of date."""
        if self.dock.is_require_field_changed:
            return True                      # a basic input changed since the last solve
        required = (KEY_TS_OVERALL_WIDTH, KEY_TS_NO_OF_GIRDERS,
                    KEY_TS_GIRDER_SPACING, KEY_TS_DECK_OVERHANG)
        return any(data.get(k) in (None, "") for k in required)

    @staticmethod
    def _solve_spacing(data: dict, spacing: float):
        """Overhang for a new spacing with the girder count fixed (existing solver)."""
        def num(key, default=0.0):
            try:
                v = data.get(key)
                return float(v) if v not in (None, "") else default
            except (TypeError, ValueError):
                return default

        railing = num(KEY_RL_WIDTH, DEFAULT_RAILING_WIDTH)
        footpath = str(data.get(KEY_FOOTPATH, "None")).strip()
        n_footpaths = 2 if "Both" in footpath else (0 if footpath in ("None", "") else 1)

        solver = BridgeConfigurationSolver(
            carriageway_width=num(KEY_CARRIAGEWAY_WIDTH),
            crash_barrier_width=num(KEY_CB_WIDTH, DEFAULT_CRASH_BARRIER_WIDTH),
            footpath_width=num(KEY_TS_FOOTPATH_WIDTH, 0.0),
            railing_width=railing / 1000.0 if railing > 10 else railing,
            median_width=num(KEY_MD_WIDTH, 0.0),
            n_footpaths=n_footpaths,
        )
        return solver._solve_layout(
            no_of_girders=int(num(KEY_TS_NO_OF_GIRDERS, 2)),
            girder_spacing=spacing,
            deck_overhang=num(KEY_TS_DECK_OVERHANG, 0.0),
            changed_field="spacing",
        )
