"""Reading natural frequencies out of CalculiX, and refusing what is not one.

Every sample here is real output, copied from a solved cantilever rather than
invented, because the whole point of this parser is to survive a format nobody
documents.

Three separate hazards are guarded, and each one produced a plausible wrong
answer rather than a crash:

1. A ``*FREQUENCY`` step prints three more tables after the eigenvalues --
   participation factors, effective modal mass, total effective mass -- and
   every one of them starts its rows with a mode number.
2. It also emits a reaction total and an internal energy per mode, so a reader
   pairing those with load cases by position would attribute an eigenvalue
   artefact to a real load case. That is trap 6 with more records.
3. It writes a full mode shape into the FRD without being asked, which shifts
   every later result block along.
"""

from __future__ import annotations

import pytest

from openoptima.solvers.calculix.dat import (
    FrequencyTable,
    frequencies_in_step,
    parse_dat,
    parse_frequencies,
)
from openoptima.solvers.calculix.frd import parse_frd

# Real output. A cantilever with a static step first and a frequency step
# second, trimmed to the records that matter.
DAT_SAMPLE = """
                        S T E P       1


                                INCREMENT     1


 total internal energy for set EALL and time  0.1000000E+01

        7.571858E+01

 total force (fx,fy,fz) for set FIXED and time  0.1000000E+01

       -2.401180E-09  8.542651E-10  1.000000E+02

                        S T E P       2


     E I G E N V A L U E   O U T P U T

 MODE NO    EIGENVALUE                       FREQUENCY
                                     REAL PART            IMAGINARY PART
                           (RAD/TIME)      (CYCLES/TIME     (RAD/TIME)

      1   0.6937157E+07   0.2633848E+04   0.4191900E+03   0.0000000E+00
      2   0.2732500E+08   0.5227332E+04   0.8319557E+03   0.0000000E+00
      3   0.2666615E+09   0.1632977E+05   0.2598964E+04   0.0000000E+00

     P A R T I C I P A T I O N   F A C T O R S

MODE NO.   X-COMPONENT     Y-COMPONENT     Z-COMPONENT

      1   0.9578068E-09   0.1451249E-07  -0.4899526E-02
      2  -0.1162280E-07  -0.4902012E-02   0.1077454E-07
      3  -0.1013165E-07  -0.6162381E-07   0.2724705E-02

     E F F E C T I V E   M O D A L   M A S S

MODE NO.   X-COMPONENT     Y-COMPONENT     Z-COMPONENT

      1   0.9173938E-18   0.2106125E-15   0.2400535E-04
TOTAL     0.8666719E-14   0.3158660E-04   0.3400445E-04


                    E I G E N V A L U E    N U M B E R     1


 total internal energy for set EALL and time  0.2000000E+01

        3.468578E+06

 total force (fx,fy,fz) for set FIXED and time  0.2000000E+01

       -6.644555E-03 -1.006757E-01  3.398878E+04
"""


@pytest.fixture
def dat_file(tmp_path):
    path = tmp_path / "beam.dat"
    path.write_text(DAT_SAMPLE)
    return path


class TestReadingTheEigenvalueTable:
    def test_it_finds_the_frequencies(self, dat_file):
        tables = parse_frequencies(dat_file)
        assert len(tables) == 1
        assert tables[0].hertz == pytest.approx((419.19, 831.9557, 2598.964), rel=1e-6)

    def test_it_reads_the_hertz_column_not_the_eigenvalue(self, dat_file):
        """Four numbers follow the mode number and only one of them is hertz.

        Taking the first would report 6 937 157 instead of 419.19 -- a number
        so large it would look like a units problem rather than a column one.
        """
        assert parse_frequencies(dat_file)[0].hertz[0] == pytest.approx(419.19)

    def test_the_printed_hertz_agrees_with_the_eigenvalue(self, dat_file):
        """sqrt(eigenvalue) / 2 pi, as a cross-check on which column is which."""
        import math

        assert parse_frequencies(dat_file)[0].hertz[0] == pytest.approx(
            math.sqrt(0.6937157e7) / (2 * math.pi), rel=1e-5
        )

    def test_it_is_tagged_with_its_step(self, dat_file):
        assert parse_frequencies(dat_file)[0].step == 2

    def test_the_tables_that_follow_are_not_eigenvalues(self, dat_file):
        """Participation factors and modal mass also start rows with a mode number.

        Reading on past the eigenvalue table would append 0.9578068E-09 and
        friends as further "frequencies", and the rigid-body check would then
        see a mode at essentially zero and refuse a perfectly good model.
        """
        assert len(parse_frequencies(dat_file)[0].hertz) == 3

    def test_the_per_mode_output_totals_are_not_another_table(self, dat_file):
        """ "EIGENVALUE NUMBER 1" is a heading over leftover output, not a table."""
        assert len(parse_frequencies(dat_file)) == 1

    def test_a_missing_file_is_empty_not_an_error(self, tmp_path):
        assert parse_frequencies(tmp_path / "nothing.dat") == []


class TestTheFrequencyStepDoesNotPolluteTheStaticResults:
    def test_its_reaction_totals_are_tagged_with_its_own_step(self, dat_file):
        """Trap 6, with one spurious record per mode instead of one per step.

        The 3.4e4 N "reaction" in step 2 is an artefact of the eigenvalue
        solve. Summing it with the real 100 N from step 1 would report a
        catastrophic equilibrium error on a sound model.
        """
        totals = parse_dat(dat_file)
        steps = {total.step for total in totals}
        assert steps == {1, 2}
        static = [total for total in totals if total.step == 1]
        assert len(static) == 1
        assert static[0].force[2] == pytest.approx(100.0)


