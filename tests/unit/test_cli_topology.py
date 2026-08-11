"""The topology command, and what it must never claim.

A topology run produces a shape nobody has analysed. The one thing this
command must get right is saying so: reporting a stress or a factor of safety
for a shape that has not been through the evaluation pipeline would be the most
dangerous output this software could produce.

Nothing here runs beso.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openoptima.cli.main import build_parser


def parse(*argv: str):
    return build_parser().parse_args(["topology", *argv])


PROJECT = str(Path("examples/l_bracket/project.yaml"))


class TestTheCommandExists:
    def test_it_is_registered(self):
        args = parse(PROJECT, "--deck", "part.inp")
        assert args.func.__name__ == "command_topology"

    def test_the_analysis_file_is_required(self):
        with pytest.raises(SystemExit):
            parse(PROJECT)

    def test_its_defaults_are_sensible(self):
        args = parse(PROJECT, "--deck", "part.inp")
        assert 0.0 < args.keep < 1.0
        assert args.feature_size > 0
        assert args.rounds > 0
        assert args.smoothing % 2 == 0

    def test_it_defaults_to_one_core(self):
        """More than one core makes the result unrepeatable, so it is opt-in.

        Measured: the identical problem on all cores produced two different
        shapes; on one core it is bit-identical.
        """
        assert parse(PROJECT, "--deck", "part.inp").cores == 1

    def test_every_setting_can_be_given(self):
        args = parse(
            PROJECT,
            "--deck",
            "part.inp",
            "--keep",
            "0.3",
            "--feature-size",
            "5",
            "--rounds",
            "80",
            "--removal-rate",
            "0.04",
            "--smoothing",
            "8",
            "--cores",
            "4",
            "--solver",
            "/usr/bin/ccx",
            "--analyse",
        )
        assert args.keep == 0.3
        assert args.feature_size == 5.0
        assert args.rounds == 80
        assert args.removal_rate == 0.04
        assert args.smoothing == 8
        assert args.cores == 4
        assert args.analyse is True


class TestAnalysingTheShapeIsAskedFor:
    """A topology run reports a shape. Only ``--analyse`` reports numbers.

    Analysing takes another mesh and another solve, so it is opt-in rather than
    automatic. What must never happen is the other way round: reporting a
    stress or a factor of safety that was never computed.
    """

    def test_it_is_off_unless_asked_for(self):
        assert parse(PROJECT, "--deck", "part.inp").analyse is False

    def test_the_help_says_what_it_adds(self):
        parser = build_parser()
        for action in parser._subparsers._group_actions[0].choices["topology"]._actions:
            if action.dest == "analyse":
                help_text = action.help or ""
                assert "factor of safety" in help_text
                return
        pytest.fail("no --analyse option found")


class TestItRefusesAMissingFile:
    def test_a_missing_analysis_file_is_reported_plainly(self, tmp_path, capsys):
        from openoptima.cli.main import command_topology

        args = parse(PROJECT, "--deck", str(tmp_path / "nothing.inp"))
        args.workspace = str(tmp_path)
        assert command_topology(args) == 2
        assert "no analysis file" in capsys.readouterr().err


class TestTheHelpSaysWhatItIsFor:
    def test_it_explains_the_difference_from_the_parametric_path(self):
        help_text = build_parser().format_help()
        assert "topology" in help_text

    def test_the_removal_rate_warns_about_deleting_a_load_path(self):
        parser = build_parser()
        for action in parser._subparsers._group_actions[0].choices["topology"]._actions:
            if action.dest == "removal_rate":
                assert "load path" in (action.help or "")
                return
        pytest.fail("no --removal-rate option found")


class TestAFactorOfSafetyBelowOneIsSaidInWords:
    """``Outcome: OK`` means the run finished, not that the design passes.

    A project that declares no constraint on the factor of safety gets ``OK``
    printed beside a factor of 0.64, which is exactly the caveat this project
    must not hide in vocabulary. Measured on the real topology result: 0.644.
    """

    @staticmethod
    def _steel(allowable=250.0):
        from openoptima.domain.model import Material

        return Material(
            name="steel",
            elastic_modulus=210000.0,
            poisson_ratio=0.3,
            density=7.85e-9,
            allowable_stress=allowable,
        )

    @staticmethod
    def _printed_plastic():
        """A material with a different strength in each direction.

        It carries no single allowable stress -- there is nothing sensible to
        put there -- so its factor of safety comes from a failure criterion.
        """
        from openoptima.domain.orthotropic import OrthotropicMaterial

        return OrthotropicMaterial(
            name="printed nylon",
            modulus=(2400.0, 2400.0, 1600.0),
            poisson=(0.38, 0.38, 0.38),
            shear_modulus=(600.0, 600.0, 870.0),
            density=1.14e-9,
            build_direction=(0.0, 0.0, 1.0),
        )

    def note(self, factor, material=None, capsys=None):
        from openoptima.cli.main import _print_factor_of_safety_note

        _print_factor_of_safety_note(
            {"factor_of_safety": factor}, material if material is not None else self._steel()
        )
        return capsys.readouterr().out

    def test_below_one_it_says_the_design_does_not_pass(self, capsys):
        out = self.note(0.644, capsys=capsys)
        assert "0.64" in out
        assert "does not pass" in out
        assert "250 MPa" in out

    def test_it_says_how_far_over_the_limit_the_part_is(self, capsys):
        """A percentage over 100 cannot be read as good news; "working harder" can.

        An earlier wording said the part was "working HARDER than the allowable
        stress you set", and a reader took that as capability -- asking whether
        the part would be better still at a higher number. It describes what the
        part can take rather than what is demanded of it, which inverts the
        warning. The percentage of the user's own limit does not invert.
        """
        out = self.note(0.644, capsys=capsys)
        assert "155%" in out
        assert "HARDER" not in out

    def test_just_above_one_it_says_the_margin_is_thin(self, capsys):
        out = self.note(1.05, capsys=capsys)
        assert "95%" in out
        assert "little margin" in out
        assert "does not pass" not in out

    def test_it_quotes_the_stress_the_factor_came_from_not_the_raw_peak(self, capsys):
        """The factor is the allowable divided by the *percentile* stress.

        Measured on the real topology result: the raw peak is 400.0 MPa and the
        percentile measure the factor uses is 387.9 MPa. Printing 400 beside a
        factor of 0.64 gives a reader two numbers that do not divide into each
        other, and invites them to check the arithmetic and conclude the
        software is wrong.
        """
        from openoptima.cli.main import _print_factor_of_safety_note

        _print_factor_of_safety_note(
            {
                "factor_of_safety": 0.6444,
                "stress_max_mpa": 387.9,
                "stress_raw_max_mpa": 400.0,
            },
            self._steel(),
        )
        out = capsys.readouterr().out
        assert "388 MPa" in out
        assert "400" not in out

    def test_a_factor_of_zero_does_not_divide_by_zero(self, capsys):
        """Nothing printed would be the worst possible output for the worst case."""
        out = self.note(0.0, capsys=capsys)
        assert "does not pass" in out
        assert "loaded past" in out

    def test_a_comfortable_factor_says_nothing(self, capsys):
        assert self.note(2.4, capsys=capsys) == ""

    def test_a_directional_material_is_not_told_it_has_an_allowable_stress(self, capsys):
        """With different strengths in each direction there is no single number."""
        out = self.note(0.8, material=self._printed_plastic(), capsys=capsys)
        assert "does not pass" in out
        assert "MPa" not in out
        assert "strengths you gave" in out

    def test_nothing_is_printed_when_no_factor_was_computed(self, capsys):
        from openoptima.cli.main import _print_factor_of_safety_note

        _print_factor_of_safety_note({"mass_kg": 1.0}, self._steel())
        assert capsys.readouterr().out == ""
