"""Reading a shape out of an imported CAD file, against the real OCC kernel.

The import itself is not new machinery -- ``cadquery_provider.py`` already
re-imports its own STEP export through the identical call,
``gmsh.model.occ.importShapes``. What has to be checked here is everything
around that call: does a known shape survive the round trip with its size
intact (a units mistake would be silent and would not look like an error),
does more than one solid get refused rather than silently taking the first
one, and is the source file left completely alone.
"""

from __future__ import annotations

import pytest

from openoptima.domain.failures import EvaluationFailure, FailureCode
from openoptima.domain.project import GeometryDefinition
from openoptima.domain.variables import DesignSpace, DesignVariable
from openoptima.geometry import create_provider
from openoptima.geometry.gmsh_session import gmsh_session, suppress_native_output
from openoptima.geometry.occ.provider import OccGeometryProvider
from openoptima.geometry.step_provider import StepGeometryProvider

from ..conftest import requires_gmsh

pytestmark = [requires_gmsh, pytest.mark.gmsh]

LENGTH, WIDTH, HEIGHT = 100.0, 10.0, 5.0
EMPTY_SPACE = DesignSpace(())


def _known_box(tmp_path):
    """A box of exact, known dimensions, built and verified by the existing
    OCC provider -- not by this new code -- so this is a real external
    reference, not the thing under test checking itself."""
    provider = OccGeometryProvider(
        GeometryDefinition(
            provider="occ",
            template="cantilever_box",
            parameters={"length": LENGTH, "width": WIDTH, "height": HEIGHT},
        )
    )
    artifact = provider.build(EMPTY_SPACE.defaults(), tmp_path / "source")
    assert artifact.step_path is not None, "the OCC provider must have written a STEP file"
    return artifact


class TestAKnownShapeSurvivesTheRoundTrip:
    """The one thing that would be silent if it were wrong: size.

    A units mistake -- reading a file's dimensions in the wrong scale -- would
    not throw an error. It would produce a shape that looks entirely normal
    and is the wrong size, and every number computed from it downstream would
    be wrong in a way nobody would notice from the output alone. So this is
    checked against a reference of known size, not merely trusted because the
    import did not raise.
    """

    def test_the_volume_matches(self, tmp_path):
        source = _known_box(tmp_path)
        imported = StepGeometryProvider(
            GeometryDefinition(provider="step", source=str(source.step_path))
        ).build(EMPTY_SPACE.defaults(), tmp_path / "imported")

        expected = LENGTH * WIDTH * HEIGHT
        assert imported.volume == pytest.approx(expected, rel=1e-6)
        assert imported.volume == pytest.approx(source.volume, rel=1e-6)

    def test_the_bounding_box_matches(self, tmp_path):
        source = _known_box(tmp_path)
        imported = StepGeometryProvider(
            GeometryDefinition(provider="step", source=str(source.step_path))
        ).build(EMPTY_SPACE.defaults(), tmp_path / "imported")

        assert imported.bbox.as_tuple() == pytest.approx(source.bbox.as_tuple(), rel=1e-6)

    def test_the_surface_area_matches(self, tmp_path):
        source = _known_box(tmp_path)
        imported = StepGeometryProvider(
            GeometryDefinition(provider="step", source=str(source.step_path))
        ).build(EMPTY_SPACE.defaults(), tmp_path / "imported")

        expected = 2 * (LENGTH * WIDTH + LENGTH * HEIGHT + WIDTH * HEIGHT)
        assert imported.surface_area == pytest.approx(expected, rel=1e-6)

    def test_it_records_which_provider_and_which_file(self, tmp_path):
        source = _known_box(tmp_path)
        imported = StepGeometryProvider(
            GeometryDefinition(provider="step", source=str(source.step_path))
        ).build(EMPTY_SPACE.defaults(), tmp_path / "imported")

        assert imported.metadata["provider"] == "step"
        assert imported.metadata["source"] == str(source.step_path)


class TestTheDesignVectorHasNoEffect:
    """There is nothing in an imported file for a design vector to drive."""

    def test_two_different_design_vectors_give_the_identical_shape(self, tmp_path):
        source = _known_box(tmp_path)
        provider = StepGeometryProvider(
            GeometryDefinition(provider="step", source=str(source.step_path))
        )
        space = DesignSpace((DesignVariable(id="anything", minimum=0.0, maximum=1.0, default=0.0),))

        first = provider.build(space.decode({"anything": 0.0}), tmp_path / "a")
        second = provider.build(space.decode({"anything": 1.0}), tmp_path / "b")

        assert first.volume == pytest.approx(second.volume, rel=1e-12)
        assert first.bbox.as_tuple() == pytest.approx(second.bbox.as_tuple(), rel=1e-12)


class TestAnAssemblyIsRefusedNotSilentlyTruncated:
    """More than one solid means this is not the single part this provider reads.

    Taking the first solid and discarding the rest would build and analyse a
    fragment of whatever the file actually contained, with nothing in the
    output to say that most of the file was thrown away.
    """

    @staticmethod
    def _two_boxes(tmp_path):
        path = tmp_path / "two_solids.step"
        with gmsh_session() as gmsh:
            gmsh.model.add("two_boxes")
            gmsh.model.occ.addBox(0, 0, 0, 10, 10, 10)
            gmsh.model.occ.addBox(20, 0, 0, 10, 10, 10)
            gmsh.model.occ.synchronize()
            assert len(gmsh.model.getEntities(3)) == 2
            with suppress_native_output():
                gmsh.write(str(path))
        return path

    def test_build_refuses_it(self, tmp_path):
        path = self._two_boxes(tmp_path)
        provider = StepGeometryProvider(GeometryDefinition(provider="step", source=str(path)))
        with pytest.raises(EvaluationFailure) as raised:
            provider.build(EMPTY_SPACE.defaults(), tmp_path / "out")
        assert raised.value.code is FailureCode.INVALID_SOLID
        assert "2" in raised.value.message

    def test_validation_catches_it_before_any_build_is_attempted(self, tmp_path):
        path = self._two_boxes(tmp_path)
        provider = StepGeometryProvider(GeometryDefinition(provider="step", source=str(path)))
        report = provider.validate_definition()
        assert not report.ok
        assert "assembly" in report.errors[0]


