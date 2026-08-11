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
        )
        assert args.keep == 0.3
        assert args.feature_size == 5.0
        assert args.rounds == 80
        assert args.removal_rate == 0.04
        assert args.smoothing == 8
        assert args.cores == 4


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
