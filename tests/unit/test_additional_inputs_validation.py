"""
Task 2 - Additional Inputs dialog: custom-value validation and unsaved-changes handling.

The tests drive the real dialog as the real template page (``CustomWindow``) opens it, and
use real keyboard / mouse events (QTest) for user edits.  Only the modal message boxes are
replaced by a recorder so a test can read what was shown and choose which button is pressed.
Viewers that need native packages (3D viewer, matplotlib nav-cube) are stubbed only when
they cannot be imported - they are irrelevant to this dialog.
"""
import sys
import types
from copy import deepcopy

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QCheckBox, QComboBox, QLineEdit, QRadioButton, QWidget,
)

import osdagbridge.core.utils.common as common  # noqa: E402
from osdagbridge.core.utils.common import (  # noqa: E402
    ADDITIONAL_INPUT_CUSTOM_VALUE_FIELDS,
    KEY_CARRIAGEWAY_WIDTH,
    KEY_DESIGN_MODE,
    KEY_DO_CAMBER_MODE,
    KEY_DO_CAMBER_VALUE,
    KEY_INCLUDE_MEDIAN,
    KEY_LL_FOOTPATH_PRESSURE_MODE,
    KEY_LL_FOOTPATH_PRESSURE_VALUE,
    KEY_MD_TYPE,
    KEY_MD_WIDTH,
    KEY_MP_GIRDER_DEPTH,
    KEY_SL_DEAD_LOAD_MODE,
    KEY_SL_DEAD_LOAD_VALUE,
    KEY_SPAN,
    KEY_WC_THICKNESS,
    KEY_WL_GUST_FACTOR_MODE,
    KEY_WL_GUST_FACTOR_VALUE,
    VALUE_CUSTOM,
    VALUES_NO_YES,
)

import osdagbridge.desktop.ui.dialogs.additional_input.additional_inputs as ai_module  # noqa: E402

UNSAVED_TEXT = ("You have unsaved changes. Closing this window will discard them. "
                "Do you want to proceed?")
CHECKBOX_KEY = "design_options_cont.uls.bending_resistance"


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures / helpers
# ──────────────────────────────────────────────────────────────────────────────
def _stub_native_viewers():
    """Replace viewers that need pythonocc / navcube, only if they are missing."""
    def stub(name, **attrs):
        mod = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(mod, k, v)
        sys.modules[name] = mod

    try:
        import osdagbridge.desktop.ui.cad_3d  # noqa: F401
    except Exception:
        class CAD3DWindow(QWidget):
            def clear_3d_cad(self):
                pass
        stub("osdagbridge.desktop.ui.cad_3d", CAD3DWindow=CAD3DWindow)
    try:
        import osdagbridge.desktop.ui.mpl_plot_widget  # noqa: F401
    except Exception:
        class MplPlotWidget(QWidget):
            def set_engineering_scale(self, *_):
                pass

            def clear(self):
                pass
        stub("osdagbridge.desktop.ui.mpl_plot_widget", MplPlotWidget=MplPlotWidget)


class FakeMessageBox:
    """Stands in for CustomMessageBox: records every box and answers with a scripted button."""
    shown = []
    answers = []          # queue of button texts; empty -> "OK"

    def __init__(self, title="", text="", informativeText="", buttons=("OK",), dialogType=None):
        self.record = dict(title=title, text=text, buttons=list(buttons), dialogType=dialogType)
        FakeMessageBox.shown.append(self.record)

    def exec(self):
        return FakeMessageBox.answers.pop(0) if FakeMessageBox.answers else "OK"


@pytest.fixture(scope="module")
def app(request):
    qapp = QApplication.instance() or QApplication([])
    request.config._qt_main_window_built = True   # see conftest: headless teardown workaround
    return qapp


@pytest.fixture(scope="module")
def page(app):
    _stub_native_viewers()
    from osdagbridge.core.bridge_types.plate_girder.ui_fields import FrontendData
    from osdagbridge.desktop.ui.template_page import CustomWindow

    window = CustomWindow("OsdagBridge", FrontendData)
    window.resize(1600, 1000)
    window.show()
    app.processEvents()
    dock = window.input_dock
    for key, value in ((KEY_SPAN, "30"), (KEY_CARRIAGEWAY_WIDTH, "10.5")):
        editor = dock._w(key)
        editor.setText(value)
        editor.editingFinished.emit()
    app.processEvents()
    yield window


