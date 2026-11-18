"""
Task 1 - interactive 2D CAD parameter editing (double-click a dimension label).

The tests drive the real template page (``CustomWindow``) with the real input
dock, real 2D CAD canvases and real validators; mouse and keyboard input is
simulated with QTest.  Only the two heavy viewers that need native packages
(3D viewer, matplotlib nav-cube widget) are replaced - and only when they cannot
be imported - because they are irrelevant to the 2D CAD.
"""
import sys
import types

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from osdagbridge.core.utils.common import (  # noqa: E402
    CAD_EDITABLE_DIMENSION_KEYS,
    CAD_LOCKED_DIMENSION_KEYS,
    KEY_CARRIAGEWAY_WIDTH,
    KEY_CB_WIDTH,
    KEY_MP_CB_SPACING,
    KEY_SPAN,
    KEY_TS_DECK_OVERHANG,
    KEY_TS_DECK_THICKNESS,
    KEY_TS_GIRDER_SPACING,
    KEY_TS_NO_OF_GIRDERS,
    KEY_TS_OVERALL_WIDTH,
    SPAN_MAX,
    SPAN_MIN,
)

BASE_SPAN, BASE_CW = "30", "10.5"


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


@pytest.fixture(scope="module")
def app(request):
    qapp = QApplication.instance() or QApplication([])
    request.config._qt_main_window_built = True
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
    yield window


class Ctx:
    """Convenience bundle handed to every test."""

    def __init__(self, app, page):
        self.app, self.page = app, page
        self.dock = page.input_dock
        self.cs = page.cad_comp_widget.cross_section_widget
        self.tv = page.cad_comp_widget.top_view_widget
        self.sync = page.cad_parameter_sync
        self.errors = []
        self.sync.show_error = self.errors.append          # never open a modal box in tests
        self.redraws = 0
        self.dock.input_value_changed.connect(self._count)

    def _count(self):
        self.redraws += 1

    # inputs ------------------------------------------------------------------
    def set_basic(self, span=BASE_SPAN, cw=BASE_CW):
        """Fill the main dock the way a user does (also re-derives the layout)."""
        for key, value in ((KEY_SPAN, span), (KEY_CARRIAGEWAY_WIDTH, cw)):
            editor = self.dock._w(key)
            editor.setText(value)
            editor.editingFinished.emit()
        self.app.processEvents()
        self.repaint()

    def repaint(self):
        self.cs.repaint()
        self.tv.repaint()
        self.app.processEvents()

    @property
    def d(self):
        return self.page.input_dict

    # canvas interaction ------------------------------------------------------
    def label_rect(self, canvas, key):
        for rect, k in canvas._dimension_label_hits:
            if k == key:
                return rect
        raise AssertionError(f"label {key} was not painted")

    def dclick(self, canvas, key):
        QTest.mouseDClick(canvas, Qt.LeftButton, Qt.NoModifier,
                          self.label_rect(canvas, key).center().toPoint())
        self.app.processEvents()
        return canvas.dimension_editor

    def type_and_enter(self, editor, text):
        editor.setText(text)
        QTest.keyClick(editor, Qt.Key_Return)
        self.app.processEvents()


@pytest.fixture
def ctx(app, page):
    c = Ctx(app, page)
    c.dock.is_locked = False
    c.set_basic()
    c.errors.clear()
    c.redraws = 0
    yield c
    for canvas in (c.cs, c.tv):
        canvas.cancel_dimension_edit()
    c.dock.input_value_changed.disconnect(c._count)


EDITABLE_CASES = [
    # (label key, canvas attribute, expected editor text in the base state)
    (KEY_CARRIAGEWAY_WIDTH, "cs", BASE_CW),
    (KEY_TS_GIRDER_SPACING, "cs", None),
    (KEY_TS_DECK_THICKNESS, "cs", None),
    (KEY_SPAN, "tv", BASE_SPAN),
    (KEY_TS_GIRDER_SPACING, "tv", None),          # same parameter, top view label
]
LOCKED_CASES = [
    (KEY_TS_DECK_OVERHANG, "cs"),
    (KEY_TS_OVERALL_WIDTH, "cs"),
    (KEY_MP_CB_SPACING, "tv"),
]


