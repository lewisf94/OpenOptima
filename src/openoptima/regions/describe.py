"""Turning a picked face into a description that survives the shape changing.

This is the inverse of :mod:`matcher`. The matcher takes a description and
finds the face; this takes the face and writes the description.

**Why this cannot simply record which face was clicked.** A click in a 3D
viewer identifies a face by its tag, and a tag is exactly what this project
refuses to store: ``Face12`` before a dimension changes is not ``Face12``
after it. Storing the click as a number means the load eventually lands on a
different surface, the analysis still runs, and the answer is wrong with
nothing to say so. So a click has to be turned into a *description* -- the
same kind a user would have written by hand -- and that description has to be
proved to work before anybody relies on it.

## One build is not enough to write a description from

This was measured rather than assumed, and it is the reason this module takes
more than one shape. Descriptions generated from the default L-bracket and
then re-resolved at the extremes of its design range failed in two different
ways, neither of which a single-build check could have caught:

* The internal fillet's radius **is** a design variable, 3 to 25 mm. A radius
  range written from the 8 mm default matched nothing at either extreme.
* Far worse, the two bolt holes were described by their 4.5 mm radius, and at
  the smallest fillet setting that range also caught the 3 mm fillet. The
  description silently selected **three** faces where two were picked, with no
  error at all -- a constraint or a load would have been applied to the fillet
  as though it were a bolt hole.

The second is the failure this project exists to prevent, and it appears on a
perfectly ordinary part with perfectly ordinary selectors. So a candidate
description is checked against every shape it is given, and one that fails on
any of them is rejected before it is ever returned. Callers should pass the
extremes of the design range; :func:`describe_faces` warns when given nothing
to check against.

## The rule the filters follow

**Every filter is made as loose as it can be while still excluding the
competition.**

A description is written once and re-resolved against every shape the
optimiser invents afterwards, so each constraint is a thing that can break
later. Pin the area to what it is now and the description stops matching the
moment the part grows. Loosen too far and it starts matching the face next
door, which is worse. So each filter is widened until just before it would
admit a face that is not wanted, and no further. Where nothing competes on a
property at all, that side is left unbounded rather than given an invented
limit.

## Which properties are preferred, and why

Candidate descriptions are tried fewest-filters-first, because every extra
filter is another thing that can stop being true. Among equals the order
reflects how well a property survives a rebuild: what kind of surface it is
(a flat face stays flat), then which way it points or how big it is, then
roughly where it sits, and only as a last resort a score comparing it against
the other candidates.

Area is never used as a hard filter. It is the property that moves most when a
dimension changes.

## Nothing is emitted unproved

Every selector returned has been resolved through
:func:`~openoptima.regions.matcher.resolve_region` -- the real resolver, not a
copy of it -- on every shape supplied, and confirmed to select exactly the
faces that were picked. If nothing isolates them, this raises. Refusing is
correct: two faces that genuinely cannot be told apart need the user to say
which they meant, and a guess would look exactly like success.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, replace

from ..domain.failures import EvaluationFailure, FailureCode
from ..domain.regions import (
    BoundingBox,
    FaceSignature,
    RegionSelector,
    SelectionMode,
    SemanticRegion,
    SurfaceType,
)
from .matcher import passes_filters, resolve_region
from .signature import angle_between

#: How much of the way to the nearest unwanted face a filter may reach. At 0.5
#: a box sits exactly halfway between the faces it must keep and the nearest
#: one it must exclude -- as generous as it can be while leaving the same
#: clearance on the other side. Higher buys tolerance to the shape changing at
#: the cost of margin against picking the wrong face, and those are not equally
#: bad: a description that stops matching fails loudly, one that matches the
#: wrong face does not.
_REACH = 0.5

#: A plane's normal is compared with this tolerance. Wide enough for a face
#: that tilts slightly as the shape rebuilds, far tighter than the angle
#: between any two faces a user would want to tell apart.
_NORMAL_TOLERANCE_DEG = 5.0

#: Beyond this angle from an axis, a normal is quoted numerically rather than
#: called "+X" and friends.
_AXIS_SNAP_DEG = 15.0

#: Two measurements closer together than this, relative to their own size, are
#: the same measurement: a filter boundary cannot be placed between them.
#:
#: **This exists because ignoring it produced a description that worked by
#: luck.** The L-bracket's two bolt holes are both 9 mm across, and the circle
#: fit returns 4.5 and 4.499999999999495 for them -- a difference of 5.05e-13
#: mm, which is fitting noise and nothing else. The "as loose as possible" rule
#: then dutifully placed a radius boundary inside that gap, producing a filter
#: that selected exactly one of the two holes on the shape it was written from.
#:
#: It survived being checked against the design range as well, and that is the
#: part worth remembering: the noise is *deterministic*. Same kernel, same
#: operations, same rounding, so the identical 5.05e-13 appeared at every
#: design point. **Checking a description against more shapes cannot catch a
#: defect whose cause is deterministic**, so this has to be caught here, by
#: refusing to treat an unmeaningful gap as a real one.
#:
#: 1e-6 sits seven orders of magnitude above the measured noise and three below
#: the finest distinction any real design would draw between two features.
_MEANINGFUL_GAP = 1.0e-6


@dataclass(frozen=True)
class BuildSample:
    """One shape to check a candidate description against."""

    signatures: list[FaceSignature]
    scale_length: float
    label: str = ""


@dataclass(frozen=True)
class DescribedRegion:
    """A generated selector, and the evidence that it works."""

    selector: RegionSelector
    #: Which properties were needed. Fewer is better: every one is a thing
    #: that can stop being true when the shape changes.
    filters_used: tuple[str, ...]
    #: How far clear of the next-best candidate on the shape it was written
    #: from. ``inf`` means the hard filters alone isolated the face, which is
    #: the strongest outcome -- no comparison between candidates is involved.
    margin: float
    #: Plain-English rendering, for showing back to whoever clicked.
    explanation: str
    #: How many other shapes it was confirmed against.
    checked_against: int = 0
    warnings: tuple[str, ...] = ()

    @property
    def isolated_by_filters_alone(self) -> bool:
        return math.isinf(self.margin)


@dataclass(frozen=True)
class _Fingerprint:
    """What "the same faces" means on a shape whose tags cannot be compared."""

    count: int
    types: frozenset[SurfaceType]
    normals: tuple[tuple[float, float, float] | None, ...]

    @classmethod
    def of(cls, faces: Sequence[FaceSignature]) -> _Fingerprint:
        ordered = sorted(faces, key=lambda f: (-f.area, f.tag))
        return cls(
            count=len(faces),
            types=frozenset(f.surface_type for f in faces),
            normals=tuple(f.normal for f in ordered),
        )

    def disagreement(self, other: _Fingerprint) -> str:
        if self.count != other.count:
            return f"selects {other.count} face(s) instead of {self.count}"
        if self.types != other.types:
            got = ", ".join(sorted(t.value for t in other.types))
            want = ", ".join(sorted(t.value for t in self.types))
            return f"selects {got} where it should select {want}"
        for mine, theirs in zip(self.normals, other.normals, strict=False):
            if mine is None or theirs is None:
                continue
            if angle_between(mine, theirs) > 30.0:
                return "selects a face pointing a noticeably different way"
        return ""


def _matches(selector: RegionSelector, signatures: Sequence[FaceSignature]) -> set[int]:
    """Tags passing the hard filters, using the resolver's own logic."""
    return {s.tag for s in signatures if passes_filters(s, selector)[0]}


