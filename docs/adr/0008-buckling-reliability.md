# 8. An untrustworthy buckling factor is a failure, not a warning

**Status:** accepted. The decision stands; the diagnosis in *Context* below was
wrong and is corrected in *Update* at the end. Read that section before acting
on the slenderness numbers in this document.

## Context

Linear buckling was added because minimising mass drives designs towards thin
slender sections — exactly the geometry that buckles — and a static analysis
cannot see it.

The implementation verifies well in one regime and badly in another. Against
Euler's formula:

| Section | Slenderness | Result |
|---|---|---|
| 20 mm square, 200 / 400 / 800 mm long | 69–277 | within 1%, mode series correct at 1 : 9 |
| 22 mm square, 600 mm long | 195 | 9x too high |
| 8 mm square, 400 / 500 mm long | 346 / 444 | 9x too high |

In the failing cases the returned mode series was 1 : 1.95 : 3.20, nothing like
a column's, so the eigenvalue solve had missed the true lowest mode entirely.
Refining the mesh moved the answer around without converging, and requesting up
to 30 modes never surfaced a lower one. Static behaviour on the same meshes was
correct: lateral tip deflection matched beam theory to 0.14%.

Crucially the error is **optimistic**. It reports a strut as several times more
stable than it is.

## Decision

Every buckling result is cross-checked against beam theory computed
independently from the mesh's own geometry. When the result falls outside the
validated range, the evaluation **fails** with `RESULT_UNRELIABLE` — classified
as an ERROR, never retried — rather than reporting the number with a warning
attached.

## Rationale

A warning is not protection when the consumer is an optimiser. It reads the
number, sees the constraint satisfied, and selects the design. The warning goes
into a log nobody reads, and the winning design in the report is unsafe.

This is the same reasoning as the reaction-versus-applied-load equilibrium check
and the ambiguous-region halt: where a wrong answer is indistinguishable from a
right one, refusing to answer is the only safe behaviour.

`RESULT_UNRELIABLE` is an ERROR rather than INFEASIBLE because we genuinely do
not know whether the design is good — exactly the distinction ADR 2 exists to
preserve. Calling it infeasible would teach the optimiser that slender designs
are bad, which is a claim we have not established.

## Consequences

Genuinely slender members return no buckling result at all. That is honest: the
tool cannot analyse them reliably, and the user is told to use beam elements or
a hand calculation. `buckling.slenderness_limit` can be raised, and the
documentation is explicit that doing so silences the check rather than improving
the analysis.

The limit of 150 rests on a handful of measured points. Establishing the real
boundary is item V9 in the verification plan.

## Update: the trigger was not slenderness

V9 was carried out and the diagnosis above turned out to be wrong.

Slenderness was a coincidence, not a cause. The same strut was measured failing
at slenderness 69 and passing at 277, and vice versa. What actually predicts the
failure is **the buckling factor itself**: below roughly **0.52** against the
applied load, CalculiX silently skips the lowest mode and returns the second —
about nine times too high, in the unsafe direction, with nothing in its output
to say so. That explains the 1 : 1.95 : 3.20 mode series recorded above: it is a
correct series with its first term missing.

It also explains why nothing helped. Refining the mesh does not fix it, and
asking for more modes does not surface the missing one, because the mode was
never computed.

**It is now fixed at the root** rather than guarded against. The `*BUCKLE` step
is solved against a reference load 1000 times smaller and the result divided
back. The eigenvalue is exactly inversely proportional to the reference load, so
this is an identity, not an approximation — and it moves the factor well clear
of the 0.52 threshold where the solver misbehaves. Do not remove that scaling.

The slenderness guard in `results/buckling_check.py` was **not** removed. It no
longer carries the weight it was given here, but a cheap independent check
against beam theory is worth keeping, and it catches a different family of
problems. Treat it as a secondary safeguard, not as the reason the numbers can
be trusted.

The evidence is in `results/buckling_check.py` and V9 of
`docs/verification-plan.md`. The trap is recorded as item 7 in `AGENTS.md`.
