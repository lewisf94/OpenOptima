# 1. Record architecture decisions

**Status:** accepted

## Context

This project is built substantially by coding agents, which start each session
without memory of why anything is the way it is. An agent that cannot see the
reasoning behind a decision will cheerfully reverse it — usually by choosing the
more obvious option that was already considered and rejected.

## Decision

Every decision that would be expensive to reverse, or that looks wrong without
context, gets a short ADR here. Agents must read the relevant ADR before
changing the area it covers; `AGENTS.md` says so.

## Consequences

A little ceremony. In exchange, the "why is it done this odd way?" question has
an answer that survives the session it was decided in.
