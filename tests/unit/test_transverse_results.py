"""
Task 3 - result plotting for transverse members.

Covers the mapping (results_data.get_member_element_map / build_transverse_lines),
the data extraction (extract_transverse_*_profiles), and the plotting entry
point reached from the analysis "Member" dropdown (selected_girder ==
"Transverse Members").

The tests use a small synthetic 3-girder grillage and an xarray-like fake
dataset, so they need neither OpenSees nor an analysis run.  The element tags
are deliberately interleaved (transverse and longitudinal tags mixed) to prove
that nothing depends on element numbering.

        z=4   n3 ---- n6 ---- n9
        z=2   n2 ---- n5 ---- n8        x stations 0 / 5 / 10
        z=0   n1 ---- n4 ---- n7

        start_edge      : e1, e2        (x = 0)
        girder 1        : e3, e4        (z = 0)
        transverse_slab : e5, e6        (x = 5)
        girder 2        : e7, e8        (z = 2)
        girder 3        : e9, e10       (z = 4)
        end_edge        : e11, e12      (x = 10)
"""
import sys
import types

import numpy as np
import pytest

# plot_generator / results_data import openseespy at module level; the tests
# never call OpenSees, so a stand-in is enough when it is not installed.
try:  # pragma: no cover - depends on the environment
    import openseespy.opensees  # noqa: F401
except ImportError:  # pragma: no cover
    _pkg = types.ModuleType("openseespy")
    _ops = types.ModuleType("openseespy.opensees")
    _pkg.opensees = _ops
    sys.modules.setdefault("openseespy", _pkg)
    sys.modules.setdefault("openseespy.opensees", _ops)

from osdagbridge.core.bridge_types.plate_girder import results_data as rd  # noqa: E402
from osdagbridge.core.bridge_types.plate_girder import plot_generator as pg  # noqa: E402
from osdagbridge.core.utils.common import TRANSVERSE_MEMBERS_LABEL  # noqa: E402

FORCE_COMPS = ["Vx_i", "Vx_j", "Vy_i", "Vy_j", "Vz_i", "Vz_j",
               "Mx_i", "Mx_j", "My_i", "My_j", "Mz_i", "Mz_j"]
DISP_COMPS = ["x", "y", "z", "theta_x", "theta_y", "theta_z"]


def n(ix, iz):
    return ix * 3 + iz + 1


NODES = {n(ix, iz): [5.0 * ix, 0.0, 2.0 * iz] for ix in range(3) for iz in range(3)}
MEMBERS = {
    1: [n(0, 0), n(0, 1)], 2: [n(0, 1), n(0, 2)],
    3: [n(0, 0), n(1, 0)], 4: [n(1, 0), n(2, 0)],
    5: [n(1, 0), n(1, 1)], 6: [n(1, 1), n(1, 2)],
    7: [n(0, 1), n(1, 1)], 8: [n(1, 1), n(2, 1)],
    9: [n(0, 2), n(1, 2)], 10: [n(1, 2), n(2, 2)],
    11: [n(2, 0), n(2, 1)], 12: [n(2, 1), n(2, 2)],
}
GROUPS = {
    "start_edge": [1, 2], "transverse_slab": [5, 6], "end_edge": [11, 12],
    "exterior_main_beam_1": [3, 4], "interior_main_beam": [7, 8],
    "exterior_main_beam_2": [9, 10],
}
TRANSVERSE = {"start_edge": [1, 2], "transverse_slab": [5, 6], "end_edge": [11, 12]}


class FakeModel:
    """Minimal stand-in for the ospgrillage model's member-group API."""

    def __init__(self, groups=None, z_groups=None, broken=False):
        self.groups = dict(GROUPS if groups is None else groups)
        self.common_grillage_element_z_group = z_groups if z_groups is not None else {
            "edge_beam": [], "exterior_main_beam_1": [0],
            "interior_main_beam": [1], "exterior_main_beam_2": [2],
        }
        self.broken = broken

    def get_element(self, member=None, options="nodes", z_group_num=0, **_):
        if self.broken:
            raise RuntimeError("model not built")
        if member in ("transverse_slab", "start_edge", "end_edge"):
            return list(self.groups.get(member, []))
        if member in self.common_grillage_element_z_group:
            zg = self.common_grillage_element_z_group[member]
            return list(self.groups.get(member, [])) if zg and z_group_num < len(zg) else []
        raise KeyError(member)