# ──────────────────────────────────────────────────────────────────────────────
# 1-7  Which labels open an editor
# ──────────────────────────────────────────────────────────────────────────────
def test_declared_key_groups_do_not_overlap():
    assert set(CAD_EDITABLE_DIMENSION_KEYS).isdisjoint(CAD_LOCKED_DIMENSION_KEYS)
    assert set(CAD_EDITABLE_DIMENSION_KEYS) == {
        KEY_CARRIAGEWAY_WIDTH, KEY_TS_GIRDER_SPACING, KEY_TS_DECK_THICKNESS, KEY_SPAN}


@pytest.mark.parametrize("key, canvas_attr, expected", EDITABLE_CASES)
def test_double_click_on_editable_label_opens_prefilled_selected_editor(ctx, key, canvas_attr, expected):
    canvas = getattr(ctx, canvas_attr)
    editor = ctx.dclick(canvas, key)

    assert editor is not None and canvas.dimension_edit_key == key
    if expected is not None:
        assert editor.text() == expected                      # shows the current value
    else:
        assert editor.text() == ctx.sync.current_text(key) != ""
    assert editor.selectedText() == editor.text()             # typing replaces it
    # appears over the label it belongs to, fully inside the canvas
    label = ctx.label_rect(canvas, key)
    assert abs(editor.geometry().center().x() - label.center().x()) <= 2
    assert canvas.rect().contains(editor.geometry())


@pytest.mark.parametrize("key, canvas_attr", LOCKED_CASES)
def test_double_click_on_locked_label_does_not_open_editor(ctx, key, canvas_attr):
    canvas = getattr(ctx, canvas_attr)
    assert ctx.dclick(canvas, key) is None
    assert canvas.dimension_editor is None


def test_double_click_on_empty_canvas_does_not_open_editor(ctx):
    for canvas in (ctx.cs, ctx.tv):
        QTest.mouseDClick(canvas, Qt.LeftButton, Qt.NoModifier, QPoint(3, 3))
        ctx.app.processEvents()
        assert canvas.dimension_editor is None


def test_only_left_button_opens_editor(ctx):
    pos = ctx.label_rect(ctx.cs, KEY_SPAN if False else KEY_TS_DECK_THICKNESS).center().toPoint()
    QTest.mouseDClick(ctx.cs, Qt.RightButton, Qt.NoModifier, pos)
    assert ctx.cs.dimension_editor is None


def test_canvas_without_handler_is_not_editable(app):
    """Preview/report canvases never get a handler, so they must stay inert."""
    from PySide6.QtCore import QRectF
    from osdagbridge.desktop.ui.docks.cad_cross_section import CrossSectionCADWidget
    canvas = CrossSectionCADWidget()
    canvas.resize(600, 300)
    canvas.show()
    canvas.begin_dimension_label_pass()
    canvas.register_dimension_label(QRectF(100, 100, 120, 18), KEY_TS_DECK_THICKNESS)
    QTest.mouseDClick(canvas, Qt.LeftButton, Qt.NoModifier, QPoint(160, 109))
    assert canvas.dimension_editor is None
    canvas.close()


def test_only_one_editor_exists_at_a_time(ctx):
    first = ctx.dclick(ctx.cs, KEY_TS_DECK_THICKNESS)
    second = ctx.dclick(ctx.tv, KEY_SPAN)
    assert second is not None and ctx.cs.dimension_editor is None
    assert ctx.tv.dimension_editor is second
    # same canvas, different label: the old editor is replaced, not stacked
    ctx.dclick(ctx.cs, KEY_CARRIAGEWAY_WIDTH)
    ctx.dclick(ctx.cs, KEY_TS_DECK_THICKNESS)
    QApplication.sendPostedEvents(None, QEvent.DeferredDelete)       # flush deleteLater()
    editors = ctx.cs.findChildren(type(first), "cad_dimension_editor")
    assert len(editors) == 1 and editors[0] is ctx.cs.dimension_editor


# ──────────────────────────────────────────────────────────────────────────────
# 8-11  Validation (value is rejected, nothing changes)
# ──────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("key, good", [
    (KEY_SPAN, "35"), (KEY_CARRIAGEWAY_WIDTH, "9.5"),
    (KEY_TS_GIRDER_SPACING, "2.5"), (KEY_TS_DECK_THICKNESS, "220"),
])
def test_valid_numeric_value_is_accepted(ctx, key, good):
    result = ctx.sync.validate(key, good)
    assert result.ok, result.message
    assert result.value == pytest.approx(float(good))