class Dlg:
    """The opened dialog plus the helpers the tests share."""

    def __init__(self, app, page, dialog):
        self.app, self.page, self.dlg = app, page, dialog
        self.saved_signals = []
        dialog.update_template_page_2d_cad.connect(self.saved_signals.append)

    # access ------------------------------------------------------------------
    def edit(self, key):
        w = self.dlg.findChild(QLineEdit, key)
        assert w is not None, key
        return w

    def combo(self, key):
        w = self.dlg.findChild(QComboBox, key)
        assert w is not None, key
        return w

    # user actions ------------------------------------------------------------
    def pick(self, key, text):
        """Choose a combo entry with the keyboard, like a user does (emits `activated`)."""
        combo = self.combo(key)
        steps = combo.findText(text) - combo.currentIndex()
        assert combo.findText(text) >= 0, (key, text)
        for _ in range(abs(steps)):
            QTest.keyClick(combo, Qt.Key_Down if steps > 0 else Qt.Key_Up)
        assert combo.currentText() == text
        self.app.processEvents()

    def type_into(self, key, text, replace=True):
        edit = self.edit(key)
        if replace:
            edit.selectAll()
            QTest.keyClick(edit, Qt.Key_Delete)
        QTest.keyClicks(edit, text)
        edit.editingFinished.emit()
        self.app.processEvents()

    def set_custom(self, mode_key, value_key, value):
        self.pick(mode_key, VALUE_CUSTOM)
        self.type_into(value_key, value) if value else self.clear_value(value_key)

    def clear_value(self, key):
        edit = self.edit(key)
        edit.selectAll()
        QTest.keyClick(edit, Qt.Key_Delete)
        self.app.processEvents()

    def save(self):
        FakeMessageBox.shown.clear()
        self.dlg._save_inputs()
        return list(FakeMessageBox.shown)

    def close(self, *answers):
        FakeMessageBox.shown.clear()
        FakeMessageBox.answers[:] = list(answers)
        self.dlg.close()
        self.app.processEvents()
        return list(FakeMessageBox.shown)


@pytest.fixture
def d(app, page, monkeypatch):
    """Dialog opened exactly as the application opens it; everything restored afterwards."""
    monkeypatch.setattr(ai_module, "CustomMessageBox", FakeMessageBox)
    FakeMessageBox.shown.clear()
    FakeMessageBox.answers.clear()
    saved_input_dict = deepcopy(page.input_dict)
    page._show_additional_inputs()
    app.processEvents()
    ctx = Dlg(app, page, page._additional_inputs_dialog)
    yield ctx
    ctx.dlg.update_template_page_2d_cad.disconnect(ctx.saved_signals.append)
    ctx.dlg.is_modified = False
    ctx.dlg.hide()
    page.input_dict.clear()            # the dialog edits this very dict when it saves
    page.input_dict.update(saved_input_dict)
    FakeMessageBox.answers.clear()


def expected_error(name):
    return f"Error: The custom value for '{name}' cannot be empty."


# ──────────────────────────────────────────────────────────────────────────────
# Centralised keys
# ──────────────────────────────────────────────────────────────────────────────
def test_every_declared_pair_resolves_to_real_widgets(d):
    for selector_key, value_key in ADDITIONAL_INPUT_CUSTOM_VALUE_FIELDS:
        assert d.dlg.findChild(QLineEdit, value_key) is not None, value_key
        if selector_key == KEY_DESIGN_MODE:
            continue                      # lives in the input dock, read from the input dict
        combo = d.combo(selector_key)
        assert combo.findText(VALUE_CUSTOM) >= 0, selector_key


def test_no_parameter_strings_are_hardcoded_in_the_validation_loop():
    import inspect
    src = inspect.getsource(ai_module.AdditionalInputs._find_empty_custom_value)
    src += inspect.getsource(ai_module.AdditionalInputs._custom_value_specs)
    assert "ADDITIONAL_INPUT_CUSTOM_VALUE_FIELDS" in src
    assert "typical_section." not in src and "loading." not in src


