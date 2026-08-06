---
name: Wrong or suspicious result
about: The software produced a number you do not believe
title: ''
labels: 'correctness'
---

**This is the most important kind of issue this project can receive.** Software
that crashes is easy; software that returns a plausible wrong number is the
failure mode that matters.

## What you got, and what you expected

Include the reference you are comparing against (hand calculation, another
solver, a test result) and why you trust it.

## Reproduce

- Project file (attach or paste)
- Command run
- Version: `pip show openoptima`
- `openoptima doctor` output

## From the run

Attach `evaluation_manifest.json` from the run directory — it records tool
versions, the setup digest and the resolved regions.

- Was there an equilibrium warning?
- Which stress measure was configured, and which regions were excluded?
- Element type and mesh quality from the result?
- Has the result been checked for mesh convergence?