def _box_around(
    targets: Sequence[FaceSignature],
    others: Sequence[FaceSignature],
    scale_length: float,
) -> BoundingBox | None:
    """The most generous box holding every target centroid and no other.

    Grown isotropically until just before the nearest unwanted centroid would
    fall inside. A point enters an isotropically padded box only once the pad
    reaches its largest per-axis overhang, so padding by less than the smallest
    such overhang across all unwanted faces cannot admit any of them.

    A face sitting only a hair outside cannot be excluded by position: see
    :data:`_MEANINGFUL_GAP`. Building a razor-thin box around that hair would
    produce a filter that separates two faces by numerical noise.
    """
    xs = [t.centroid[0] for t in targets]
    ys = [t.centroid[1] for t in targets]
    zs = [t.centroid[2] for t in targets]
    tight = BoundingBox(min(xs), min(ys), min(zs), max(xs), max(ys), max(zs))

    def overhang(point: tuple[float, float, float]) -> float:
        return max(
            tight.xmin - point[0],
            point[0] - tight.xmax,
            tight.ymin - point[1],
            point[1] - tight.ymax,
            tight.zmin - point[2],
            point[2] - tight.zmax,
        )

    room = min((overhang(o.centroid) for o in others), default=None)
    if room is None:
        # Nothing to exclude: pad by the targets' own extent so the box still
        # tolerates the shape moving.
        room = max(
            tight.xmax - tight.xmin,
            tight.ymax - tight.ymin,
            tight.zmax - tight.zmin,
            1.0,
        )
    elif room <= _MEANINGFUL_GAP * max(scale_length, 1e-12):
        # Something unwanted sits inside the targets' own box, or so close to
        # it that only rounding separates them. No box can tell them apart.
        return None

    pad = _REACH * room
    return BoundingBox(
        tight.xmin - pad,
        tight.ymin - pad,
        tight.zmin - pad,
        tight.xmax + pad,
        tight.ymax + pad,
        tight.zmax + pad,
    )