# ── xarray-like fakes ─────────────────────────────────────────────────────────

class _Vals:
    def __init__(self, values):
        self.values = np.asarray(values)


class FakeDA:
    def __init__(self, arr, dim, ids, comps):
        self.values = np.asarray(arr, dtype=float)
        self._dim = dim
        self.coords = {dim: _Vals(ids), "Component": _Vals(comps)}

    def sel(self, **kw):
        i = list(self.coords[self._dim].values).index(kw[self._dim])
        j = list(self.coords["Component"].values).index(kw["Component"])
        return _Vals(self.values[i, j])


class FakeDS:
    def __init__(self, forces=None, disps=None):
        self._vars = {}
        if forces is not None:
            self._vars["forces"] = forces
        if disps is not None:
            self._vars["displacements"] = disps

    def __getitem__(self, key):
        if key == "Component":
            return self._vars["forces"].coords["Component"]
        return self._vars[key]


def raw_force(lc, e, c):
    return (lc + 1) * 1e6 + e * 1e3 + c * 10.0


def raw_disp(lc, nd, c):
    return (lc + 1) * 1e-3 + nd * 1e-4 + c * 1e-5


def make_ds(lc=0, elems=None, comps=None, with_forces=True, with_disps=True):
    elems = sorted(MEMBERS) if elems is None else elems
    comps = FORCE_COMPS if comps is None else comps
    forces = disps = None
    if with_forces:
        forces = FakeDA([[raw_force(lc, e, c) for c in range(len(comps))] for e in elems],
                        "Element", elems, comps)
    if with_disps:
        nds = sorted(NODES)
        disps = FakeDA([[raw_disp(lc, nd, c) for c in range(len(DISP_COMPS))] for nd in nds],
                       "Node", nds, DISP_COMPS)
    return FakeDS(forces, disps)


def kn(lc, e, comp):
    return raw_force(lc, e, FORCE_COMPS.index(comp)) / 1000.0


def mm(lc, nd, comp, flip):
    v = raw_disp(lc, nd, DISP_COMPS.index(comp)) * 1000.0
    return -v if flip else v


# =============================================================================
# Member mapping
# =============================================================================

def test_transverse_elements_identified_from_model_groups():
    m = rd.get_member_element_map(FakeModel(), MEMBERS)
    assert m["transverse"] == [1, 2, 5, 6, 11, 12]
    assert m["transverse_groups"] == TRANSVERSE


def test_longitudinal_elements_remain_identified():
    m = rd.get_member_element_map(FakeModel(), MEMBERS)
    assert m["longitudinal"] == [3, 4, 7, 8, 9, 10]


def test_mapping_does_not_overlap_and_covers_every_element():
    m = rd.get_member_element_map(FakeModel(), MEMBERS)
    assert not set(m["transverse"]) & set(m["longitudinal"])
    assert set(m["transverse"]) | set(m["longitudinal"]) == set(MEMBERS)


def test_mapping_does_not_depend_on_element_numbering():
    m = rd.get_member_element_map(FakeModel(), MEMBERS)
    # transverse tags sit both below and above the longitudinal ones
    assert min(m["transverse"]) < min(m["longitudinal"])
    assert max(m["transverse"]) > max(m["longitudinal"])
    assert set(rd.get_transverse_element_tags(FakeModel(), MEMBERS)) == {1, 2, 5, 6, 11, 12}


def test_element_found_in_both_sets_stays_longitudinal():
    groups = dict(GROUPS)
    groups["transverse_slab"] = [5, 6, 3]          # 3 is a girder element
    m = rd.get_member_element_map(FakeModel(groups=groups), MEMBERS)
    assert 3 not in m["transverse"]
    assert 3 in m["longitudinal"]


def test_tags_that_are_not_live_elements_are_dropped():
    m = rd.get_member_element_map(FakeModel(), {k: v for k, v in MEMBERS.items() if k != 6})
    assert 6 not in m["transverse"]