# ──────────────────────────────────────────────────────────────────────────────
# Feature A - pre-save validation
# ──────────────────────────────────────────────────────────────────────────────
def test_save_with_non_empty_custom_value_succeeds(d):
    d.set_custom(KEY_DO_CAMBER_MODE, KEY_DO_CAMBER_VALUE, "0.5")
    shown = d.save()
    assert [s["title"] for s in shown] == ["Saved"]
    assert d.dlg.default_input_dict[KEY_DO_CAMBER_VALUE] == 0.5
    assert len(d.saved_signals) == 1


def test_save_with_empty_custom_value_is_blocked(d):
    d.set_custom(KEY_DO_CAMBER_MODE, KEY_DO_CAMBER_VALUE, "")
    shown = d.save()
    assert len(shown) == 1
    assert shown[0]["title"] == "Error"
    assert shown[0]["dialogType"] == ai_module.MessageBoxType.Critical
    assert shown[0]["text"] == expected_error("Camber")
    assert d.saved_signals == []


def test_save_with_whitespace_only_custom_value_is_blocked(d):
    d.pick(KEY_DO_CAMBER_MODE, VALUE_CUSTOM)
    d.edit(KEY_DO_CAMBER_VALUE).setText("   \t ")
    shown = d.save()
    assert [s["text"] for s in shown] == [expected_error("Camber")]
    assert d.saved_signals == []


@pytest.mark.parametrize("mode_key, value_key, name", [
    (KEY_DO_CAMBER_MODE,            KEY_DO_CAMBER_VALUE,            "Camber"),
    (KEY_WL_GUST_FACTOR_MODE,       KEY_WL_GUST_FACTOR_VALUE,       "Gust Factor, G"),
    (KEY_LL_FOOTPATH_PRESSURE_MODE, KEY_LL_FOOTPATH_PRESSURE_VALUE, "Footpath Pressure"),
    (KEY_SL_DEAD_LOAD_MODE,         KEY_SL_DEAD_LOAD_VALUE,         "Dead Load for Seismic Force"),
])
def test_error_message_names_the_actual_parameter(d, mode_key, value_key, name):
    d.set_custom(mode_key, value_key, "")
    shown = d.save()
    assert [s["text"] for s in shown] == [expected_error(name)]


def test_validation_covers_several_tabs_and_stops_at_the_first_in_ui_order(d):
    d.set_custom(KEY_DO_CAMBER_MODE, KEY_DO_CAMBER_VALUE, "")                # Design Options tab
    d.set_custom(KEY_WL_GUST_FACTOR_MODE, KEY_WL_GUST_FACTOR_VALUE, "")      # Loading tab
    tabs = [d.dlg.tabs.tabText(i) for i in range(d.dlg.tabs.count())]
    loading_first = (next(i for i, t in enumerate(tabs) if "Loading" in t)
                     < next(i for i, t in enumerate(tabs) if "Design" in t))
    first, second = (("Gust Factor, G", "Camber") if loading_first
                     else ("Camber", "Gust Factor, G"))

    assert [s["text"] for s in d.save()] == [expected_error(first)]          # only one error
    d.type_into(KEY_WL_GUST_FACTOR_VALUE if first == "Gust Factor, G" else KEY_DO_CAMBER_VALUE, "1.2")
    assert [s["text"] for s in d.save()] == [expected_error(second)]
    d.type_into(KEY_DO_CAMBER_VALUE if first == "Gust Factor, G" else KEY_WL_GUST_FACTOR_VALUE, "1.2")
    assert [s["title"] for s in d.save()] == ["Saved"]


def test_non_custom_selection_with_empty_value_field_does_not_block(d):
    d.set_custom(KEY_DO_CAMBER_MODE, KEY_DO_CAMBER_VALUE, "")
    d.pick(KEY_DO_CAMBER_MODE, "Default")                                    # valid, non-custom
    assert d.edit(KEY_DO_CAMBER_VALUE).text().strip() == ""
    assert [s["title"] for s in d.save()] == ["Saved"]


