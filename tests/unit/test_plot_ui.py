"""UI tests for the plotting controls (output dock + plot widget).

Covers: Transverse Members selection, the shared checkbox / radio widgets
(toggle, visual state, label clicks, exclusivity, persistence across a
refresh), signal wiring (no duplicate connections, one redraw per action),
and control visibility when the dock is resized.

Runs offscreen; the real light stylesheet is loaded so QSS-driven rendering is
what gets tested.
"""
import os
import sys
import types

import numpy as np
import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QFile, QPoint, Qt, QTextStream           # noqa: E402
from PySide6.QtGui import QColor                                      # noqa: E402
from PySide6.QtTest import QTest                                      # noqa: E402
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox,   # noqa: E402
                               QHBoxLayout, QWidget)

from osdagbridge.core.utils.common import (                           # noqa: E402
    KEY_ANALYSIS_LOAD_COMBINATION, KEY_ANALYSIS_MEMBER, KEY_TS_NO_OF_GIRDERS,
    TRANSVERSE_MEMBERS_LABEL,
)


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    import osdagbridge.desktop.resources.resources_rc  # noqa: F401  (registers :/ resources)
    f = QFile(":/themes/lightstyle.qss")
    assert f.open(QFile.ReadOnly | QFile.Text)
    a.setStyleSheet(QTextStream(f).readAll())
    return a


