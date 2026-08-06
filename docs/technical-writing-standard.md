# Technical writing standard

## Requirement

All repository-owned English technical text must follow ASD-STE100 Simplified
Technical English (STE), Issue 9.

This requirement applies to existing text and future text.

This requirement applies to these types of text:

- README files, guides, plans, procedures, and test instructions.
- Agent instructions, issue text, pull request text, and commit messages.
- Code comments, user help, warnings, errors, and diagnostic explanations.

The official standard is the source of truth.

- Use the Issue 9 standard.
- Use the official FAQ.

This file is a project checklist. It does not replace the official standard
or its controlled dictionary.

### How this relates to `CLAUDE.md`

`CLAUDE.md` states *why* this project writes in plain language: the reader is
directing the project but is not a CAE specialist. This file states *how* —
the mechanical rules: sentence length, contractions, voice, structure.

The two rules stack. STE alone would let a project list familiar engineering
nouns as "project terms" and use them unexplained. This project does not do
that for CAE and optimisation terms — see "Project terms" below.

## Exclusions

These items are outside the rewrite scope:

- Source code, identifiers, commands, file paths, region selectors, and API
  names.
- Exact log output and data that comes from Gmsh, CalculiX, or a solver run —
  FRD and DAT file contents, `ccx` console output, Gmsh log messages.
- Direct quotations, licence text, generated text, and third-party text —
  including the Timoshenko and Euler references quoted in
  `docs/verification-plan.md`, and `THIRD_PARTY_LICENSES.md`.
- Vendored components and upstream reference projects. The repository vendors
  none today. If CalculiX or Gmsh source is ever bundled, per
  `packaging/README.md`, that source keeps its own original text.
- Private configuration files and protected configuration folders.
- Numeric tolerances, verification reference values, and formula derivations
  in `tests/verification/` and `docs/verification-plan.md`. These encode
  physics, not prose. Rewrite the sentences around a number freely. Never
  change the number to shorten a sentence. `AGENTS.md` already forbids
  loosening a tolerance to make a build pass; that rule extends here.

Preserve each excluded item exactly, when accuracy or compatibility needs its
original form.

Code comments and repository-owned user messages remain in scope.

## Writing rules

- Use an approved STE word for its approved meaning and part of speech.
- Use a project term (below) as a technical noun or technical verb when
  necessary.
- Use one term for one item or action. Do not use a synonym for variety.
  Reuse the exact wording from the glossary in
  `docs/plain-english-guide.md` for every CAE or optimisation concept.
- Use a maximum of 20 words in each procedural sentence.
- Use a maximum of 25 words in each descriptive sentence.
- Give only one instruction in each sentence, unless two actions occur at the
  same time.
- Use the imperative form for an instruction.
- Put a necessary condition before the instruction.
- Give only one topic in each descriptive sentence.
- Give only one topic in each paragraph. Use a maximum of six sentences in a
  paragraph.
- Use the active voice. Use the passive voice only when the agent is unknown
  in descriptive text.
- Do not use contractions or omit necessary words.
- Do not use slang, idioms, metaphors, or vague words.
- Do not use an -ing form unless STE permits it or it is part of a technical
  noun.
- Define an abbreviation at its first use. Use the same abbreviation after
  that definition.
- Use numbered steps for a sequence. Give one main action in each step.
- Put a warning or caution before the instruction that can cause the hazard.
- State the possible result of a hazard and the action that prevents it.

The last two rules are not abstract here. `RESULT_UNRELIABLE`, an equilibrium
check failure, and a rejected buckling result are hazard statements: state
what the reader must not trust, and why, before any instruction about what to
do next.

## Project terms

These are proper nouns and file formats with no plain-English substitute. Use
the spelling and capitalisation below.

**Tools and libraries**: Gmsh, CalculiX, pymoo, NSGA-II, CadQuery,
OpenCASCADE, OpenFOAM, SciPy, NumPy, pydantic, PyYAML, SQLite, PyInstaller,
Inno Setup.

**File formats**: STEP (`.step`), BREP (`.brep`), FRD (`.frd`), DAT (`.dat`),
INP (`.inp`), MSH (`.msh`), YAML (`.yaml`), JSON (`.json`).

**Named reference results** (keep exact — see Exclusions): Timoshenko beam
theory, Euler's column formula.

**CalculiX deck keywords**, written as CalculiX itself writes them, for
example `*BUCKLE`, `*DLOAD`, `*STEP`.

Add a term here when it is a proper noun, a file format, or fixed solver
syntax. Do not add a term here only because it is a familiar engineering
word.

### What is deliberately not on this list

Mesh, element, node, degrees of freedom, stress, Pareto front, knee point,
factor of safety, singularity, and every other term in the
`docs/plain-english-guide.md` glossary. These are correct engineering terms,
and STE would normally let a project list them here and use them unexplained.
This project does not, because the reader is explicitly not a CAE specialist
— see `CLAUDE.md`.

Explain each one at its first use, in the same sentence, the way
`docs/plain-english-guide.md` does. If a term you need is not yet in that
glossary, add it there in the same change. Then use its exact wording
everywhere else.

## Review

Before a commit, review all changed technical text against this file and the
official standard.

Do these checks:

- Identify each sentence as procedural or descriptive.
- Count the words in each sentence.
- Check that each sentence has one instruction or one topic.
- Check each general word against the STE dictionary.
- Check that each project term has one consistent meaning, matching
  `docs/plain-english-guide.md`.
- Check that each condition, warning, and caution is in the correct
  position.
- Read the text for ambiguity.

Run this review alongside the checks in `AGENTS.md` under "Required checks".
It is a manual read. No tool in the current build chain checks STE compliance
automatically. A language tool can help; it does not replace the review.

The writer and reviewer remain responsible for compliance.

Do not claim formal STE compliance without a qualified review of the full
standard.

### Bringing existing text into line

This standard covers existing text as well as new text, but a single
drive-by rewrite of the whole repository is not the goal. When you touch a
file for another reason, bring the parts you touch into compliance. Do not
rewrite a file you are not otherwise changing.