class TestBadInputIsCaughtByValidationNotByCrashing:
    def test_a_missing_file_fails_validation(self, tmp_path):
        provider = StepGeometryProvider(
            GeometryDefinition(provider="step", source=str(tmp_path / "nothing.step"))
        )
        report = provider.validate_definition()
        assert not report.ok
        assert "not found" in report.errors[0]

    def test_an_unrecognised_extension_fails_validation(self, tmp_path):
        path = tmp_path / "notes.txt"
        path.write_text("this is not a CAD file")
        provider = StepGeometryProvider(GeometryDefinition(provider="step", source=str(path)))
        report = provider.validate_definition()
        assert not report.ok
        assert "extension" in report.errors[0]

    def test_a_corrupt_step_file_fails_validation_rather_than_crashing(self, tmp_path):
        path = tmp_path / "corrupt.step"
        path.write_text("this has the right extension but is not a STEP file at all")
        provider = StepGeometryProvider(GeometryDefinition(provider="step", source=str(path)))
        report = provider.validate_definition()  # must not raise
        assert not report.ok

    def test_build_also_refuses_a_missing_file_with_a_classified_error(self, tmp_path):
        """Reaching build() with a missing file means it vanished after
        validation passed -- still not a property of any particular design."""
        provider = StepGeometryProvider(
            GeometryDefinition(provider="step", source=str(tmp_path / "nothing.step"))
        )
        with pytest.raises(EvaluationFailure) as raised:
            provider.build(EMPTY_SPACE.defaults(), tmp_path / "out")
        assert raised.value.code is FailureCode.INTERNAL_ERROR

    def test_a_missing_import_is_never_classified_as_a_bad_design(self, tmp_path):
        """A missing or corrupt file fails identically for every design, so it
        cannot be information about any one of them -- see domain/failures.py."""
        from openoptima.domain.failures import INFEASIBLE_CODES

        provider = StepGeometryProvider(
            GeometryDefinition(provider="step", source=str(tmp_path / "nothing.step"))
        )
        with pytest.raises(EvaluationFailure) as raised:
            provider.build(EMPTY_SPACE.defaults(), tmp_path / "out")
        assert raised.value.code not in INFEASIBLE_CODES


class TestTheSourceFileIsNeverTouched:
    """AGENTS.md: 'Never write to the user's source CAD model.'

    A SolidWorks file that OpenOptima quietly modified would be the kind of
    surprise nobody would think to check for, since a re-import provider has
    no reason to write to its input at all -- but 'has no reason to' is not
    the same guarantee as 'cannot'.
    """

    def test_content_is_byte_identical_after_build(self, tmp_path):
        source = _known_box(tmp_path)
        before = source.step_path.read_bytes()

        StepGeometryProvider(
            GeometryDefinition(provider="step", source=str(source.step_path))
        ).build(EMPTY_SPACE.defaults(), tmp_path / "imported")

        assert source.step_path.read_bytes() == before

    def test_modification_time_is_unchanged_after_build(self, tmp_path):
        source = _known_box(tmp_path)
        before = source.step_path.stat().st_mtime_ns

        StepGeometryProvider(
            GeometryDefinition(provider="step", source=str(source.step_path))
        ).build(EMPTY_SPACE.defaults(), tmp_path / "imported")

        assert source.step_path.stat().st_mtime_ns == before

    def test_validation_alone_does_not_touch_it_either(self, tmp_path):
        source = _known_box(tmp_path)
        before = source.step_path.read_bytes()

        StepGeometryProvider(
            GeometryDefinition(provider="step", source=str(source.step_path))
        ).validate_definition()

        assert source.step_path.read_bytes() == before


class TestARelativeSourceResolvesAgainstTheProjectNotTheTerminal:
    """Matches the fix in cli/main.py and app/checks.py: a relative
    geometry.source is written relative to the project file, and doctor must
    resolve it the same way an evaluation would -- not against whatever
    directory a command happened to be run from."""

    def test_a_relative_source_resolves_against_root(self, tmp_path):
        source = _known_box(tmp_path)
        provider = StepGeometryProvider(
            GeometryDefinition(provider="step", source=source.step_path.name),
            root=source.step_path.parent,
        )
        assert provider.source_path == source.step_path
        report = provider.validate_definition()
        assert report.ok

    def test_root_can_be_set_after_construction(self, tmp_path):
        """This is how the evaluation pipeline actually sets it -- see
        pipeline.py's `if hasattr(provider, "root")`."""
        source = _known_box(tmp_path)
        provider = StepGeometryProvider(
            GeometryDefinition(provider="step", source=source.step_path.name)
        )
        provider.root = source.step_path.parent
        assert provider.validate_definition().ok


class TestCreateProviderDispatchesToStep:
    def test_it_returns_a_step_provider(self):
        provider = create_provider(GeometryDefinition(provider="step", source="bracket.step"))
        assert isinstance(provider, StepGeometryProvider)
        assert provider.name == "step"