def test_inactive_tab_is_not_validated_until_it_becomes_active(d):
    dlg = d.dlg
    assert dlg.working_input_dict[KEY_INCLUDE_MEDIAN] == VALUES_NO_YES[0]    # median tab disabled
    assert not d.combo(KEY_MD_TYPE).isEnabled()                              # a user cannot edit it either
    d.combo(KEY_MD_TYPE).setCurrentText(VALUE_CUSTOM)                        # so set the state directly
    d.edit(KEY_MD_WIDTH).setText("")
    assert [s["title"] for s in d.save()] == ["Saved"]                       # inactive: ignored

    dlg.working_input_dict[KEY_INCLUDE_MEDIAN] = VALUES_NO_YES[1]
    dlg.refresh_typical_section()                                            # median tab now active
    d.edit(KEY_MD_WIDTH).setText("")      # the app re-fills the width when the tab turns on, so clear it now
    assert [s["text"] for s in d.save()] == [expected_error("Median Width")]


def test_girder_dimension_is_required_only_in_custom_design_mode(d):
    dlg = d.dlg
    d.clear_value(KEY_MP_GIRDER_DEPTH)
    assert [s["title"] for s in d.save()] == ["Saved"]                       # Optimized mode

    dlg.design_mode_trigger("Custom")
    d.edit(KEY_MP_GIRDER_DEPTH).setText("")
    assert [s["text"] for s in d.save()] == [expected_error("Total Depth, d")]
    d.edit(KEY_MP_GIRDER_DEPTH).setText("1500")
    assert [s["title"] for s in d.save()] == ["Saved"]


def test_blocked_save_changes_no_application_state(d, monkeypatch):
    dlg = d.dlg
    d.type_into(KEY_WC_THICKNESS, "77")                                      # a pending valid edit
    d.set_custom(KEY_DO_CAMBER_MODE, KEY_DO_CAMBER_VALUE, "")
    flushed = []
    monkeypatch.setattr(dlg, "_save_stiffener_member_data", lambda *a, **k: flushed.append(a))
    default_before = deepcopy(dlg.default_input_dict)
    working_before = deepcopy(dlg.working_input_dict)

    d.save()

    assert dlg.default_input_dict == default_before       # nothing committed, nothing partial
    assert dlg.working_input_dict == working_before       # nothing even touched
    assert flushed == []                                  # the save pipeline never started
    assert d.saved_signals == []
    assert d.page.input_dict.get(KEY_WC_THICKNESS) != 77


# ──────────────────────────────────────────────────────────────────────────────
# Feature B - modified-state tracking
# ──────────────────────────────────────────────────────────────────────────────
def test_new_dialog_starts_unmodified(app):
    _stub_native_viewers()
    fresh = ai_module.AdditionalInputs()
    assert fresh.is_modified is False
    fresh.deleteLater()


def test_opening_the_dialog_does_not_mark_it_modified(d):
    assert d.dlg.is_modified is False


def test_programmatic_changes_do_not_mark_modified(d):
    dlg = d.dlg
    dlg.findChild(QComboBox, KEY_DO_CAMBER_MODE).setCurrentText(VALUE_CUSTOM)
    dlg.findChild(QLineEdit, KEY_DO_CAMBER_VALUE).setText("1.5")
    dlg.findChild(QCheckBox, CHECKBOX_KEY).setChecked(
        not dlg.findChild(QCheckBox, CHECKBOX_KEY).isChecked())
    d.app.processEvents()
    assert dlg.is_modified is False


def test_reloading_after_user_edits_gives_a_clean_dialog(d):
    d.type_into(KEY_WC_THICKNESS, "66")
    assert d.dlg.is_modified is True
    d.page._show_additional_inputs()
    d.app.processEvents()
    assert d.dlg.is_modified is False


def test_user_dropdown_change_is_tracked(d):
    d.pick(KEY_DO_CAMBER_MODE, VALUE_CUSTOM)
    assert d.dlg.is_modified is True


def test_picking_the_same_dropdown_entry_again_is_not_a_change(d):
    combo = d.combo(KEY_DO_CAMBER_MODE)
    combo.activated.emit(combo.currentIndex())
    assert d.dlg.is_modified is False