def _radius_bracket(
    targets: Sequence[FaceSignature], others: Sequence[FaceSignature]
) -> tuple[float | None, float | None] | None:
    """The widest radius range covering every target and no other round face.

    Widened towards the nearest unwanted radius rather than by a fixed
    percentage. Where nothing competes on one side, that side is left
    unbounded: inventing a limit there would reject a perfectly good face for
    no reason, which is what a doubled upper bound did to a fillet whose radius
    was a design variable running to 25 mm.
    """
    radii = [t.radius for t in targets]
    if any(r is None for r in radii):
        return None
    low, high = min(radii), max(radii)  # type: ignore[type-var]
    assert low is not None and high is not None

    # Only a radius that differs *meaningfully* can be filtered against. A
    # competitor within the noise floor is the same size for this purpose, so
    # it is excluded from the bracket calculation entirely -- it will still
    # pass the resulting filter, which is correct, and the caller will then
    # reject radius-alone and reach for a property that does discriminate.
    other_radii = [
        r
        for o in others
        if (r := o.radius) is not None
        and abs(r - (low if r < low else high)) > _MEANINGFUL_GAP * max(abs(r), abs(low))
    ]
    below = [r for r in other_radii if r < low]
    above = [r for r in other_radii if r > high]

    lower = low - _REACH * (low - max(below)) if below else None
    upper = high + _REACH * (min(above) - high) if above else None
    if lower is None and upper is None:
        return None
    return lower, upper