class TestPickingOutRigidBodyModes:
    """A part free to drift or spin has no natural frequency to report."""

    def test_a_properly_held_part_has_none(self):
        # Measured on the verification cantilever.
        table = FrequencyTable((418.9, 831.5, 2595.0, 4987.5, 6011.9, 7140.1))
        assert table.rigid_body_modes == ()
        assert table.fundamental == pytest.approx(418.9)

    def test_a_part_free_to_slide_is_caught(self):
        """Measured: held against the load only, three modes came back at zero."""
        table = FrequencyTable((0.0, 0.0, 0.0011992, 1821.4, 3574.1, 5830.2))
        assert len(table.rigid_body_modes) == 3
        assert table.fundamental == pytest.approx(1821.4)

    def test_a_completely_free_part_has_no_answer_at_all(self):
        """Measured on an unheld beam: six modes, none of them real."""
        table = FrequencyTable((0.0, 0.0, 0.0, 0.0, 0.0, 0.001863))
        assert len(table.rigid_body_modes) == 5
        assert table.fundamental == pytest.approx(0.001863)

    def test_every_mode_at_exactly_zero_leaves_nothing(self):
        table = FrequencyTable((0.0, 0.0, 0.0))
        assert len(table.rigid_body_modes) == 3
        assert table.fundamental is None

    def test_a_genuinely_floppy_part_is_not_mistaken_for_a_loose_one(self):
        """The reason the threshold is relative rather than a fixed few hertz.

        Measured on a 300 x 10 x 2 mm beam: a real first mode of 18.6 Hz, and
        every bit as accurate as the 419 Hz one. A fixed threshold high enough
        to catch the 0.0012 Hz case above would eventually swallow this.
        """
        table = FrequencyTable((18.603, 92.818, 116.563, 326.3, 578.7, 639.3))
        assert table.rigid_body_modes == ()
        assert table.fundamental == pytest.approx(18.603)

    def test_an_empty_table_reports_nothing_rather_than_crashing(self):
        assert FrequencyTable(()).rigid_body_modes == ()
        assert FrequencyTable(()).fundamental is None


class TestSelectingTheRightStep:
    def test_it_finds_the_table_for_a_step(self):
        tables = [FrequencyTable((10.0,), step=3), FrequencyTable((20.0,), step=7)]
        found = frequencies_in_step(tables, 7)
        assert found is not None and found.hertz == (20.0,)

    def test_a_step_with_no_table_is_none_not_the_wrong_one(self):
        tables = [FrequencyTable((10.0,), step=3)]
        assert frequencies_in_step(tables, 9) is None


# Real FRD structure: three static blocks, then a mode shape, then another
# static block. Only the mode shape carries MODAL on its 100C record.
FRD_SAMPLE = """    1PSTEP                         1           1           1
  100CL  101 1.000000000        2003                     0    1           1
 -4  DISP        4    1
 -5  D1          1    2    1    0
 -1         1 1.00000E+00 2.00000E+00 3.00000E+00
 -3
    1PSTEP                         4           1           2
    1PMODE                         1
  100CL  102 419.1899797        2003                     2    2MODAL      1
 -4  DISP        4    1
 -5  D1          1    2    1    0
 -1         1 9.99999E+02 9.99999E+02 9.99999E+02
 -3
    1PSTEP                         7           1           1
  100CL  103 2.000000000        2003                     0    1           1
 -4  DISP        4    1
 -5  D1          1    2    1    0
 -1         1 4.00000E+00 5.00000E+00 6.00000E+00
 -3
"""


class TestModeShapesAreNeverReadAsResults:
    """The hazard that made this whole feature dangerous.

    A ``*FREQUENCY`` step writes DISP, STRESS and ERROR for every mode into
    the FRD, because CalculiX carries an output request forward from the step
    that made it. Six modes means eighteen extra blocks. The reader pairs the
    n-th DISP block with the n-th solved step, so counting them would hand the
    second load case a mode shape -- a displacement field that looks entirely
    like a real deflection, scaled arbitrarily.
    """

    @pytest.fixture
    def frd_file(self, tmp_path):
        path = tmp_path / "beam.frd"
        path.write_text(FRD_SAMPLE)
        return path

    def test_the_mode_shape_block_is_skipped(self, frd_file):
        assert len(parse_frd(frd_file)) == 2

    def test_the_second_static_result_is_still_the_second_block(self, frd_file):
        """Without the skip this would return the mode shape's 999.999."""
        blocks = parse_frd(frd_file)
        assert blocks[1].values[1] == pytest.approx([4.0, 5.0, 6.0])

    def test_the_first_static_result_is_untouched(self, frd_file):
        assert parse_frd(frd_file)[0].values[1] == pytest.approx([1.0, 2.0, 3.0])

    def test_a_file_with_no_modal_blocks_is_unchanged(self, tmp_path):
        """Every existing verified result rests on this path; it must not move."""
        path = tmp_path / "static.frd"
        path.write_text(
            "  100CL  101 1.000000000        2003                     0    1           1\n"
            " -4  DISP        4    1\n"
            " -5  D1          1    2    1    0\n"
            " -1         1 1.00000E+00 2.00000E+00 3.00000E+00\n"
            " -3\n"
        )
        blocks = parse_frd(path)
        assert len(blocks) == 1
        assert blocks[0].values[1] == pytest.approx([1.0, 2.0, 3.0])
