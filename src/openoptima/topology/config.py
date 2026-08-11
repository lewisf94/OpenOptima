"""Describing a topology problem to ``beso``, in the form it expects.

``beso`` is configured by a file called ``beso_conf.py`` which it reads with
``exec()``. So this module generates **Python source code**, and two things
follow from that which would not matter for an ordinary settings file.

**Every value is written with ``repr()``.** A Windows path contains
backslashes, and ``"C:\\Users\\..."`` written naively into Python source
becomes a string with a tab and a form feed in it. ``repr()`` escapes it
correctly. Nothing here is ever built by pasting a value into a template.

**beso reads the file from its own directory, not the working directory.** So
each run needs its own copy of beso's scripts; two runs sharing one copy would
overwrite each other's settings. :mod:`openoptima.topology.runner` does that
copying.

The mapping from OpenOptima's settings to beso's is deliberately narrow. Only
the options this project has a use for are written; everything else keeps
beso's own default.
"""

from __future__ import annotations

from pathlib import Path

from ..domain.model import Material
from ..domain.topology import TopologySettings

#: What the optimiser is trying to do. Deliberately not the full set beso
#: offers.
#:
#: ``buckling`` is **absent on purpose and must not be added without
#: measurement.** CalculiX silently returns the second buckling mode instead of
#: the first when the true factor falls below about 0.52 -- roughly nine times
#: too high, in the unsafe direction, with nothing in its output to say so.
#: OpenOptima corrects this by solving against a much smaller reference load.
#: beso drives the same solver and has no such correction, so its buckling
#: objective must be assumed affected until a benchmark says otherwise. An
#: optimiser acting on a buckling factor nine times too high would drive a
#: design straight at the failure it was asked to avoid.
OBJECTIVES = {
    "stiffness": "stiffness",
    "failure_index": "failure_index",
}

#: How much softer a removed element is than a solid one. Not zero: an element
#: with no stiffness at all makes the solver's matrix singular, so the standard
#: approach keeps a token stiffness that is negligible structurally.
VOID_STIFFNESS_RATIO = 1.0e-6

#: The element set beso optimises. ``all_available`` is beso's own keyword for
#: "every element in the file", which is what OpenOptima wants: the design
#: region is decided by the geometry we meshed, not by a set inside the deck.
ELEMENT_SET = "all_available"


class UnsupportedObjective(ValueError):
    """An objective beso offers that OpenOptima will not pass on unchecked."""


def _material_card(material: Material, stiffness_scale: float = 1.0) -> str:
    """One CalculiX ``*ELASTIC`` card, as beso wants it: a string with ``\\n``.

    beso writes this straight into the deck it generates, after its own
    ``*MATERIAL`` line.
    """
    modulus = material.elastic_modulus * stiffness_scale
    return f"*ELASTIC \n{modulus:.9g},  {material.poisson_ratio:.9g}"


def objective_for(name: str) -> str:
    """Translate an OpenOptima objective name, or refuse it."""
    if name not in OBJECTIVES:
        offered = ", ".join(sorted(OBJECTIVES))
        extra = ""
        if name == "buckling":
            extra = (
                " Buckling is deliberately not available: CalculiX can silently "
                "report a buckling factor about nine times too high, in the unsafe "
                "direction, and beso has no correction for it. Optimising against "
                "that number would drive the design at the very failure it was "
                "asked to avoid. It stays unavailable until a benchmark measures it."
            )
        raise UnsupportedObjective(
            f"unknown topology objective {name!r}; expected one of {offered}.{extra}"
        )
    return OBJECTIVES[name]


def render_config(
    *,
    settings: TopologySettings,
    material: Material,
    solver_executable: Path | str,
    working_directory: Path | str,
    deck_name: str,
    objective: str = "stiffness",
    allowable_stress_mpa: float | None = None,
    cpu_cores: int = 0,
) -> str:
    """Produce the contents of a ``beso_conf.py`` for one run.

    ``working_directory`` must contain no spaces. On Windows beso starts the
    solver through the shell, which joins its arguments into one string and
    breaks on any path containing a space. :mod:`openoptima.topology.workspace`
    chooses a safe directory; this function does not check, because by the time
    it is called the choice has already been made.
    """
    base = objective_for(objective)

    if base == "failure_index" and allowable_stress_mpa is None:
        raise UnsupportedObjective(
            "the failure_index objective needs an allowable stress, and none was "
            "given. Allowable stress is a design decision this software will not "
            "infer -- see docs/engineering-assumptions.md."
        )

    # beso works in whatever units the deck uses. OpenOptima's decks are always
    # in the internal mm/N/MPa/t system, so the numbers below need no
    # conversion -- but they must never be taken from user input directly.
    solid = _material_card(material)
    void = _material_card(material, VOID_STIFFNESS_RATIO)

    if base == "failure_index":
        assert allowable_stress_mpa is not None  # narrowed by the check above
        failure_indices = (
            f"[[('stress_von_Mises', {allowable_stress_mpa!r})], "
            f"[('stress_von_Mises', {allowable_stress_mpa!r})]]"
        )
    else:
        # beso only evaluates failure indices when they are defined, and an
        # empty list is its documented way of saying "do not".
        failure_indices = "[]"

    radius = settings.effective_filter_radius_mm

    lines = [
        "# Generated by OpenOptima. Do not edit: it is rewritten on every run.",
        "#",
        "# beso reads this file with exec(), from its own directory. Every value",
        "# below is written with repr() so that a Windows path cannot turn into",
        "# escape characters.",
        "",
        f"path = {str(working_directory)!r}",
        f"path_calculix = {str(solver_executable)!r}",
        f"file_name = {deck_name!r}",
        "",
        f"elset_name = {ELEMENT_SET!r}",
        "domain_optimized[elset_name] = True",
        f"domain_density[elset_name] = [{VOID_STIFFNESS_RATIO!r}, 1.0]",
        f"domain_material[elset_name] = [{void!r}, {solid!r}]",
        f"domain_FI[elset_name] = {failure_indices}",
        "domain_same_state[elset_name] = False",
        "",
        f"optimization_base = {base!r}",
        f"mass_goal_ratio = {settings.volume_fraction!r}",
        "",
        "# The smoothing radius is what makes the minimum feature size mean",
        "# anything: the filter blurs material over its own radius, so a feature",
        "# narrower than twice that radius cannot survive it.",
        f"filter_list = [['simple', {radius!r}]]",
        "",
        f"mass_removal_ratio = {settings.evolution_rate!r}",
        f"mass_addition_ratio = {settings.evolution_rate * 0.5!r}",
        "ratio_type = 'relative'",
        f"iterations_limit = {settings.maximum_iterations!r}",
        "",
        f"cpu_cores = {cpu_cores!r}",
        "",
        "# Ask for the mesh back in CalculiX's own format. Everything beso",
        "# produces has to go back through OpenOptima's ordinary evaluation on a",
        "# body-fitted mesh before any number from it is reported, and an .inp",
        "# is what that pipeline can read.",
        "save_resulting_format = 'inp vtk'",
        "save_iteration_results = 0",
        "",
    ]
    return "\n".join(lines)