def test_user_text_edit_is_tracked(d):
    d.type_into(KEY_WC_THICKNESS, "55")
    assert d.dlg.is_modified is True


def test_user_checkbox_change_is_tracked(d):
    box = d.dlg.findChild(QCheckBox, CHECKBOX_KEY)
    box.click()          # Qt's own user-click path (pressed/released/clicked); the page is not laid out for a mouse hit
    assert d.dlg.is_modified is True


def test_user_radio_button_change_is_tracked(d):
    dlg = d.dlg
    radio = QRadioButton("probe", dlg.tabs.widget(0))     # the dialog has none today; the tracker is generic
    dlg._wire_modification_tracking()
    dlg._wire_modification_tracking()                     # idempotent: no duplicate connections
    dlg._capture_modified_baseline()
    QTest.mouseClick(radio, Qt.LeftButton)
    assert radio.isChecked() and dlg.is_modified is True
    dlg.is_modified = False
    QTest.mouseClick(radio, Qt.LeftButton)                # already selected: not a change
    assert dlg.is_modified is False


def test_change_to_a_custom_value_field_is_tracked(d):
    d.pick(KEY_WL_GUST_FACTOR_MODE, VALUE_CUSTOM)
    d.dlg.is_modified = False
    QTest.keyClicks(d.edit(KEY_WL_GUST_FACTOR_VALUE), "2")
    assert d.dlg.is_modified is True


def test_tracking_connects_each_widget_only_once(d):
    edit = d.edit(KEY_WC_THICKNESS)
    for _ in range(3):
        d.dlg._wire_modification_tracking()
    calls = []
    original = d.dlg._on_user_edit
    d.dlg._on_user_edit = lambda w: calls.append(w)      # lambdas look this attribute up at call time
    QTest.keyClicks(edit, "1")
    d.dlg._on_user_edit = original
    assert len(calls) == 1


def test_defaults_button_that_changes_nothing_does_not_mark_modified(d, monkeypatch):
    monkeypatch.setattr(d.dlg, "reset_active_tab_defaults", lambda: None)
    d.dlg._on_defaults_clicked()
    assert d.dlg.is_modified is False


def test_defaults_button_that_rewrites_values_marks_modified(d):
    # The existing reset really changes inputs on the Typical Section tab (e.g. railing width).
    d.dlg._on_defaults_clicked()
    assert d.dlg.is_modified is True


def test_successful_save_resets_modified(d):
    d.type_into(KEY_WC_THICKNESS, "44")
    assert d.dlg.is_modified is True
    assert [s["title"] for s in d.save()] == ["Saved"]
    assert d.dlg.is_modified is False


def test_failed_save_keeps_modified(d):
    d.set_custom(KEY_DO_CAMBER_MODE, KEY_DO_CAMBER_VALUE, "")
    assert d.dlg.is_modified is True
    assert [s["title"] for s in d.save()] == ["Error"]
    assert d.dlg.is_modified is True


def test_edits_after_a_save_mark_modified_again(d):
    d.type_into(KEY_WC_THICKNESS, "44")
    d.save()
    d.type_into(KEY_WC_THICKNESS, "45")
    assert d.dlg.is_modified is True


# ──────────────────────────────────────────────────────────────────────────────
# Feature C - closeEvent
# ──────────────────────────────────────────────────────────────────────────────
def test_closing_an_unmodified_dialog_shows_no_warning(d):
    assert d.dlg.isVisible()
    shown = d.close()
    assert shown == []
    assert not d.dlg.isVisible()


def test_closing_a_modified_dialog_shows_the_standard_warning(d):
    d.type_into(KEY_WC_THICKNESS, "33")
    shown = d.close("Cancel")
    assert len(shown) == 1
    assert shown[0]["text"] == UNSAVED_TEXT
    assert shown[0]["buttons"] == ["Discard", "Cancel"]
    assert shown[0]["dialogType"] == ai_module.MessageBoxType.Warning


