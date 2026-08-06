# 2. Infeasible designs and infrastructure errors are different

**Status:** accepted

## Context

An evaluation can fail two ways that look identical from outside: the design was
impossible (a wall went to zero, a stress limit was exceeded), or we could not
determine the answer (the solver crashed, the disk filled, a worker was killed).

The optimiser needs a number for every design. The tempting simplification is to
give any failure a very poor score.

## Decision

The two are distinct outcomes throughout, from `FailureCode` up to the pymoo
adapter.

- `INFEASIBLE` — the design violates a genuine geometric or engineering
  constraint. Fed to the optimiser as a constraint violation so the search is
  pushed away properly. Never retried; it is deterministic.
- `ERROR` — we could not find out. Retried if transient. Masked out of the
  population with a penalty, and counted separately in the study report. Never
  cached, because a transient failure must not become permanent.

## Consequences

If a solver crash were scored as "a terrible design", the search would learn to
avoid a perfectly good region of the design space because of an infrastructure
problem — and would do so invisibly, since the result would look like ordinary
optimisation progress.

The cost is that every failure site must classify itself. `outcome_for()` makes
that a single lookup, and the reports surface the error rate so a study run on a
flaky machine is obvious rather than quietly biased.
