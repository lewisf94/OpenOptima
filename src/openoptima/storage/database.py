"""SQLite storage for designs, results and studies.

Metadata lives in the database (searchable, joinable); bulk artefacts stay on
disk under ``runs/``.  The database is the index, not the archive.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ..domain.failures import EvaluationState, FailureCode, Outcome
from ..domain.results import EvaluationResult
from ..domain.variables import DesignSpace, DesignVector

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evaluations (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    evaluation_hash      TEXT NOT NULL,
    design_digest        TEXT NOT NULL,
    setup_digest         TEXT NOT NULL,
    study                TEXT NOT NULL DEFAULT '',
    run_id               TEXT NOT NULL DEFAULT '',
    run_directory        TEXT NOT NULL DEFAULT '',
    outcome              TEXT NOT NULL,
    state                TEXT NOT NULL,
    failure_code         TEXT,
    message              TEXT NOT NULL DEFAULT '',
    design_json          TEXT NOT NULL,
    metrics_json         TEXT NOT NULL,
    violations_json      TEXT NOT NULL DEFAULT '{}',
    load_cases_json      TEXT NOT NULL DEFAULT '[]',
    mesh_json            TEXT,
    warnings_json        TEXT NOT NULL DEFAULT '[]',
    provenance_json      TEXT NOT NULL DEFAULT '{}',
    wall_time            REAL NOT NULL DEFAULT 0,
    created_at           REAL NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_evaluations_hash
    ON evaluations (evaluation_hash);
CREATE INDEX IF NOT EXISTS idx_evaluations_study ON evaluations (study);
CREATE INDEX IF NOT EXISTS idx_evaluations_outcome ON evaluations (outcome);

CREATE TABLE IF NOT EXISTS studies (
    name        TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    settings    TEXT NOT NULL DEFAULT '{}',
    state       TEXT NOT NULL DEFAULT 'running',
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
"""

SCHEMA_VERSION = "1"


