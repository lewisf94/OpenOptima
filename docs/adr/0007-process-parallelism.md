# 7. Parallelism is by process, never by thread

**Status:** accepted

## Context

Evaluations are independent and expensive, so they should run concurrently.

## Decision

Concurrency is `ProcessPoolExecutor`. gmsh access is additionally serialised
behind a lock within any single process.

## Rationale

gmsh keeps global state in a C library: two threads meshing at once corrupt each
other's models. CalculiX is a subprocess that wants its own working directory
and its own `OMP_NUM_THREADS`. Neither is thread-safe in the way a pure-Python
worker would be.

Process isolation also means a segfaulting solver kills one worker rather than
the study, which is why `WORKER_CRASH` exists as a retryable error code.

The parent pre-allocates run-directory ids (`RunSpaceFactory.reserve`) so
concurrent workers cannot collide on directory names.

## Consequences

Payloads must be picklable, which is why `_evaluate_in_worker` is module level
and takes plain data. Default worker count leaves one core free so an
interactive machine stays usable; oversubscribing makes everything slower
because each solver process also wants threads.
