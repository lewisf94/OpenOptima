"""FRD/DAT parsing.

The column-slicing behaviour is the point of these tests.  CalculiX writes
fixed-width fields and will happily emit ``-1.23456E+05-9.87654E+04`` with no
separator when a value fills its field; a parser that splits on whitespace
merges those two numbers into one and produces a plausible, wrong answer.
"""

from __future__ import annotations

import numpy as np
import pytest

from openoptima.solvers.base import von_mises_from_tensor
from openoptima.solvers.calculix.dat import parse_dat
from openoptima.solvers.calculix.frd import blocks_named, parse_frd

FRD_SAMPLE = """    1C
    1UUSER
 -4  DISP        4    1
 -5  D1          1    2    1    0
 -5  D2          1    2    2    0
 -5  D3          1    2    3    0
 -1         1 1.00000E+00 2.00000E+00 3.00000E+00
 -1         2-1.00000E+00-2.00000E+00-3.00000E+00
 -3
 -4  STRESS      6    1
 -5  SXX         1    4    1    1
 -5  SYY         1    4    2    2
 -5  SZZ         1    4    3    3
 -5  SXY         1    4    1    2
 -5  SYZ         1    4    2    3
 -5  SZX         1    4    3    1
 -1         1 1.00000E+02 0.00000E+00 0.00000E+00 0.00000E+00 0.00000E+00 0.00000E+00
 -1         2 0.00000E+00 0.00000E+00 0.00000E+00 5.00000E+01 0.00000E+00 0.00000E+00
 -3
"""

DAT_SAMPLE = """
                        S T E P       1


 total force (fx,fy,fz) for set FIXED and time  0.1000000E+01

       -7.163067E-09 -1.116377E-08  1.000000E+03
"""


def test_parses_displacement_block(tmp_path):
    path = tmp_path / "job.frd"
    path.write_text(FRD_SAMPLE)
    blocks = parse_frd(path)
    disp = blocks_named(blocks, "DISP")
    assert len(disp) == 1
    assert disp[0].components == ["D1", "D2", "D3"]
    assert disp[0].values[1] == [1.0, 2.0, 3.0]


def test_parses_adjacent_negative_values_without_a_separator(tmp_path):
    """The failure mode a whitespace split would produce."""
    path = tmp_path / "job.frd"
    path.write_text(FRD_SAMPLE)
    disp = blocks_named(parse_frd(path), "DISP")[0]
    assert disp.values[2] == [-1.0, -2.0, -3.0], (
        "values packed edge to edge must be split by column, not by whitespace"
    )


def test_parses_six_stress_components(tmp_path):
    path = tmp_path / "job.frd"
    path.write_text(FRD_SAMPLE)
    stress = blocks_named(parse_frd(path), "STRESS")[0]
    assert len(stress.components) == 6
    assert stress.values[1][0] == pytest.approx(100.0)


def test_as_array_orders_rows_by_requested_node(tmp_path):
    path = tmp_path / "job.frd"
    path.write_text(FRD_SAMPLE)
    disp = blocks_named(parse_frd(path), "DISP")[0]
    array = disp.as_array([2, 1])
    assert array[0].tolist() == [-1.0, -2.0, -3.0]
    assert array[1].tolist() == [1.0, 2.0, 3.0]


def test_three_digit_exponent_does_not_corrupt_adjacent_values(tmp_path):
    """CalculiX built with a Windows Fortran runtime writes ``E-003``, not
    ``E-03``. That makes a negative value one character wider than the field
    a fixed-width slice assumed, which shifted every later value on the line
    and turned a 0.006 mm node displacement into a bogus 37.9 mm one -- a
    result that looked alarming rather than obviously wrong. See the module
    docstring in ``frd.py``.
    """
    path = tmp_path / "job.frd"
    path.write_text(
        "    1C\n"
        "    1UUSER\n"
        " -4  DISP        4    1\n"
        " -5  D1          1    2    1    0\n"
        " -5  D2          1    2    2    0\n"
        " -5  D3          1    2    3    0\n"
        " -1      3676-6.49358E-0037.88340E-0072.36900E-003\n"
        " -3\n"
    )
    disp = blocks_named(parse_frd(path), "DISP")[0]
    assert disp.values[3676] == pytest.approx([-6.49358e-3, 7.88340e-7, 2.36900e-3])


def test_multiple_steps_produce_multiple_blocks(tmp_path):
    path = tmp_path / "job.frd"
    path.write_text(FRD_SAMPLE + FRD_SAMPLE)
    assert len(blocks_named(parse_frd(path), "DISP")) == 2


def test_missing_file_is_an_error(tmp_path):
    with pytest.raises(OSError):
        parse_frd(tmp_path / "absent.frd")


class TestVonMises:
    def test_uniaxial_stress_equals_the_applied_stress(self):
        tensor = np.array([[100.0, 0, 0, 0, 0, 0]])
        assert von_mises_from_tensor(tensor)[0] == pytest.approx(100.0)

    def test_pure_shear(self):
        tensor = np.array([[0, 0, 0, 50.0, 0, 0]])
        assert von_mises_from_tensor(tensor)[0] == pytest.approx(50.0 * np.sqrt(3.0))

    def test_hydrostatic_stress_produces_no_von_mises(self):
        tensor = np.array([[80.0, 80.0, 80.0, 0, 0, 0]])
        assert von_mises_from_tensor(tensor)[0] == pytest.approx(0.0, abs=1e-9)

    def test_equal_biaxial_tension(self):
        tensor = np.array([[100.0, 100.0, 0.0, 0, 0, 0]])
        assert von_mises_from_tensor(tensor)[0] == pytest.approx(100.0)


class TestDat:
    def test_parses_reaction_total(self, tmp_path):
        path = tmp_path / "job.dat"
        path.write_text(DAT_SAMPLE)
        totals = parse_dat(path)
        assert len(totals) == 1
        assert totals[0].set_name == "FIXED"
        assert totals[0].force[2] == pytest.approx(1000.0)

    def test_absent_file_returns_empty_rather_than_raising(self, tmp_path):
        assert parse_dat(tmp_path / "nothing.dat") == []