def test_missing_mapping_is_handled_safely():
    assert rd.get_transverse_element_tags(FakeModel(broken=True), MEMBERS) == []
    assert rd.get_member_element_map(object(), MEMBERS)["transverse"] == []


def test_empty_transverse_collections_are_handled():
    model = FakeModel(groups={"exterior_main_beam_1": [3, 4]})
    m = rd.get_member_element_map(model, MEMBERS)
    assert m["transverse"] == []
    assert m["transverse_groups"] == {"transverse_slab": [], "start_edge": [], "end_edge": []}
    assert rd.build_transverse_lines(NODES, MEMBERS, []) == []


# =============================================================================
# Transverse lines
# =============================================================================

def test_lines_are_ordered_across_the_deck_and_named_by_span_position():
    tags, group = [], {}
    for g, ts in TRANSVERSE.items():
        for t in ts:
            tags.append(t)
            group[t] = g
    lines = rd.build_transverse_lines(NODES, MEMBERS, tags, group)
    assert [ln["name"] for ln in lines] == ["T1", "T2", "T3"]
    assert [ln["group"] for ln in lines] == ["start_edge", "transverse_slab", "end_edge"]
    assert [ln["elements"] for ln in lines] == [[1, 2], [5, 6], [11, 12]]
    assert lines[1]["node_ids"] == [n(1, 0), n(1, 1), n(1, 2)]
    assert lines[1]["zs"] == [0.0, 2.0, 4.0]


def test_elements_stored_against_the_line_direction_are_flagged_reversed():
    members = dict(MEMBERS)
    members[6] = [n(1, 2), n(1, 1)]                 # i-node on the high-z side
    lines = rd.build_transverse_lines(NODES, members, [5, 6])
    assert lines[0]["elements"] == [5, 6]
    assert lines[0]["reversed"] == [False, True]
    assert lines[0]["node_ids"] == [n(1, 0), n(1, 1), n(1, 2)]


def test_different_groups_sharing_a_node_are_not_merged():
    members = dict(MEMBERS)
    members[11] = [n(1, 2), n(2, 0)]                # touches line 5-6 at n(1,2)
    group = {5: "transverse_slab", 6: "transverse_slab", 11: "end_edge"}
    lines = rd.build_transverse_lines(NODES, members, [5, 6, 11], group)
    assert sorted(ln["elements"] for ln in lines) == [[5, 6], [11]]


def test_unknown_elements_are_ignored_by_line_builder():
    lines = rd.build_transverse_lines(NODES, MEMBERS, [5, 6, 999])
    assert [ln["elements"] for ln in lines] == [[5, 6]]


# =============================================================================
# Data extraction
# =============================================================================

def _lines(tags=(1, 2, 5, 6, 11, 12)):
    return rd.build_transverse_lines(NODES, MEMBERS, list(tags))


def test_transverse_forces_are_extracted_correctly():
    out = rd.extract_transverse_force_profiles(make_ds(0), _lines(), "Vy_i", "Vy_j")
    assert out["unit"] == "kN"
    t2 = next(p for p in out["profiles"] if p["name"] == "T2")      # elements 5, 6
    assert t2["values"].tolist() == pytest.approx(
        [kn(0, 5, "Vy_i"), kn(0, 6, "Vy_i"), kn(0, 6, "Vy_j")])


def test_j_end_sign_convention_matches_longitudinal_shear():
    out = rd.extract_transverse_force_profiles(
        make_ds(0), _lines((5, 6)), "Vz_i", "Vz_j", negate_j=True)
    assert out["profiles"][0]["values"].tolist() == pytest.approx(
        [kn(0, 5, "Vz_i"), kn(0, 6, "Vz_i"), -kn(0, 6, "Vz_j")])


def test_transverse_moments_are_extracted_correctly():
    out = rd.extract_transverse_force_profiles(make_ds(0), _lines(), "Mz_i", "Mz_j")
    assert out["unit"] == "kNm"
    t1 = next(p for p in out["profiles"] if p["name"] == "T1")      # elements 1, 2
    assert t1["values"].tolist() == pytest.approx(
        [kn(0, 1, "Mz_i"), kn(0, 2, "Mz_i"), kn(0, 2, "Mz_j")])