def click(widget, x=None):
    """Real mouse click; x defaults to the left edge (indicator)."""
    QTest.mouseClick(widget, Qt.LeftButton,
                     pos=QPoint(8 if x is None else x, widget.height() // 2))
    QApplication.processEvents()


def pixmap_diff(a, b):
    return a.toImage() != b.toImage()


# =============================================================================
# Shared widgets: CustomRadioButton / RichCheckBox / QCheckBox under the QSS
# =============================================================================

@pytest.fixture
def host(app):
    w = QWidget()
    w.setLayout(QHBoxLayout())
    w.show()
    yield w
    w.close()


def _add(host, widget):
    host.layout().addWidget(widget)
    host.show()
    QApplication.processEvents()
    return widget


def test_radio_selects_on_click_and_looks_different(host):
    from osdagbridge.desktop.ui.utils.custom_widgets import CustomRadioButton
    rb = _add(host, CustomRadioButton("Option"))
    before = rb.grab()
    click(rb)
    assert rb.isChecked()
    assert pixmap_diff(before, rb.grab()), "selected radio is not visually distinct"


@pytest.mark.parametrize("text", ["Option", "F<sub>x</sub>"])
def test_radio_label_click_selects_the_button(host, text):
    from osdagbridge.desktop.ui.utils.custom_widgets import CustomRadioButton
    rb = _add(host, CustomRadioButton(text))
    for x in range(2, rb.width(), 4):              # every pixel column, circle and label
        rb.setChecked(False)
        click(rb, x)
        assert rb.isChecked(), f"click at x={x} (width {rb.width()}) did not select"


def test_radio_has_no_per_widget_stylesheet(host):
    from osdagbridge.desktop.ui.utils.custom_widgets import CustomRadioButton
    assert CustomRadioButton("x").styleSheet() == ""


def test_disabled_radio_is_visibly_disabled_and_keeps_selection(host):
    from osdagbridge.desktop.ui.utils.custom_widgets import CustomRadioButton
    rb = _add(host, CustomRadioButton("Option"))
    rb.setChecked(True)
    enabled = rb.grab()
    rb.setEnabled(False)
    QApplication.processEvents()
    assert rb.isChecked()
    assert pixmap_diff(enabled, rb.grab())


def test_radio_repaints_on_programmatic_change(host):
    from osdagbridge.desktop.ui.utils.custom_widgets import CustomRadioButton
    rb = _add(host, CustomRadioButton("Option"))
    off = rb.grab()
    rb.setChecked(True)
    QApplication.processEvents()
    on = rb.grab()
    rb.setChecked(False)           # radio buttons only go off via the group; force it
    rb.setAutoExclusive(False)
    rb.setChecked(False)
    QApplication.processEvents()
    assert pixmap_diff(off, on)


def test_radio_groups_do_not_interfere(host):
    from PySide6.QtWidgets import QButtonGroup
    from osdagbridge.desktop.ui.utils.custom_widgets import CustomRadioButton
    g1, g2 = QButtonGroup(host), QButtonGroup(host)
    a1, a2 = (_add(host, CustomRadioButton(t)) for t in ("a1", "a2"))
    b1, b2 = (_add(host, CustomRadioButton(t)) for t in ("b1", "b2"))
    for b in (a1, a2):
        g1.addButton(b)
    for b in (b1, b2):
        g2.addButton(b)
    click(a1)
    click(b2)
    click(a2)
    assert (a1.isChecked(), a2.isChecked(), b1.isChecked(), b2.isChecked()) == (False, True, False, True)


@pytest.mark.parametrize("cls_name", ["QCheckBox", "RichCheckBox"])
def test_checkbox_toggles_and_checked_state_is_visible(host, cls_name):
    from osdagbridge.desktop.ui.utils import custom_widgets
    cls = QCheckBox if cls_name == "QCheckBox" else custom_widgets.RichCheckBox
    cb = _add(host, cls("Max"))
    unchecked = cb.grab()
    click(cb)
    assert cb.isChecked()
    checked = cb.grab()
    assert pixmap_diff(unchecked, checked), "checked box looks identical to unchecked"
    click(cb)
    assert not cb.isChecked()
    assert not pixmap_diff(unchecked, cb.grab()), "unchecking does not restore the look"


def test_checked_indicator_is_visibly_marked_in_the_indicator_area(host):
    """The tick must be inside the indicator, not clipped or shifted away."""
    from osdagbridge.desktop.ui.utils.custom_widgets import RichCheckBox
    cb = _add(host, RichCheckBox("x"))
    off = cb.grab().toImage()
    cb.setChecked(True)
    on = cb.grab().toImage()
    changed = [(x, y) for x in range(on.width()) for y in range(on.height())
               if on.pixel(x, y) != off.pixel(x, y)]
    assert changed and max(x for x, _ in changed) < 20   # inside the 16px indicator


def test_rich_checkbox_label_click_toggles(host):
    from osdagbridge.desktop.ui.utils.custom_widgets import RichCheckBox
    cb = _add(host, RichCheckBox("M<sub>z</sub> values"))
    for x in range(2, cb.width(), 4):
        cb.setChecked(False)
        click(cb, x)
        assert cb.isChecked(), f"label click at x={x} did not toggle"


def test_disabled_checked_checkbox_differs_from_enabled_checked(host):
    cb = _add(host, QCheckBox("Max"))
    cb.setChecked(True)
    enabled = cb.grab()
    cb.setEnabled(False)
    QApplication.processEvents()
    assert cb.isChecked() and pixmap_diff(enabled, cb.grab())


def test_stylesheet_defines_every_checkbox_state():
    f = QFile(":/themes/lightstyle.qss")
    f.open(QFile.ReadOnly | QFile.Text)
    qss = QTextStream(f).readAll()
    for state in ("unchecked:hover", "checked", "checked:hover",
                  "unchecked:disabled", "checked:disabled"):
        assert f"QCheckBox::indicator:{state}" in qss, state


# =============================================================================
# Output dock + plot widget (with a small real xarray dataset)
# =============================================================================

NX, NZ = 4, 3          # 4 transverse lines x 3 girder positions


def _make_model():
    """nodes (id -> (x, y, z)), members (elem -> [n_i, n_j]) and element groups."""
    nodes, members, tags = {}, {}, {"start_edge": [], "transverse_slab": [], "end_edge": []}
    nid = lambda i, k: 100 + i * 10 + k
    for i in range(NX):
        for k in range(NZ):
            nodes[nid(i, k)] = (3.0 * i, 0.0, 2.0 * k)
    elem = 1
    for i in range(NX):
        group = "start_edge" if i == 0 else ("end_edge" if i == NX - 1 else "transverse_slab")
        for k in range(NZ - 1):
            members[elem] = [nid(i, k), nid(i, k + 1)]
            tags[group].append(elem)
            elem += 1
    for k in range(NZ):                              # longitudinal girders G1..G3
        for i in range(NX - 1):
            members[elem] = [nid(i, k), nid(i + 1, k)]
            elem += 1
    return nodes, members, tags


FORCE_COMPS = ["Mx_i", "Mx_j", "My_i", "My_j", "Mz_i", "Mz_j",
               "Vx_i", "Vx_j", "Vy_i", "Vy_j", "Vz_i", "Vz_j"]
DISP_COMPS = ["x", "y", "z", "theta_x", "theta_y", "theta_z"]


def _dataset(nodes, members):
    """Small real xarray dataset with the same layout as the analysis results."""
    xr = pytest.importorskip("xarray")
    elems, nds = sorted(members), sorted(nodes)
    rng = np.random.default_rng(7)
    forces = rng.uniform(1e3, 9e3, (2, len(elems), len(FORCE_COMPS)))
    # a component that is genuinely zero, as Vz / Fx are in a grillage model
    forces[:, :, FORCE_COMPS.index("Vz_i")] = 0.0
    forces[:, :, FORCE_COMPS.index("Vz_j")] = 0.0
    disps = rng.uniform(1e-3, 9e-3, (2, len(nds), len(DISP_COMPS)))
    return xr.Dataset({
        "forces": xr.DataArray(forces, dims=("Loadcase", "Element", "Component"),
                               coords={"Loadcase": ["SW", "DD"], "Element": elems,
                                       "Component": FORCE_COMPS}),
        "displacements": xr.DataArray(disps, dims=("Loadcase", "Node", "Component"),
                                      coords={"Loadcase": ["SW", "DD"], "Node": nds,
                                              "Component": DISP_COMPS}),
    })


@pytest.fixture
def ui(app):
    """Real OutputDock + MplPlotWidget wired together on a synthetic model."""
    from osdagbridge.core.bridge_types.plate_girder import ui_fields as uf
    from osdagbridge.desktop.ui.docks.output_dock import OutputDock
    from osdagbridge.desktop.ui.mpl_plot_widget import MplPlotWidget

    class Backend:
        input_dict = {KEY_TS_NO_OF_GIRDERS: NZ}

        def output_values(self, flag=None):
            return uf.FrontendData.output_values(uf.FrontendData)

        def get_available_loadcases(self):
            return ["SW", "DD"]

    nodes, members, tags = _make_model()
    dock = OutputDock(backend=Backend())
    dock.resize(380, 620)
    dock.show()
    plot = MplPlotWidget()
    plot.resize(700, 500)
    plot.show()

    ds = _dataset(nodes, members)
    # mirror what the app does after a design run
    dock.refresh_loadcase_dropdowns()
    dock.refresh_member_dropdown()
    plot._ds_all = ds
    plot._loadcases = ["SW", "DD"]
    plot._nodes, plot._members = nodes, members
    plot._transverse_elements = tags
    plot.link_output_dock(dock)
    QApplication.processEvents()
    yield types.SimpleNamespace(dock=dock, plot=plot, nodes=nodes, members=members, tags=tags)
    plot.close()
    dock.close()


def _combo(ui, key):
    return ui.dock._w(key)


def _radios(ui):
    from osdagbridge.desktop.ui.utils.custom_widgets import CustomRadioButton
    return {r.text(): r for r in ui.dock.output_widget.findChildren(CustomRadioButton)}


def _checks(ui):
    return {c.text().strip(): c for c in ui.dock.output_widget.findChildren(QCheckBox)}


def _flush(ui):
    ui.plot._update_timer.stop()
    ui.plot.update_plot()
    QApplication.processEvents()


def _select(ui, member=None, load_case=None):
    if member is not None:
        _combo(ui, KEY_ANALYSIS_MEMBER).setCurrentText(member)
    if load_case is not None:
        _combo(ui, KEY_ANALYSIS_LOAD_COMBINATION).setCurrentText(load_case)
    _flush(ui)


def test_member_dropdown_lists_girders_then_transverse_members_once(ui):
    combo = _combo(ui, KEY_ANALYSIS_MEMBER)
    items = [combo.itemText(i) for i in range(combo.count())]
    assert items == ["All", "G1", "G2", "G3", TRANSVERSE_MEMBERS_LABEL]


def test_transverse_dropdown_is_filled_even_without_the_design_member_combo(app):
    """The Transverse entry must not depend on the (separate) design dropdown."""
    from osdagbridge.desktop.ui.docks.output_dock import OutputDock
    combo = QComboBox()
    stub = types.SimpleNamespace(
        backend=types.SimpleNamespace(input_dict={KEY_TS_NO_OF_GIRDERS: 2}),
        _w=lambda key: combo if key == KEY_ANALYSIS_MEMBER else None)
    OutputDock.refresh_member_dropdown(stub)
    assert [combo.itemText(i) for i in range(combo.count())] == \
        ["All", "G1", "G2", TRANSVERSE_MEMBERS_LABEL]


def test_selecting_transverse_members_plots_end_diaphragms_and_cross_bracings(ui):
    _select(ui, TRANSVERSE_MEMBERS_LABEL)
    assert sorted(ui.plot._summary_data) == ["CB1", "CB2", "ED1", "ED2"]
    # the widget shows the title through its own overlay (not matplotlib's)
    assert "Transverse Members" in ui.plot._title_overlay.title_label.text()


def test_every_component_is_available_and_plots_for_transverse_members(ui):
    _select(ui, TRANSVERSE_MEMBERS_LABEL)
    radios = _radios(ui)
    assert len(radios) == 9
    for text, rb in radios.items():
        click(rb)
        _flush(ui)
        assert rb.isChecked()
        assert sorted(ui.plot._summary_data) == ["CB1", "CB2", "ED1", "ED2"], text


def test_zero_component_shows_a_message_not_a_silent_flat_plot(ui):
    _select(ui, TRANSVERSE_MEMBERS_LABEL)
    click(_radios(ui)["V<sub>z</sub>"])            # genuinely zero in the dataset
    _flush(ui)
    texts = [t.get_text() for t in ui.plot._fig.texts if t.get_gid() == "transverse_notice"]
    assert texts and "zero" in texts[0]
    click(_radios(ui)["V<sub>y</sub>"])
    _flush(ui)
    assert not [t for t in ui.plot._fig.texts if t.get_gid() == "transverse_notice"]


def test_load_case_change_changes_the_transverse_plot(ui):
    _select(ui, TRANSVERSE_MEMBERS_LABEL, "SW")
    click(_radios(ui)["V<sub>y</sub>"])
    _flush(ui)
    sw = {k: dict(v) for k, v in ui.plot._summary_data.items()}
    _select(ui, load_case="DD")
    dd = {k: dict(v) for k, v in ui.plot._summary_data.items()}
    assert sw != dd


def test_switching_between_longitudinal_and_transverse_selections(ui):
    _select(ui, "All")
    assert sorted(ui.plot._summary_data) == ["G1", "G2", "G3"]
    _select(ui, TRANSVERSE_MEMBERS_LABEL)
    assert sorted(ui.plot._summary_data) == ["CB1", "CB2", "ED1", "ED2"]
    _select(ui, "G2")
    assert sorted(ui.plot._summary_data) == ["G2"]
    _select(ui, "All")
    assert sorted(ui.plot._summary_data) == ["G1", "G2", "G3"]


def test_longitudinal_plot_is_unchanged_by_transverse_support(ui):
    _select(ui, "All")
    for text in ("M<sub>z</sub>", "V<sub>y</sub>", "D<sub>y</sub>"):
        click(_radios(ui)[text])
        _flush(ui)
        assert sorted(ui.plot._summary_data) == ["G1", "G2", "G3"]
        assert not [t for t in ui.plot._fig.texts if t.get_gid() == "transverse_notice"]


def test_missing_results_give_a_message_and_no_fake_zero_plot(ui):
    ui.plot._transverse_elements = {}
    _select(ui, TRANSVERSE_MEMBERS_LABEL)
    assert ui.plot._summary_data == {}
    assert any("transverse" in t.get_text().lower() for t in ui.plot._fig.texts)


# ---- radios in the dock ------------------------------------------------------

def test_exactly_one_force_radio_is_selected_and_it_is_exclusive(ui):
    radios = _radios(ui)
    assert sum(r.isChecked() for r in radios.values()) == 1
    for text, rb in radios.items():
        click(rb)
        assert [t for t, r in radios.items() if r.isChecked()] == [text]


def test_radio_click_triggers_exactly_one_redraw(ui):
    calls = []
    orig = ui.plot.update_plot
    ui.plot.update_plot = lambda *a, **k: (calls.append(1), orig(*a, **k))[1]
    radios = _radios(ui)
    target = next(r for r in radios.values() if not r.isChecked())
    click(target)
    QTest.qWait(30)                       # let the coalescing timer fire
    assert len(calls) == 1


def test_radio_state_is_painted_before_the_slow_redraw_runs(ui):
    """The slot must not redraw synchronously inside the click (it blocked the repaint)."""
    calls = []
    orig = ui.plot.update_plot
    ui.plot.update_plot = lambda *a, **k: (calls.append(1), orig(*a, **k))[1]
    target = next(r for r in _radios(ui).values() if not r.isChecked())
    QTest.mouseClick(target, Qt.LeftButton, pos=QPoint(8, target.height() // 2))
    assert target.isChecked() and calls == []      # no redraw yet, control already updated
    QTest.qWait(30)
    assert calls == [1]


# ---- checkboxes in the dock --------------------------------------------------

def test_display_checkboxes_toggle_and_drive_the_plot(ui):
    cbs = _checks(ui)
    for name in ("Max", "Min", "All", "Summary", "Utilization Ratio"):
        assert name in cbs
    click(cbs["Summary"])
    assert cbs["Summary"].isChecked() and ui.plot._is_summary_checked
    click(cbs["Max"])
    assert cbs["Max"].isChecked() and ui.plot._show_max
    click(cbs["Max"])
    assert not cbs["Max"].isChecked() and not ui.plot._show_max


def test_all_disables_max_min_but_keeps_their_state(ui):
    cbs = _checks(ui)
    click(cbs["Max"])
    click(cbs["All"])
    assert not cbs["Max"].isEnabled() and not cbs["Min"].isEnabled()
    assert cbs["Max"].isChecked()
    click(cbs["All"])
    assert cbs["Max"].isEnabled() and cbs["Max"].isChecked()


def test_checkbox_and_radio_state_survives_a_plot_refresh(ui):
    cbs, radios = _checks(ui), _radios(ui)
    click(cbs["Summary"])
    click(cbs["Max"])
    click(radios["T<sub>x</sub>"])
    _select(ui, TRANSVERSE_MEMBERS_LABEL, "DD")
    for _ in range(2):                               # re-run: dropdowns + links rebuilt
        ui.dock.refresh_loadcase_dropdowns()
        ui.dock.refresh_member_dropdown()
        ui.plot.link_output_dock(ui.dock)
        QApplication.processEvents()
    assert cbs["Summary"].isChecked() and cbs["Max"].isChecked()
    assert [t for t, r in radios.items() if r.isChecked()] == ["T<sub>x</sub>"]
    assert _combo(ui, KEY_ANALYSIS_MEMBER).currentText() == TRANSVERSE_MEMBERS_LABEL
    assert _combo(ui, KEY_ANALYSIS_LOAD_COMBINATION).currentText() == "DD"


def test_relinking_does_not_duplicate_connections(ui):
    for _ in range(3):
        ui.plot.link_output_dock(ui.dock)
    calls = []
    orig = ui.plot.update_plot
    ui.plot.update_plot = lambda *a, **k: (calls.append(1), orig(*a, **k))[1]
    _combo(ui, KEY_ANALYSIS_LOAD_COMBINATION).setCurrentText("DD")
    QTest.qWait(30)
    assert len(calls) == 1
    seen = []
    ui.plot._on_summary_toggled = lambda c: seen.append(c)       # not wired (bound earlier)
    cb = _checks(ui)["Max"]
    toggles = []
    ui.plot._on_max_toggled = lambda c: toggles.append(c)
    click(cb)
    assert cb.isChecked()


def test_clear_disconnects_everything(ui):
    ui.plot.clear()
    assert ui.plot._dock_connections == []
    click(next(iter(_radios(ui).values())))        # must not raise / redraw into a dead plot


# ---- layout / resizing -------------------------------------------------------

@pytest.mark.parametrize("size", [(380, 700), (380, 420), (380, 300), (300, 500)])
def test_every_control_is_reachable_at_any_dock_size(ui, size):
    """Each control is either fully visible or reachable by scrolling - never clipped away."""
    dock = ui.dock
    dock.resize(*size)
    QApplication.processEvents()
    sa = dock.scroll_area
    widgets = list(_radios(ui).values()) + list(_checks(ui).values()) + [
        _combo(ui, KEY_ANALYSIS_MEMBER), _combo(ui, KEY_ANALYSIS_LOAD_COMBINATION)]
    for wdg in widgets:
        assert wdg.width() > 0 and wdg.height() > 0
        sa.ensureWidgetVisible(wdg, 0, 0)
        QApplication.processEvents()
        top_left = wdg.mapTo(sa.viewport(), QPoint(0, 0))
        vp = sa.viewport().rect()
        assert vp.contains(top_left), f"{wdg} unreachable at {size}"
        assert top_left.y() + wdg.height() <= vp.height() + 1
        # horizontally: either inside the viewport, or the area can scroll to it
        assert (top_left.x() + wdg.width() <= vp.width() + 1) or sa.horizontalScrollBar().maximum() > 0


def test_dock_scrolls_when_short_and_the_buttons_stay_in_view(ui):
    ui.dock.resize(380, 300)
    QApplication.processEvents()
    assert ui.dock.scroll_area.verticalScrollBar().maximum() > 0
    assert ui.dock.report_btn.isVisible()
    assert ui.dock.report_btn.mapTo(ui.dock, QPoint(0, 0)).y() + ui.dock.report_btn.height() <= ui.dock.height()


def test_dock_content_is_not_wider_than_its_scroll_viewport_unless_scrollable(ui):
    ui.dock.resize(300, 600)
    QApplication.processEvents()
    sa = ui.dock.scroll_area
    content = sa.widget().width()
    assert content <= sa.viewport().width() or sa.horizontalScrollBar().maximum() > 0


@pytest.mark.parametrize("width", [260, 400, 700, 1100])
def test_plot_title_wraps_and_stays_inside_the_plot(ui, width):
    ui.plot.resize(width, 500)
    QApplication.processEvents()
    ui.plot._title_overlay.update_text(
        "Transverse Members — Bending Moment — Mx (kNm) with a deliberately long title", "Load Case/Combination : SW")
    ui.plot._position_title_overlay()
    QApplication.processEvents()
    geo = ui.plot._title_overlay.geometry()
    canvas = ui.plot._canvas.rect()
    assert geo.left() >= 0 and geo.right() <= canvas.right()
    lbl = ui.plot._title_overlay.title_label
    assert lbl.wordWrap()
    # the label is tall enough for its wrapped text (not clipped)
    assert lbl.height() >= lbl.heightForWidth(lbl.width()) - 1


def test_plot_area_and_dock_resize_without_error(ui):
    for sz in [(1400, 900), (1000, 600), (800, 500)]:
        ui.plot.resize(*sz)
        QApplication.processEvents()
        click(next(iter(_radios(ui).values())))
        QTest.qWait(20)
        assert ui.plot._fig.axes
