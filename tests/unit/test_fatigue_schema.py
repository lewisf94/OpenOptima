"""A load cycle has to be reachable from a project file, and hashed once it is.

Two separate failures are guarded here, and the project has been bitten by
both before.

**A capability nobody can reach is not done** -- trap 17 in ``AGENTS.md``,
where the whole directional-strength stack was built and verified and no
project file could switch it on.

**Anything that can change a number belongs in the evaluation hash.** Which
two load cases are paired into a cycle decides the reported swing, and so does
the convention that gives a mean stress its sign. A cached result computed
under one is not a cache hit for the other.
"""

from __future__ import annotations

import re

import pytest
import yaml

from openoptima.domain.fatigue import EquivalentStress, FatigueSettings, LoadCycle
from openoptima.schema.project_schema import ProjectSchema

BASE = {
    "schema_version": 1,
    "name": "fatigue wiring",
    "geometry": {"provider": "occ", "template": "l_bracket"},
    "regions": [
        {"name": "held", "selector": {"surface_type": "plane", "normal": [-1.0, 0.0, 0.0]}},
        {"name": "pushed", "selector": {"surface_type": "plane", "normal": [1.0, 0.0, 0.0]}},
    ],
    "material": {
        "name": "steel",
        "elastic_modulus_mpa": 210000.0,
        "poisson_ratio": 0.3,
        "density_kg_m3": 7850.0,
        "allowable_stress_mpa": 200.0,
    },
    "load_cases": [
        {
            "id": "push",
            "boundary_conditions": [{"region": "held", "kind": "fixed", "dofs": [1, 2, 3]}],
            "loads": [{"kind": "force", "region": "pushed", "vector": [0.0, 0.0, -100.0]}],
        },
        {
            "id": "pull",
            "boundary_conditions": [{"region": "held", "kind": "fixed", "dofs": [1, 2, 3]}],
            "loads": [{"kind": "force", "region": "pushed", "vector": [0.0, 0.0, 100.0]}],
        },
    ],
    "mesh": {"global_size": 6.0, "minimum_size": 1.5},
    "objectives": [{"metric": "mass_kg"}],
}


def _project(**fatigue):
    payload = dict(BASE)
    if fatigue:
        payload["fatigue"] = fatigue
    return ProjectSchema.model_validate(payload).to_domain()


def test_a_project_file_can_describe_a_load_cycle() -> None:
    """The trap-17 check: load it from a project file, or it is not finished."""
    project = _project(enabled=True, cycles=[{"name": "reversal", "between": ["push", "pull"]}])
    assert project.fatigue.enabled
    assert project.fatigue.cycles[0].name == "reversal"
    assert project.fatigue.cycles[0].between == ("push", "pull")
    assert project.fatigue.equivalent_stress is EquivalentStress.SIGNED_MISES_TRACE


def test_fatigue_is_off_unless_a_project_asks_for_it() -> None:
    project = _project()
    assert not project.fatigue.enabled
    assert project.fatigue.cycles == ()


def test_a_cycle_naming_an_unknown_load_case_is_refused_at_load_time() -> None:
    """A typo would otherwise fail every design in a study. Caught here it is
    one line, before anything is meshed."""
    with pytest.raises(ValueError, match="no such load case is defined"):
        _project(enabled=True, cycles=[{"name": "oops", "between": ["push", "shove"]}])


def test_a_cycle_with_the_same_case_at_both_ends_is_refused() -> None:
    """Nothing swings, so there is no cycle to measure."""
    with pytest.raises(ValueError, match="same load case at both ends"):
        _project(enabled=True, cycles=[{"name": "still", "between": ["push", "push"]}])


def test_fatigue_switched_on_with_no_cycle_is_refused() -> None:
    with pytest.raises(ValueError, match="no load cycle is described"):
        _project(enabled=True, cycles=[])


def test_a_cycle_needs_exactly_two_ends() -> None:
    with pytest.raises(ValueError, match="exactly two"):
        _project(enabled=True, cycles=[{"name": "three", "between": ["push", "pull", "push"]}])


def _digest(**fatigue) -> str:
    return _project(**fatigue).setup_digest()


def test_pairing_different_load_cases_changes_the_hash() -> None:
    """A different pairing is a different swing, so it is a different question
    and an old answer is not a cache hit for it."""
    payload = dict(BASE)
    payload["load_cases"] = [
        *BASE["load_cases"],
        {
            "id": "half",
            "boundary_conditions": [{"region": "held", "kind": "fixed", "dofs": [1, 2, 3]}],
            "loads": [{"kind": "force", "region": "pushed", "vector": [0.0, 0.0, 50.0]}],
        },
    ]

    def digest(between):
        body = dict(payload)
        body["fatigue"] = {"enabled": True, "cycles": [{"name": "c", "between": between}]}
        return ProjectSchema.model_validate(body).to_domain().setup_digest()

    assert digest(["push", "pull"]) != digest(["push", "half"])