def test_reversed_element_uses_the_end_attached_to_each_node():
    members = dict(MEMBERS)
    members[6] = [n(1, 2), n(1, 1)]
    lines = rd.build_transverse_lines(NODES, members, [5, 6])
    out = rd.extract_transverse_force_profiles(
        make_ds(0), lines, "Vy_i", "Vy_j", negate_j=True)
    # node (1,1) is the j-end of e6, node (1,2) its i-end
    assert out["profiles"][0]["values"].tolist() == pytest.approx(
        [kn(0, 5, "Vy_i"), -kn(0, 6, "Vy_j"), kn(0, 6, "Vy_i")])


def test_transverse_displacements_are_extracted_correctly():
    lines = _lines((5, 6))
    for comp, flip in (("dx", False), ("dy", True), ("dz", True)):
        out = rd.extract_transverse_displacement_profiles(make_ds(0), lines, comp)
        c = comp[1]
        assert out["unit"] == "mm"
        assert out["profiles"][0]["values"].tolist() == pytest.approx(
            [mm(0, nd, c, flip) for nd in (n(1, 0), n(1, 1), n(1, 2))])


def test_extraction_uses_the_slice_it_is_given_so_load_cases_do_not_mix():
    lines = _lines((5, 6))
    a = rd.extract_transverse_force_profiles(make_ds(0), lines, "Mz_i", "Mz_j")
    b = rd.extract_transverse_force_profiles(make_ds(1), lines, "Mz_i", "Mz_j")
    assert a["profiles"][0]["values"].tolist() == pytest.approx(
        [kn(0, 5, "Mz_i"), kn(0, 6, "Mz_i"), kn(0, 6, "Mz_j")])
    assert b["profiles"][0]["values"].tolist() == pytest.approx(
        [kn(1, 5, "Mz_i"), kn(1, 6, "Mz_i"), kn(1, 6, "Mz_j")])
    assert a["profiles"][0]["values"].tolist() != b["profiles"][0]["values"].tolist()


def test_summary_holds_max_and_min_per_line():
    out = rd.extract_transverse_force_profiles(make_ds(0), _lines(), "Mz_i", "Mz_j")
    for p in out["profiles"]:
        assert out["summary"][p["name"]] == {"max": float(p["values"].max()),
                                             "min": float(p["values"].min())}


def test_missing_component_raises_a_clear_error():
    ds = make_ds(0, comps=["Vy_i", "Vy_j"])
    with pytest.raises(rd.TransverseResultsError):
        rd.extract_transverse_force_profiles(ds, _lines(), "Mz_i", "Mz_j")
    with pytest.raises(rd.TransverseResultsError):
        rd.extract_transverse_displacement_profiles(make_ds(0), _lines(), "dq")


def test_missing_result_tables_raise_a_clear_error():
    with pytest.raises(rd.TransverseResultsError):
        rd.extract_transverse_force_profiles(
            make_ds(0, with_forces=False), _lines(), "Vy_i", "Vy_j")
    with pytest.raises(rd.TransverseResultsError):
        rd.extract_transverse_displacement_profiles(
            make_ds(0, with_disps=False), _lines(), "dy")


def test_no_lines_raises_a_clear_error():
    with pytest.raises(rd.TransverseResultsError):
        rd.extract_transverse_force_profiles(make_ds(0), [], "Vy_i", "Vy_j")
    with pytest.raises(rd.TransverseResultsError):
        rd.extract_transverse_displacement_profiles(make_ds(0), [], "dy")


def test_line_with_missing_element_results_is_skipped_not_faked():
    ds = make_ds(0, elems=[e for e in sorted(MEMBERS) if e != 6])
    out = rd.extract_transverse_force_profiles(ds, _lines(), "Vy_i", "Vy_j")
    assert [p["name"] for p in out["profiles"]] == ["T1", "T3"]
    assert out["skipped_elements"] == [5, 6]