def _candidates(
    targets: Sequence[FaceSignature],
    signatures: Sequence[FaceSignature],
    mode: SelectionMode,
    scale_length: float,
) -> list[tuple[RegionSelector, list[str]]]:
    """Candidate descriptions, most robust first."""
    wanted = {t.tag for t in targets}
    others = [s for s in signatures if s.tag not in wanted]

    types = {t.surface_type for t in targets}
    shared_type = next(iter(types)) if len(types) == 1 else SurfaceType.ANY
    base = RegionSelector(surface_type=shared_type, mode=mode)

    normals = [t.normal for t in targets]
    normal: tuple[float, float, float] | None = None
    if shared_type is SurfaceType.PLANE and all(n is not None for n in normals):
        reference = normals[0]
        if reference is not None and all(
            angle_between(n, reference) <= _NORMAL_TOLERANCE_DEG for n in normals if n is not None
        ):
            normal = reference

    bracket = (
        _radius_bracket(targets, others)
        if shared_type is SurfaceType.CYLINDER and all(t.radius is not None for t in targets)
        else None
    )

    def with_box(selector: RegionSelector) -> RegionSelector | None:
        """A box sized against whatever this selector has not already excluded."""
        surviving = _matches(selector, signatures)
        competitors = [s for s in signatures if s.tag in surviving and s.tag not in wanted]
        box = _box_around(targets, competitors, scale_length)
        return replace(selector, within_box=box) if box is not None else None

    out: list[tuple[RegionSelector, list[str]]] = [(base, ["surface type"])]

    shaped: list[tuple[RegionSelector, list[str]]] = []
    if normal is not None:
        shaped.append(
            (
                replace(base, normal=normal, normal_tolerance_deg=_NORMAL_TOLERANCE_DEG),
                ["surface type", "direction it faces"],
            )
        )
    if bracket is not None:
        shaped.append(
            (
                replace(base, min_radius=bracket[0], max_radius=bracket[1]),
                ["surface type", "radius"],
            )
        )
    out.extend(shaped)

    # Then the same again with a position box, and finally the bare box.
    for selector, names in list(shaped):
        boxed = with_box(selector)
        if boxed is not None:
            out.append((boxed, [*names, "where it is"]))
    bare_box = with_box(base)
    if bare_box is not None:
        out.append((bare_box, ["surface type", "where it is"]))

    if mode is SelectionMode.SINGLE:
        # Scoring is a comparison between candidates rather than a fact about
        # one face, so it can flip as the shape changes. Last resort only.
        centre = targets[0].centroid
        for selector, names in list(out):
            out.append(
                (
                    replace(selector, centroid_near=centre),
                    [*names, "nearest to where you clicked"],
                )
            )
    return out


def _works_on(
    selector: RegionSelector,
    sample: BuildSample,
    expected: _Fingerprint,
    name: str,
) -> tuple[bool, str, float]:
    """Does this description pick the right faces on one shape?

    Tags cannot be compared between shapes -- they are precisely what does not
    survive -- so the check is on what the selected faces *are*: how many, what
    kind, and which way they point.
    """
    try:
        match = resolve_region(
            SemanticRegion(name=name, selector=selector),
            list(sample.signatures),
            scale_length=sample.scale_length,
        )
    except EvaluationFailure as exc:
        return False, exc.message.split(".")[0], 0.0

    problem = expected.disagreement(_Fingerprint.of(match.signatures))
    return (not problem), problem, match.margin


def describe_faces(
    targets: Sequence[FaceSignature],
    signatures: Sequence[FaceSignature],
    *,
    scale_length: float,
    name: str = "region",
    alternatives: Sequence[BuildSample] = (),
    shape_can_change: bool = True,
) -> DescribedRegion:
    """Write a durable description of the picked faces.

    ``targets`` are the faces the user chose -- one for a load face, several
    for something like "both bolt holes". ``signatures`` is every face of the
    solid they were chosen on.

    ``alternatives`` should carry the same part built at the extremes of its
    design range. A description that cannot be checked against a shape that has
    actually changed is a description nobody has tested, and this warns when
    none is supplied.

    Set ``shape_can_change`` to ``False`` when the part has no design variables
    at all -- an imported CAD file, most often. There is then only ever one
    shape, so a description written from it is complete rather than untested,
    and warning about it would be telling the user to go and check something
    that cannot vary.

    Raises :class:`~openoptima.domain.failures.EvaluationFailure` when the
    picked faces cannot be told apart from the rest on every supplied shape,
    rather than returning a description that would silently select the wrong
    surface later.
    """
    if not targets:
        raise EvaluationFailure(
            FailureCode.INTERNAL_ERROR, "describe_faces needs at least one face"
        )

    wanted = {t.tag for t in targets}
    missing = wanted - {s.tag for s in signatures}
    if missing:
        raise EvaluationFailure(
            FailureCode.INTERNAL_ERROR,
            f"picked face(s) {sorted(missing)} are not part of this solid",
        )

    mode = SelectionMode.SINGLE if len(targets) == 1 else SelectionMode.ALL
    expected = _Fingerprint.of(targets)
    here = BuildSample(list(signatures), scale_length, "the shape you picked on")

    rejected: list[str] = []
    for selector, names in _candidates(targets, signatures, mode, scale_length):
        if _matches(selector, signatures) != wanted and selector.centroid_near is None:
            continue

        ok, problem, margin = _works_on(selector, here, expected, name)
        if not ok:
            rejected.append(f"{' + '.join(names)}: {problem}")
            continue

        failed_elsewhere = ""
        for sample in alternatives:
            ok, problem, _margin = _works_on(selector, sample, expected, name)
            if not ok:
                where = sample.label or "another shape"
                failed_elsewhere = f"{' + '.join(names)}: on {where}, {problem}"
                break
        if failed_elsewhere:
            rejected.append(failed_elsewhere)
            continue

        warnings: list[str] = []
        if selector.centroid_near is not None:
            warnings.append(
                "This face could not be separated from the others by what it is "
                "and where it is alone, so the description falls back on picking "
                "the closest match. That is more fragile than the rest: if the "
                "shape changes enough, a different face could become the closest."
            )
        if not alternatives and shape_can_change:
            warnings.append(
                "This description was written from one shape and has not been "
                "checked against any other. Run `openoptima doctor` to confirm "
                "it still finds the right face across the whole design range."
            )
        return DescribedRegion(
            selector=selector,
            filters_used=tuple(names),
            margin=margin,
            explanation=explain(selector, targets),
            checked_against=len(alternatives),
            warnings=tuple(warnings),
        )

    raise EvaluationFailure(
        FailureCode.REGION_AMBIGUOUS,
        f"Cannot write a description for {name!r} that reliably picks the "
        f"chosen face(s) and nothing else. Every candidate failed:\n  "
        + "\n  ".join(rejected[:8])
        + "\nPick the faces you want together, or choose a face that can be "
        "told apart from its neighbours.",
        detail={
            "region": name,
            "picked": sorted(wanted),
            "rejected": rejected[:16],
        },
    )


