# 5. Produce the Pareto front; use preferences to rank it

**Status:** accepted

## Context

Users want one answer. The obvious way to give them one is a weighted score:
`0.6 x strength + 0.4 x weight`.

## Decision

The optimiser always produces the trade-off surface. Preferences rank it; they
never replace it. Four levels, in increasing subtlety:

1. **Hard limits** — never acceptable.
2. **Targets** — what I am aiming for.
3. **Desirability with saturation** — past this point, more buys nothing.
4. **Trade rules** — I will pay X of this for Y of that.

Alongside these, `marginal_rates()` reports what each step along the front
actually costs, and `knee_point()` finds where the return collapses.

## Rationale

A weighted sum has three defects that matter here:

- its meaning changes when units change (grams vs kilograms alters the answer);
- it cannot reach concave regions of a front, so entire families of good designs
  are unreachable regardless of the weights;
- it hides the trade-off it is making, so nobody can argue with it.

The specific question this must answer is: *"it may be worth a little more mass
for a lot more strength, but the code does not know that."* A weighted sum
cannot express it. A trade rule can, in the engineer's own terms, and the
marginal-rate table shows the evidence: the first 16 g buys deflection at
0.49 per unit, the next 148 g at 7.82 per unit.

Desirability uses a weighted **geometric** mean, so one unacceptable metric
drives the score to zero rather than being compensated by a good one.

## Consequences

Reports are longer, and the user has a decision to make rather than a number to
accept. That is the correct division of labour: the software computes and
presents, the engineer judges.