def test_all_lines_without_results_raises():
    ds = make_ds(0, elems=[3, 4, 7, 8, 9, 10])      # only longitudinal rows
    with pytest.raises(rd.TransverseResultsError):
        rd.extract_transverse_force_profiles(ds, _lines(), "Vy_i", "Vy_j")


def test_non_finite_values_are_never_plotted():
    ds = make_ds(0)
    ds["forces"].values[list(ds["forces"].coords["Element"].values).index(5), 2] = np.nan
    out = rd.extract_transverse_force_profiles(ds, _lines(), "Vy_i", "Vy_j")
    assert "T2" not in [p["name"] for p in out["profiles"]]


def test_longitudinal_data_is_never_substituted_for_transverse():
    ds = make_ds(0, elems=[3, 4, 7, 8, 9, 10])
    with pytest.raises(rd.TransverseResultsError):
        rd.extract_transverse_force_profiles(ds, _lines((5, 6)), "Vy_i", "Vy_j")


def test_multi_loadcase_slice_is_rejected():
    ds = make_ds(0)
    ds["forces"].values = np.stack([ds["forces"].values] * 2)      # extra dimension
    with pytest.raises(rd.TransverseResultsError):
        rd.extract_transverse_force_profiles(ds, _lines(), "Vy_i", "Vy_j")


# =============================================================================
# Plotting entry point ("Transverse Members" in the Member dropdown)
# =============================================================================

ALL_KEYS = ["Fx", "Fy", "Fz", "Mx", "My", "Mz", "Dx", "Dy", "Dz"]


def _plot(key, lc=0, member=TRANSVERSE_MEMBERS_LABEL, transverse=TRANSVERSE, ds=None):
    ds = make_ds(lc) if ds is None else ds
    if key in pg.DISP_MAP:
        fn = pg.build_figure_deflection
    elif key.startswith("F"):
        fn = pg.build_figure_sfd
    else:
        fn = pg.build_figure_bmd
    return fn(ds, key, NODES, MEMBERS, edge_dist=0.0, eng_scale=1.0,
              selected_girder=member, transverse_elements=transverse)


def test_dropdown_label_is_a_single_unified_entry():
    assert TRANSVERSE_MEMBERS_LABEL == "Transverse Members"


@pytest.mark.parametrize("key", ALL_KEYS)
def test_every_result_component_is_supported(key):
    fig, summary = _plot(key)
    assert sorted(summary) == ["T1", "T2", "T3"]
    assert fig.axes and "Transverse Members" in fig.axes[0].get_title()
    unit = "mm" if key.startswith("D") else ("kNm" if key.startswith("M") else "kN")
    assert fig.axes[0].get_zlabel().endswith(f"({unit})")


def test_selecting_transverse_members_plots_transverse_data():
    _, summary = _plot("Mz")
    # line T2 = elements 5 and 6 (x = 5)
    vals = [kn(0, 5, "Mz_i"), kn(0, 6, "Mz_i"), kn(0, 6, "Mz_j")]
    assert summary["T2"] == pytest.approx({"max": max(vals), "min": min(vals)})


def test_shear_plot_uses_the_longitudinal_sign_convention():
    _, summary = _plot("Fy")
    vals = [kn(0, 5, "Vy_i"), kn(0, 6, "Vy_i"), -kn(0, 6, "Vy_j")]
    assert summary["T2"] == pytest.approx({"max": max(vals), "min": min(vals)})


def test_displacement_plot_values_are_in_mm_with_sag_positive():
    _, summary = _plot("Dy")
    vals = [mm(0, nd, "y", True) for nd in (n(1, 0), n(1, 1), n(1, 2))]
    assert summary["T2"] == pytest.approx({"max": max(vals), "min": min(vals)})


def test_changing_the_load_case_changes_the_plotted_data():
    _, s0 = _plot("Mz", lc=0)
    _, s1 = _plot("Mz", lc=1)
    assert s0["T2"] != s1["T2"]
    vals = [kn(1, 5, "Mz_i"), kn(1, 6, "Mz_i"), kn(1, 6, "Mz_j")]
    assert s1["T2"] == pytest.approx({"max": max(vals), "min": min(vals)})