def test_changing_the_sign_convention_changes_the_hash() -> None:
    """It decides whether a mean stress reads as pulling the material apart or
    pressing it together, which is a different number."""
    cycles = [{"name": "c", "between": ["push", "pull"]}]
    first = _digest(enabled=True, cycles=cycles, equivalent_stress="signed_mises_trace")
    second = _digest(
        enabled=True, cycles=cycles, equivalent_stress="signed_mises_abs_max_principal"
    )
    assert first != second


def test_switching_fatigue_on_changes_the_hash() -> None:
    off = _digest()
    on = _digest(enabled=True, cycles=[{"name": "c", "between": ["push", "pull"]}])
    assert off != on


def test_the_settings_refuse_two_cycles_with_one_name() -> None:
    with pytest.raises(ValueError, match="both called"):
        FatigueSettings(
            enabled=True,
            cycles=(
                LoadCycle(name="same", between=("a", "b")),
                LoadCycle(name="same", between=("a", "c")),
            ),
        )


def test_the_documented_example_is_valid_yaml_and_loads() -> None:
    """The block in the schema docstring is what a user copies. If it drifts
    from what the schema accepts, they get an error on their first attempt."""
    from openoptima.schema.project_schema import FatigueSchema

    doc = FatigueSchema.__doc__ or ""
    match = re.search(r"\n( {8}fatigue:\n(?: {8}.*\n| *\n)*)", doc)
    assert match, "the fatigue docstring no longer contains an indented example block"
    snippet = "\n".join(line[8:] for line in match.group(1).splitlines())
    parsed = yaml.safe_load(snippet)["fatigue"]
    loaded = FatigueSchema.model_validate(parsed)
    assert loaded.enabled
    assert loaded.cycles[0].between == ["thrust_up", "thrust_down"]


def test_a_project_file_can_supply_a_fatigue_curve() -> None:
    """The trap-17 check again: a curve nobody can reach is not a feature."""
    project = _project(
        enabled=True,
        cycles=[{"name": "reversal", "between": ["push", "pull"], "repeats": 1.0e6}],
        curve={
            "endurance_stress_mpa": 100.0,
            "endurance_cycles": 1.0e7,
            "slope": 5.0,
            "mean_stress_sensitivity": 0.3,
        },
    )
    curve = project.fatigue.curve
    assert curve is not None
    assert curve.endurance_stress == 100.0
    assert curve.slope == 5.0
    assert curve.mean_stress_sensitivity == 0.3
    assert project.fatigue.cycles[0].repeats == 1.0e6


def test_a_swing_without_a_curve_is_a_complete_answer_on_its_own() -> None:
    """Reporting how far the stress swings is useful without a life, and is
    what a project gets when it has no curve for its material."""
    project = _project(enabled=True, cycles=[{"name": "c", "between": ["push", "pull"]}])
    assert project.fatigue.curve is None
    assert project.fatigue.cycles[0].repeats is None


def test_changing_the_curve_changes_the_hash() -> None:
    """A different curve is a different life, so an old answer is not a cache
    hit for it."""
    cycles = [{"name": "c", "between": ["push", "pull"]}]
    base = {"endurance_stress_mpa": 100.0, "endurance_cycles": 1.0e7, "slope": 5.0}
    first = _digest(enabled=True, cycles=cycles, curve=base)
    for change in (
        {"slope": 6.0},
        {"endurance_stress_mpa": 90.0},
        {"mean_stress_sensitivity": 0.3},
    ):
        assert _digest(enabled=True, cycles=cycles, curve=base | change) != first


def test_changing_how_often_a_cycle_repeats_changes_the_hash() -> None:
    """It changes the damage total, which is a reported number."""
    curve = {"endurance_stress_mpa": 100.0, "endurance_cycles": 1.0e7, "slope": 5.0}
    once = _digest(
        enabled=True,
        cycles=[{"name": "c", "between": ["push", "pull"], "repeats": 1e5}],
        curve=curve,
    )
    twice = _digest(
        enabled=True,
        cycles=[{"name": "c", "between": ["push", "pull"], "repeats": 2e5}],
        curve=curve,
    )
    assert once != twice


def test_a_cycle_that_never_happens_is_refused() -> None:
    with pytest.raises(ValueError, match="not a number of cycles"):
        _project(
            enabled=True,
            cycles=[{"name": "c", "between": ["push", "pull"], "repeats": 0.0}],
        )