@pytest.mark.parametrize("key", list(CAD_EDITABLE_DIMENSION_KEYS))
@pytest.mark.parametrize("bad", ["", "   "])
def test_empty_value_is_rejected(ctx, key, bad):
    result = ctx.sync.validate(key, bad)
    assert not result.ok and result.message


@pytest.mark.parametrize("key", list(CAD_EDITABLE_DIMENSION_KEYS))
@pytest.mark.parametrize("bad", ["abc", "12abc", "1,5", "--3", "1.2.3", "nan", "inf", "-inf", "1e3", "٣٠"])
def test_non_numeric_value_is_rejected(ctx, key, bad):
    assert not ctx.sync.validate(key, bad).ok


@pytest.mark.parametrize("key", list(CAD_EDITABLE_DIMENSION_KEYS))
@pytest.mark.parametrize("bad", ["0", "0.0", "-1", "-35"])
def test_zero_and_negative_values_are_rejected(ctx, key, bad):
    assert not ctx.sync.validate(key, bad).ok


def test_existing_project_limits_are_used_not_new_ones(ctx):
    # span: software limits from common.py
    assert ctx.sync.validate(KEY_SPAN, str(SPAN_MIN)).ok
    assert ctx.sync.validate(KEY_SPAN, str(SPAN_MAX)).ok
    assert "between" in ctx.sync.validate(KEY_SPAN, str(SPAN_MAX + 1)).message
    # deck thickness: validator range 100-500 mm
    assert ctx.sync.validate(KEY_TS_DECK_THICKNESS, "100").ok
    assert ctx.sync.validate(KEY_TS_DECK_THICKNESS, "500").ok
    assert not ctx.sync.validate(KEY_TS_DECK_THICKNESS, "501").ok
    # carriageway width: IRC 5 minimum for a single carriageway (4.25 m)
    assert ctx.sync.validate(KEY_CARRIAGEWAY_WIDTH, "4.25").ok
    assert "IRC 5" in ctx.sync.validate(KEY_CARRIAGEWAY_WIDTH, "4").message


def test_spacing_too_large_for_girder_count_is_rejected(ctx):
    result = ctx.sync.validate(KEY_TS_GIRDER_SPACING, "4")     # 5 girders: 4*4 m > 11.4 m
    assert not result.ok and "girders" in result.message.lower()


def test_validate_has_no_side_effects(ctx):
    before = dict(ctx.d)
    ctx.sync.validate(KEY_SPAN, "abc")
    ctx.sync.validate(KEY_TS_GIRDER_SPACING, "2.0")
    assert ctx.d == before and ctx.redraws == 0


# ──────────────────────────────────────────────────────────────────────────────
# 12-14  Escape / Enter / click outside
# ──────────────────────────────────────────────────────────────────────────────
def test_escape_cancels_and_changes_nothing(ctx):
    before = dict(ctx.d)
    editor = ctx.dclick(ctx.cs, KEY_CARRIAGEWAY_WIDTH)
    editor.setText("12")
    QTest.keyClick(editor, Qt.Key_Escape)
    ctx.app.processEvents()
    assert ctx.cs.dimension_editor is None
    assert ctx.d == before and ctx.dock._w(KEY_CARRIAGEWAY_WIDTH).text() == BASE_CW
    assert ctx.redraws == 0 and not ctx.errors


def test_enter_commits_a_valid_value(ctx):
    editor = ctx.dclick(ctx.tv, KEY_SPAN)
    ctx.type_and_enter(editor, "40")
    assert ctx.tv.dimension_editor is None
    assert ctx.d[KEY_SPAN] == "40"
    assert ctx.tv.params["span_length"] == 40000


def test_enter_with_invalid_value_keeps_editor_open_and_changes_nothing(ctx):
    before = dict(ctx.d)
    editor = ctx.dclick(ctx.cs, KEY_TS_DECK_THICKNESS)
    ctx.type_and_enter(editor, "50")
    assert ctx.cs.dimension_editor is editor and editor.property("error") is True
    assert ctx.errors == ["Deck thickness must be at least 100 mm."]    # existing message reused
    assert ctx.d == before and ctx.redraws == 0
    # the user can correct it and commit
    ctx.type_and_enter(editor, "260")
    assert ctx.cs.dimension_editor is None and ctx.d[KEY_TS_DECK_THICKNESS] == 260


def test_unchanged_value_closes_quietly_without_recalculation(ctx):
    editor = ctx.dclick(ctx.cs, KEY_CARRIAGEWAY_WIDTH)
    QTest.keyClick(editor, Qt.Key_Return)
    ctx.app.processEvents()
    assert ctx.cs.dimension_editor is None and ctx.redraws == 0 and not ctx.errors