def test_discard_closes_without_saving(d):
    original = d.edit(KEY_WC_THICKNESS).text()
    saved_before = deepcopy(d.dlg.default_input_dict)
    d.type_into(KEY_WC_THICKNESS, "33")
    d.close("Discard")
    assert not d.dlg.isVisible()
    assert d.dlg.is_modified is False
    assert d.dlg.default_input_dict == saved_before            # nothing saved
    assert d.saved_signals == []                               # preview/page never told
    assert d.edit(KEY_WC_THICKNESS).text() == original         # reused dialog shows the saved state


def test_cancel_keeps_the_dialog_open_with_values_and_modified_state(d):
    d.set_custom(KEY_DO_CAMBER_MODE, KEY_DO_CAMBER_VALUE, "0.7")
    d.type_into(KEY_WC_THICKNESS, "33")
    d.close("Cancel")
    assert d.dlg.isVisible()
    assert d.dlg.is_modified is True
    assert d.combo(KEY_DO_CAMBER_MODE).currentText() == VALUE_CUSTOM
    assert d.edit(KEY_DO_CAMBER_VALUE).text() == "0.7"
    assert d.edit(KEY_WC_THICKNESS).text() == "33"


def test_dismissing_the_warning_without_a_choice_keeps_the_dialog_open(d):
    d.type_into(KEY_WC_THICKNESS, "33")
    d.close(None)                                              # e.g. the box's own X button
    assert d.dlg.isVisible() and d.dlg.is_modified is True


def test_user_can_keep_editing_and_save_after_cancel(d):
    d.type_into(KEY_WC_THICKNESS, "33")
    d.close("Cancel")
    assert [s["title"] for s in d.save()] == ["Saved"]
    assert d.close() == []                                     # clean now: closes silently
    assert not d.dlg.isVisible()


def test_title_bar_close_button_uses_the_same_guard(d):
    d.type_into(KEY_WC_THICKNESS, "33")
    FakeMessageBox.shown.clear()
    FakeMessageBox.answers[:] = ["Cancel"]
    QTest.mouseClick(d.dlg.title_bar.btn_close, Qt.LeftButton)
    assert [s["text"] for s in FakeMessageBox.shown] == [UNSAVED_TEXT]
    assert d.dlg.isVisible()


def test_escape_key_uses_the_same_guard(d):
    d.type_into(KEY_WC_THICKNESS, "33")
    FakeMessageBox.shown.clear()
    FakeMessageBox.answers[:] = ["Cancel"]
    d.dlg.reject()                                             # what QDialog does for Esc
    assert [s["text"] for s in FakeMessageBox.shown] == [UNSAVED_TEXT]
    assert d.dlg.isVisible()
    FakeMessageBox.answers[:] = ["Discard"]
    d.dlg.reject()
    assert not d.dlg.isVisible()


def test_close_after_discard_and_reopen_shows_saved_values(d):
    d.type_into(KEY_WC_THICKNESS, "33")
    d.close("Discard")
    d.page._show_additional_inputs()
    d.app.processEvents()
    assert d.dlg.isVisible() and d.dlg.is_modified is False
    assert d.edit(KEY_WC_THICKNESS).text() != "33"


# ──────────────────────────────────────────────────────────────────────────────
# Regression
# ──────────────────────────────────────────────────────────────────────────────
def test_valid_save_still_commits_edits_and_updates_the_preview(d):
    d.type_into(KEY_WC_THICKNESS, "88")
    shown = d.save()
    assert [s["title"] for s in shown] == ["Saved"]
    assert shown[0]["dialogType"] == ai_module.MessageBoxType.Success
    assert float(d.dlg.default_input_dict[KEY_WC_THICKNESS]) == 88.0
    assert len(d.saved_signals) == 1


def test_saved_values_survive_reopening(d):
    d.type_into(KEY_WC_THICKNESS, "91")
    d.save()
    d.page._show_additional_inputs()
    d.app.processEvents()
    assert float(d.edit(KEY_WC_THICKNESS).text()) == 91.0
    assert d.dlg.is_modified is False


def test_all_central_keys_still_import():
    assert common.VALUE_CUSTOM == "Custom"
    assert len(set(ADDITIONAL_INPUT_CUSTOM_VALUE_FIELDS)) == len(ADDITIONAL_INPUT_CUSTOM_VALUE_FIELDS)
