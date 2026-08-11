"""Validation on ``geometry:`` in the project file.

Each provider needs different information to build anything at all, and a
missing field here should be a located, readable error -- not a ``KeyError``
three layers into the pipeline once a study is already running.

None of this needed gmsh before; the checks for ``occ`` and ``cadquery``
were exercised only by hand. Added here alongside the new ``step`` provider
because all three belong to the same validator.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from openoptima.schema.project_schema import GeometrySchema

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "l_bracket" / "project.yaml"


class TestOcc:
    def test_a_template_is_required(self):
        with pytest.raises(ValidationError, match=re.escape("geometry.template is required")):
            GeometrySchema(provider="occ")

    def test_a_template_is_enough(self):
        schema = GeometrySchema(provider="occ", template="cantilever_box")
        assert schema.template == "cantilever_box"


class TestCadquery:
    def test_a_source_is_required(self):
        with pytest.raises(ValidationError, match=re.escape("geometry.source is required")):
            GeometrySchema(provider="cadquery")

    def test_a_source_is_enough(self):
        schema = GeometrySchema(provider="cadquery", source="my_part.py")
        assert schema.source == "my_part.py"


class TestStep:
    """The provider for a file exported from SolidWorks, Fusion 360, or similar."""

    def test_a_source_is_required(self):
        with pytest.raises(ValidationError, match=re.escape("geometry.source is required")):
            GeometrySchema(provider="step")

    def test_the_message_gives_a_concrete_example(self):
        """A blank part number means nothing; naming SolidWorks does."""
        with pytest.raises(ValidationError, match="SolidWorks"):
            GeometrySchema(provider="step")

    def test_a_source_is_enough_and_no_template_is_needed(self):
        """Unlike 'occ', 'step' has no built-in shapes to name."""
        schema = GeometrySchema(provider="step", source="bracket.step")
        assert schema.provider == "step"
        assert schema.source == "bracket.step"
        assert schema.template == ""

    def test_it_reaches_the_domain_object_through_a_whole_project(self):
        """The schema alone has no to_domain(); conversion happens in
        ProjectSchema, alongside the rest of a valid project.

        Starts from the real example project rather than a hand-built minimal
        one, so this proves the 'step' provider survives loading a complete
        file -- design variables, load cases, material and all -- not just
        the geometry block in isolation.
        """
        from openoptima.schema.loader import load_project_dict

        raw = yaml.safe_load(EXAMPLE.read_text())
        raw["geometry"] = {"provider": "step", "source": "bracket.step"}
        project = load_project_dict(raw)
        assert project.geometry.provider == "step"
        assert project.geometry.source == "bracket.step"


class TestUnknownKeysAreRejected:
    """A typo in the geometry block must fail loudly, not silently do nothing."""

    def test_a_typo_is_reported(self):
        with pytest.raises(ValidationError):
            GeometrySchema(provider="step", source="bracket.step", souce="typo.step")