def _axis_name(vector: tuple[float, float, float]) -> str:
    """The nearest axis direction, for explaining a normal to a human."""
    axes = {
        "+X": (1.0, 0.0, 0.0),
        "-X": (-1.0, 0.0, 0.0),
        "+Y": (0.0, 1.0, 0.0),
        "-Y": (0.0, -1.0, 0.0),
        "+Z": (0.0, 0.0, 1.0),
        "-Z": (0.0, 0.0, -1.0),
    }
    best, best_angle = "", 180.0
    for label, axis in axes.items():
        angle = angle_between(vector, axis)
        if angle < best_angle:
            best, best_angle = label, angle
    return best if best_angle <= _AXIS_SNAP_DEG else ""


def explain(selector: RegionSelector, targets: Sequence[FaceSignature] | None = None) -> str:
    """A one-line, plain-English rendering of what a selector looks for."""
    kind = {
        SurfaceType.PLANE: "flat face",
        SurfaceType.CYLINDER: "round face",
        SurfaceType.SPHERE: "spherical face",
        SurfaceType.CONE: "conical face",
        SurfaceType.TORUS: "curved face",
        SurfaceType.ANY: "face",
        SurfaceType.OTHER: "face",
    }[selector.surface_type]

    if selector.mode is SelectionMode.ALL:
        parts = [f"all {len(targets)} {kind}s" if targets else f"every {kind}"]
    else:
        parts = [f"the {kind}"]

    if selector.normal is not None:
        axis = _axis_name(selector.normal)
        parts.append(
            f"pointing {axis}"
            if axis
            else "pointing ({:.2f}, {:.2f}, {:.2f})".format(*selector.normal)
        )

    if selector.min_radius is not None or selector.max_radius is not None:
        measured = [t.radius for t in targets or () if t.radius is not None]
        if measured:
            parts.append(f"about {min(measured):.4g} mm in radius")
        elif selector.min_radius is not None and selector.max_radius is not None:
            parts.append(
                f"between {selector.min_radius:.4g} and {selector.max_radius:.4g} mm in radius"
            )
        elif selector.min_radius is not None:
            parts.append(f"over {selector.min_radius:.4g} mm in radius")
        else:
            parts.append(f"under {selector.max_radius:.4g} mm in radius")

    if selector.within_box is not None:
        parts.append("in this part of the model")

    if selector.centroid_near is not None:
        parts.append("closest to where you clicked")

    return " ".join(parts)
