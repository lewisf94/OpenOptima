@AGENTS.md

---

# Communicating about this project

**The project owner is not a CAE specialist.** They are directing this project
and making the engineering decisions, but they do not necessarily know the
jargon of finite element analysis, meshing or multi-objective optimisation, and
should not have to.

Write for that reader. This applies to chat replies, commit messages, pull
request descriptions, report text, CLI output and error messages — anything a
human will read.

## How to write

- **Explain a term the first time you use it**, in the same sentence. Not
  "the Pareto front" but "the Pareto front — the set of designs where you
  cannot improve one thing without giving up another".
- **Prefer the short word.** "Chops the part into small pieces" beats
  "discretises the domain". "How much it bends" beats "displacement field".
- **Lead with what it means, then the name.** "There is no single best
  design, only a menu of trade-offs. That menu is called the Pareto front."
- **Use a concrete number from an actual run** instead of describing a capability
  in the abstract.
- **Say what a thing is *for*, not just what it does.** "This catches setup
  mistakes in ten seconds instead of two hours" is more useful than "this
  validates region selectors across the design space".
- **Do not hide a caveat in vocabulary.** If a result should not be trusted,
  say so in words the reader cannot misread.

Avoid unexplained: *topological naming, semantic region, failure taxonomy,
consistent nodal load, scaled Jacobian, discretisation, provenance, spawn
context, divergence theorem, p-norm, aspiration point, infill point.*
Every one of these has a plain-English equivalent in
`docs/plain-english-guide.md` — use it, or link there.

These bullets are the *why*. [`docs/technical-writing-standard.md`](docs/technical-writing-standard.md)
is the *how*: a mechanical standard (Simplified Technical English) covering
sentence length, contractions, voice and structure, for every piece of
repository-owned text. It does not relax this rule — a CAE or optimisation
term still needs a plain-English explanation at first use, from the glossary
below, before anyone uses it.

## Where the explanations live

`docs/plain-english-guide.md` is the jargon-free explanation of the whole
project: what it does, how to run it, what every term means, how to read the
results, and when not to trust them. **Keep it current.** If you add a feature or
a concept that a non-specialist would meet, explain it there in the same change —
not only in the technical docs.

Its glossary is the canonical list of plain-English wordings. Reuse those exact
phrasings elsewhere so the project speaks with one voice.

## What still needs precision

Being readable does not mean being vague. Never soften these:

- a result that should not be trusted, and exactly why
- the difference between an infeasible design and an infrastructure error
- what the analysis does not cover (buckling, fatigue, contact, plasticity)
- that allowable stress is the engineer's decision, not a material property
- any warning the software raises

When something is uncertain, say plainly that it is uncertain. Confident prose
about an unreliable number is worse than jargon.