class ResultStore:
    """Thin, explicit persistence layer.  No ORM, no magic."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(str(self.path), timeout=30.0)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.executescript(_SCHEMA)
        self._connection.execute(
            "INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', ?)",
            (SCHEMA_VERSION,),
        )
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> ResultStore:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self._connection
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise

    # -- writes --------------------------------------------------------------
    def record(self, result: EvaluationResult, *, setup_digest: str, study: str = "") -> int:
        payload = (
            result.evaluation_hash,
            result.design.digest(),
            setup_digest,
            study,
            result.run_id,
            result.run_directory,
            result.outcome.value,
            result.state.value,
            result.failure_code.value if result.failure_code else None,
            result.message,
            json.dumps(result.design.as_dict()),
            json.dumps(result.metrics),
            json.dumps(result.constraint_violations),
            json.dumps([case.to_dict() for case in result.load_cases]),
            json.dumps(result.mesh.to_dict() if result.mesh else None),
            json.dumps(result.warnings),
            json.dumps(result.provenance),
            result.wall_time,
            result.created_at,
        )
        with self._transaction() as connection:
            cursor = connection.execute(
                """
                INSERT INTO evaluations (
                    evaluation_hash, design_digest, setup_digest, study, run_id,
                    run_directory, outcome, state, failure_code, message,
                    design_json, metrics_json, violations_json, load_cases_json,
                    mesh_json, warnings_json, provenance_json, wall_time, created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(evaluation_hash) DO UPDATE SET
                    outcome=excluded.outcome,
                    state=excluded.state,
                    failure_code=excluded.failure_code,
                    message=excluded.message,
                    metrics_json=excluded.metrics_json,
                    violations_json=excluded.violations_json,
                    load_cases_json=excluded.load_cases_json,
                    mesh_json=excluded.mesh_json,
                    warnings_json=excluded.warnings_json,
                    wall_time=excluded.wall_time
                """,
                payload,
            )
        return int(cursor.lastrowid or 0)

    def start_study(self, name: str, kind: str, settings: dict[str, Any]) -> None:
        import time

        now = time.time()
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO studies (name, kind, settings, state, created_at, updated_at)
                VALUES (?,?,?,'running',?,?)
                ON CONFLICT(name) DO UPDATE SET
                    settings=excluded.settings, state='running', updated_at=excluded.updated_at
                """,
                (name, kind, json.dumps(settings), now, now),
            )

    def finish_study(self, name: str, state: str = "complete") -> None:
        import time

        with self._transaction() as connection:
            connection.execute(
                "UPDATE studies SET state=?, updated_at=? WHERE name=?",
                (state, time.time(), name),
            )

    # -- reads ---------------------------------------------------------------
    def find_by_hash(self, evaluation_hash: str) -> dict[str, Any] | None:
        row = self._connection.execute(
            "SELECT * FROM evaluations WHERE evaluation_hash=?", (evaluation_hash,)
        ).fetchone()
        return _row_to_dict(row) if row else None

    def cached_result(self, evaluation_hash: str, space: DesignSpace) -> EvaluationResult | None:
        """Return a stored result, but only if it is safe to reuse.

        Infrastructure errors are deliberately *not* cached: a solver timeout
        last week says nothing about this design, and replaying it would make a
        transient failure permanent.
        """
        record = self.find_by_hash(evaluation_hash)
        if record is None:
            return None
        outcome = Outcome(record["outcome"])
        if outcome is Outcome.ERROR:
            return None
        design = space.decode(json.loads(record["design_json"]))
        result = EvaluationResult(
            design=design,
            outcome=outcome,
            state=EvaluationState(record["state"]),
            metrics=json.loads(record["metrics_json"]),
            constraint_violations=json.loads(record["violations_json"]),
            failure_code=(FailureCode(record["failure_code"]) if record["failure_code"] else None),
            message=record["message"],
            warnings=json.loads(record["warnings_json"]),
            run_id=record["run_id"],
            run_directory=record["run_directory"],
            evaluation_hash=record["evaluation_hash"],
            wall_time=record["wall_time"],
            from_cache=True,
            provenance=json.loads(record["provenance_json"]),
            created_at=record["created_at"],
        )
        return result

    def evaluations(
        self, study: str | None = None, outcome: Outcome | None = None
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM evaluations"
        clauses: list[str] = []
        parameters: list[Any] = []
        if study is not None:
            clauses.append("study=?")
            parameters.append(study)
        if outcome is not None:
            clauses.append("outcome=?")
            parameters.append(outcome.value)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY id"
        rows = self._connection.execute(query, parameters).fetchall()
        return [_row_to_dict(row) for row in rows]

    def designs_and_metrics(
        self, space: DesignSpace, study: str | None = None
    ) -> list[tuple[DesignVector, dict[str, float], str]]:
        out: list[tuple[DesignVector, dict[str, float], str]] = []
        for record in self.evaluations(study=study):
            if record["outcome"] == Outcome.ERROR.value:
                continue
            design = space.decode(json.loads(record["design_json"]))
            out.append((design, json.loads(record["metrics_json"]), record["outcome"]))
        return out

    def summary(self, study: str | None = None) -> dict[str, int]:
        counts: dict[str, int] = {"ok": 0, "infeasible": 0, "error": 0, "total": 0}
        for record in self.evaluations(study=study):
            counts[record["outcome"]] = counts.get(record["outcome"], 0) + 1
            counts["total"] += 1
        return counts

    def failure_breakdown(self, study: str | None = None) -> dict[str, int]:
        breakdown: dict[str, int] = {}
        for record in self.evaluations(study=study):
            code = record["failure_code"]
            if code:
                breakdown[code] = breakdown.get(code, 0) + 1
        return dict(sorted(breakdown.items(), key=lambda item: -item[1]))


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return dict(zip(row.keys(), tuple(row), strict=True))