def test_click_outside_commits_a_valid_value(ctx):
    editor = ctx.dclick(ctx.cs, KEY_TS_DECK_THICKNESS)
    editor.setText("300")
    QTest.mouseClick(ctx.cs, Qt.LeftButton, Qt.NoModifier, QPoint(3, 3))    # empty canvas, takes no focus
    QTest.qWait(30)
    assert ctx.cs.dimension_editor is None and ctx.d[KEY_TS_DECK_THICKNESS] == 300


def test_click_outside_on_another_canvas_commits(ctx):
    editor = ctx.dclick(ctx.cs, KEY_CARRIAGEWAY_WIDTH)
    editor.setText("11")
    QTest.mouseClick(ctx.tv, Qt.LeftButton, Qt.NoModifier, QPoint(3, 3))
    QTest.qWait(30)
    assert ctx.cs.dimension_editor is None and ctx.d[KEY_CARRIAGEWAY_WIDTH] == "11"


def test_click_outside_with_invalid_value_reports_once_and_keeps_editor(ctx):
    before = dict(ctx.d)
    editor = ctx.dclick(ctx.cs, KEY_TS_DECK_THICKNESS)
    editor.setText("abc")
    QTest.mouseClick(ctx.tv, Qt.LeftButton, Qt.NoModifier, QPoint(3, 3))
    QTest.qWait(30)
    assert ctx.cs.dimension_editor is editor
    assert len(ctx.errors) == 1                         # one click -> one message, not a pile
    assert ctx.d == before


def test_click_on_the_editor_itself_does_not_commit(ctx):
    editor = ctx.dclick(ctx.cs, KEY_TS_DECK_THICKNESS)
    editor.setText("300")
    QTest.mouseClick(editor, Qt.LeftButton, Qt.NoModifier, QPoint(5, 5))
    QTest.qWait(30)
    assert ctx.cs.dimension_editor is editor and ctx.d[KEY_TS_DECK_THICKNESS] != 300


# ──────────────────────────────────────────────────────────────────────────────
# 15-18  Synchronisation, redraw, derived values, model integrity
# ──────────────────────────────────────────────────────────────────────────────
def test_carriageway_edit_updates_ui_dict_cad_and_derived_values(ctx):
    cb_width = float(ctx.d[KEY_CB_WIDTH])
    ctx.type_and_enter(ctx.dclick(ctx.cs, KEY_CARRIAGEWAY_WIDTH), "12")

    # main UI + dictionary hold the same value
    assert ctx.dock._w(KEY_CARRIAGEWAY_WIDTH).text() == "12"
    assert ctx.d[KEY_CARRIAGEWAY_WIDTH] == "12"
    # both CAD views were fed the new value
    assert ctx.cs.params["carriageway_width"] == ctx.tv.params["carriageway_width"] == 12000
    # derived values were recalculated by the existing solver
    assert ctx.d[KEY_TS_OVERALL_WIDTH] == pytest.approx(12 + 2 * cb_width)
    n, s, o = ctx.d[KEY_TS_NO_OF_GIRDERS], ctx.d[KEY_TS_GIRDER_SPACING], ctx.d[KEY_TS_DECK_OVERHANG]
    assert (n - 1) * s + 2 * o == pytest.approx(ctx.d[KEY_TS_OVERALL_WIDTH], abs=1e-3)
    assert ctx.cs.params["deck_overhang"] == pytest.approx(o * 1000)
    assert ctx.redraws == 1                                      # exactly one update cycle


def test_span_edit_from_top_view_redraws_geometry(ctx):
    ctx.repaint()
    before = ctx.tv.grab().toImage()
    ctx.type_and_enter(ctx.dclick(ctx.tv, KEY_SPAN), "42.5")
    ctx.repaint()
    assert ctx.dock._w(KEY_SPAN).text() == "42.5" and ctx.d[KEY_SPAN] == "42.5"
    assert ctx.tv.params["span_length"] == 42500
    assert ctx.tv.grab().toImage() != before                     # pixels changed, not just a string
    assert ctx.redraws == 1
    # the label now shows the committed value after the redraw
    assert ctx.tv.show_span_values