def test_changing_the_member_selection_changes_the_plot():
    _, trans = _plot("Mz")
    _, girder = _plot("Mz", member="G1")
    assert sorted(trans) == ["T1", "T2", "T3"]
    assert sorted(girder) == ["G1"]


@pytest.mark.parametrize("key", ["Fy", "Mz", "Dy"])
def test_existing_longitudinal_plots_still_work(key):
    _, all_g = _plot(key, member="All")
    _, g2 = _plot(key, member="G2")
    assert sorted(all_g) == ["G1", "G2", "G3"]
    assert sorted(g2) == ["G2"]


def test_longitudinal_plot_ignores_the_transverse_argument():
    _, with_t = _plot("Mz", member="G1", transverse=TRANSVERSE)
    _, without = _plot("Mz", member="G1", transverse=None)
    assert with_t == without


def test_longitudinal_values_unchanged_by_task_3():
    _, summary = _plot("Mz", member="G1")
    vals = [kn(0, 3, "Mz_i"), kn(0, 4, "Mz_i"), kn(0, 4, "Mz_j")]
    assert summary["G1"] == pytest.approx({"max": max(vals), "min": min(vals)})


@pytest.mark.parametrize("transverse", [None, {}, [], {"transverse_slab": []}])
def test_no_transverse_elements_gives_a_message_not_a_crash(transverse):
    fig, summary = _plot("Mz", transverse=transverse)
    assert summary == {}
    assert any("No transverse" in t.get_text() for t in fig.texts)


def test_missing_load_case_data_gives_a_message_not_a_crash():
    ds = make_ds(0, elems=[3, 4, 7, 8, 9, 10])
    fig, summary = _plot("Mz", ds=ds)
    assert summary == {}
    assert any("transverse" in t.get_text().lower() for t in fig.texts)


def test_unknown_component_gives_a_message_not_a_crash():
    fig, summary = pg.build_figure_bmd(
        make_ds(0), "Qq", NODES, MEMBERS, selected_girder=TRANSVERSE_MEMBERS_LABEL,
        transverse_elements=TRANSVERSE)
    assert summary == {}
    assert any("Unknown" in t.get_text() for t in fig.texts)


def test_flat_tag_list_is_accepted_as_transverse_input():
    _, summary = _plot("Mz", transverse=[1, 2, 5, 6, 11, 12])
    assert sorted(summary) == ["T1", "T2", "T3"]


# =============================================================================
# UI wiring (needs PySide6)
# =============================================================================

def test_member_dropdown_lists_girders_then_transverse_members_once():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication, QComboBox
    from osdagbridge.core.utils.common import KEY_ANALYSIS_MEMBER, KEY_TS_NO_OF_GIRDERS
    from osdagbridge.desktop.ui.docks.output_dock import OutputDock

    app = QApplication.instance() or QApplication([])
    combo = QComboBox()
    stub = types.SimpleNamespace(
        backend=types.SimpleNamespace(input_dict={KEY_TS_NO_OF_GIRDERS: 3}),
        _w=lambda key: combo if key == KEY_ANALYSIS_MEMBER else None,
    )
    OutputDock.refresh_member_dropdown(stub)
    items = [combo.itemText(i) for i in range(combo.count())]
    assert items == ["All", "G1", "G2", "G3", TRANSVERSE_MEMBERS_LABEL]
    assert items.count(TRANSVERSE_MEMBERS_LABEL) == 1
    del app


def test_results_match_real_xarray_dataset():
    xr = pytest.importorskip("xarray")
    elems = sorted(MEMBERS)
    arr = np.array([[raw_force(0, e, c) for c in range(len(FORCE_COMPS))] for e in elems])
    ds = xr.Dataset({"forces": (("Element", "Component"), arr)},
                    coords={"Element": elems, "Component": FORCE_COMPS})
    out = rd.extract_transverse_force_profiles(ds, _lines((5, 6)), "Mz_i", "Mz_j")
    assert out["profiles"][0]["values"].tolist() == pytest.approx(
        [kn(0, 5, "Mz_i"), kn(0, 6, "Mz_i"), kn(0, 6, "Mz_j")])