@pytest.mark.parametrize("canvas_attr", ["cs", "tv"])
def test_girder_spacing_edit_rederives_overhang_and_keeps_girder_count(ctx, canvas_attr):
    n, overall = ctx.d[KEY_TS_NO_OF_GIRDERS], ctx.d[KEY_TS_OVERALL_WIDTH]
    ctx.type_and_enter(ctx.dclick(getattr(ctx, canvas_attr), KEY_TS_GIRDER_SPACING), "2.5")

    assert ctx.d[KEY_TS_GIRDER_SPACING] == pytest.approx(2.5)
    assert ctx.d[KEY_TS_NO_OF_GIRDERS] == n and ctx.d[KEY_TS_OVERALL_WIDTH] == pytest.approx(overall)
    assert ctx.d[KEY_TS_DECK_OVERHANG] == pytest.approx((overall - (n - 1) * 2.5) / 2)
    for canvas in (ctx.cs, ctx.tv):
        assert canvas.params["girder_spacing"] == pytest.approx(2500)
    assert ctx.cs.params["deck_overhang"] == pytest.approx(ctx.d[KEY_TS_DECK_OVERHANG] * 1000)
    assert ctx.redraws == 1


def test_deck_thickness_edit_is_kept_and_does_not_touch_other_values(ctx):
    before = dict(ctx.d)
    ctx.type_and_enter(ctx.dclick(ctx.cs, KEY_TS_DECK_THICKNESS), "300")
    assert ctx.d[KEY_TS_DECK_THICKNESS] == 300 and ctx.cs.params["deck_thickness"] == 300
    changed = {k for k in ctx.d if ctx.d[k] != before.get(k)}
    assert changed == {KEY_TS_DECK_THICKNESS}
    assert ctx.redraws == 1


def test_edited_additional_value_survives_the_next_refresh(ctx):
    ctx.type_and_enter(ctx.dclick(ctx.cs, KEY_TS_DECK_THICKNESS), "300")
    ctx.page.update_cad_from_inputs()                           # what any later UI change triggers
    assert ctx.d[KEY_TS_DECK_THICKNESS] == 300


def test_label_text_shows_committed_value_after_redraw(ctx):
    ctx.type_and_enter(ctx.dclick(ctx.cs, KEY_CARRIAGEWAY_WIDTH), "9.5")
    ctx.repaint()
    assert ctx.cs.show_carriageway_values and ctx.cs.params["carriageway_width"] / 1000 == 9.5
    # the repainted label is registered again (labels survive redraws)
    assert ctx.label_rect(ctx.cs, KEY_CARRIAGEWAY_WIDTH).width() > 0


def test_invalid_input_never_corrupts_the_model(ctx):
    snapshot = dict(ctx.d)
    params = (dict(ctx.cs.params), dict(ctx.tv.params))
    for key, canvas in ((KEY_SPAN, ctx.tv), (KEY_CARRIAGEWAY_WIDTH, ctx.cs),
                        (KEY_TS_GIRDER_SPACING, ctx.cs), (KEY_TS_DECK_THICKNESS, ctx.cs)):
        for bad in ("", "abc", "-4", "0", "99999"):
            editor = ctx.dclick(canvas, key)
            ctx.type_and_enter(editor, bad)
            assert canvas.dimension_editor is editor                 # stays open
            canvas.cancel_dimension_edit()
    assert ctx.d == snapshot and ctx.redraws == 0
    assert (dict(ctx.cs.params), dict(ctx.tv.params)) == params
    assert ctx.dock._w(KEY_SPAN).text() == BASE_SPAN
    assert ctx.dock._w(KEY_CARRIAGEWAY_WIDTH).text() == BASE_CW


def test_locked_input_dock_blocks_editing(ctx):
    ctx.dock.is_locked = True
    assert ctx.dclick(ctx.cs, KEY_TS_DECK_THICKNESS) is None
    assert ctx.dclick(ctx.tv, KEY_SPAN) is None
    assert not ctx.sync.can_edit(KEY_SPAN)


def test_additional_edit_needs_basic_inputs_first(ctx):
    ctx.dock._w(KEY_CARRIAGEWAY_WIDTH).setText("")
    for key in (KEY_TS_OVERALL_WIDTH, KEY_TS_NO_OF_GIRDERS, KEY_TS_GIRDER_SPACING, KEY_TS_DECK_OVERHANG):
        ctx.d.pop(key, None)
    ctx.d[KEY_CARRIAGEWAY_WIDTH] = None
    result = ctx.sync.validate(KEY_TS_DECK_THICKNESS, "300")
    assert not result.ok and "Carriageway Width" in result.message
