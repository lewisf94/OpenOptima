"""Design variables and design vectors."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .failures import EvaluationFailure, FailureCode


class VariableType(str, Enum):
    CONTINUOUS = "continuous"
    INTEGER = "integer"
    CATEGORICAL = "categorical"
    BOOLEAN = "boolean"


@dataclass(frozen=True)
class ActivationRule:
    """Makes a variable conditional on the value of another variable.

    Example: ``central_rib_thickness`` only matters when ``central_rib_enabled``
    is true.  Inactive variables are frozen at their default so they cannot
    perturb the evaluation hash.
    """

    variable: str
    equals: Any

    def is_active(self, values: Mapping[str, Any]) -> bool:
        if self.variable not in values:
            return True
        return values[self.variable] == self.equals


@dataclass(frozen=True)
class DesignVariable:
    id: str
    type: VariableType = VariableType.CONTINUOUS
    minimum: float | None = None
    maximum: float | None = None
    default: Any = None
    step: float | None = None
    choices: tuple[Any, ...] = ()
    unit: str = ""
    label: str = ""
    description: str = ""
    active_when: ActivationRule | None = None

    def __post_init__(self) -> None:
        if self.type in (VariableType.CONTINUOUS, VariableType.INTEGER):
            if self.minimum is None or self.maximum is None:
                raise ValueError(f"Variable {self.id!r} needs both minimum and maximum")
            if self.minimum > self.maximum:
                raise ValueError(
                    f"Variable {self.id!r} has minimum {self.minimum} above maximum {self.maximum}"
                )
        if self.type is VariableType.CATEGORICAL and not self.choices:
            raise ValueError(f"Categorical variable {self.id!r} needs choices")

    @property
    def display_name(self) -> str:
        return self.label or self.id

    def clamp(self, value: Any) -> Any:
        """Project a raw value onto this variable's domain.

        Optimisers work in a continuous box; this maps back onto integers,
        booleans and categories.  It does *not* silently rescue out-of-range
        user input — :meth:`validate` does that check.
        """
        if self.type is VariableType.CONTINUOUS:
            assert self.minimum is not None and self.maximum is not None
            value = min(max(float(value), self.minimum), self.maximum)
            if self.step:
                assert self.minimum is not None
                steps = round((value - self.minimum) / self.step)
                value = self.minimum + steps * self.step
                value = min(max(value, self.minimum), self.maximum)
            return float(value)
        if self.type is VariableType.INTEGER:
            assert self.minimum is not None and self.maximum is not None
            return int(min(max(round(float(value)), self.minimum), self.maximum))
        if self.type is VariableType.BOOLEAN:
            return bool(round(float(value))) if not isinstance(value, bool) else value
        # Categorical: an index into choices, or the choice itself.
        if value in self.choices:
            return value
        index = int(min(max(round(float(value)), 0), len(self.choices) - 1))
        return self.choices[index]

    def pinned_bound(self, value: Any) -> str | None:
        """Which of this variable's own limits the value is sitting on, if any.

        A design that lands exactly on a limit is the optimiser saying it would
        have gone further if allowed. That is worth telling the engineer,
        because the number was then chosen by the limit rather than by the
        physics -- see :meth:`DesignSpace.pinned_variables`.

        Compared against the values actually reachable rather than the stated
        limits: a step size can stop the optimiser short of the maximum, and
        that is still pinned.
        """
        if self.type not in (VariableType.CONTINUOUS, VariableType.INTEGER):
            return None
        if self.minimum is None or self.maximum is None:
            return None
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return None
        if math.isnan(numeric) or math.isinf(numeric):
            return None
        span = float(self.maximum) - float(self.minimum)
        if span <= 0:
            # Nothing to choose, so nothing to report. A variable fixed to one
            # value is not the optimiser being held back.
            return None
        tolerance = max(span * 1e-6, 1e-9)
        if abs(numeric - float(self.clamp(self.minimum))) <= tolerance:
            return "minimum"
        if abs(numeric - float(self.clamp(self.maximum))) <= tolerance:
            return "maximum"
        return None

    def validate(self, value: Any) -> None:
        if self.type in (VariableType.CONTINUOUS, VariableType.INTEGER):
            numeric = float(value)
            if math.isnan(numeric) or math.isinf(numeric):
                raise EvaluationFailure(
                    FailureCode.INVALID_DESIGN_VARIABLES,
                    f"Variable {self.id!r} is not a finite number: {value!r}",
                )
            assert self.minimum is not None and self.maximum is not None
            if numeric < self.minimum - 1e-9 or numeric > self.maximum + 1e-9:
                raise EvaluationFailure(
                    FailureCode.INVALID_DESIGN_VARIABLES,
                    f"Variable {self.id!r} value {numeric} is outside "
                    f"[{self.minimum}, {self.maximum}]",
                )
        elif self.type is VariableType.CATEGORICAL and value not in self.choices:
            raise EvaluationFailure(
                FailureCode.INVALID_DESIGN_VARIABLES,
                f"Variable {self.id!r} value {value!r} is not one of {list(self.choices)}",
            )

    def effective_default(self) -> Any:
        if self.default is not None:
            return self.default
        if self.type is VariableType.CONTINUOUS:
            assert self.minimum is not None and self.maximum is not None
            return 0.5 * (self.minimum + self.maximum)
        if self.type is VariableType.INTEGER:
            assert self.minimum is not None and self.maximum is not None
            return round(0.5 * (self.minimum + self.maximum))
        if self.type is VariableType.BOOLEAN:
            return False
        return self.choices[0]

    def optimiser_bounds(self) -> tuple[float, float]:
        """Bounds in the continuous space the optimiser searches."""
        if self.type is VariableType.CATEGORICAL:
            return (0.0, float(len(self.choices) - 1))
        if self.type is VariableType.BOOLEAN:
            return (0.0, 1.0)
        assert self.minimum is not None and self.maximum is not None
        return (float(self.minimum), float(self.maximum))


@dataclass(frozen=True)
class BoundPin:
    """A chosen value sitting on the limit it was given, not on an optimum."""

    variable_id: str
    label: str
    value: float
    bound: str
    limit: float
    unit: str = ""

    def describe(self) -> str:
        units = f" {self.unit}" if self.unit else ""
        direction = "smallest" if self.bound == "minimum" else "largest"
        wanted = "smaller" if self.bound == "minimum" else "larger"
        return (
            f"{self.label} is {self.value:g}{units}, the {direction} value allowed. "
            f"The limit chose this number, not the physics -- the search would "
            f"have gone {wanted} if it could."
        )


@dataclass(frozen=True)
class DesignSpace:
    """The ordered set of variables an optimiser may change."""

    variables: tuple[DesignVariable, ...]

    def __post_init__(self) -> None:
        seen: set[str] = set()
        for variable in self.variables:
            if variable.id in seen:
                raise ValueError(f"Duplicate design variable id {variable.id!r}")
            seen.add(variable.id)
        for variable in self.variables:
            rule = variable.active_when
            if rule is not None and rule.variable not in seen:
                raise ValueError(
                    f"Variable {variable.id!r} is conditional on unknown variable {rule.variable!r}"
                )

    def __len__(self) -> int:
        return len(self.variables)

    def __iter__(self) -> Iterator[DesignVariable]:
        return iter(self.variables)

    def __getitem__(self, key: str) -> DesignVariable:
        for variable in self.variables:
            if variable.id == key:
                return variable
        raise KeyError(key)

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(v.id for v in self.variables)

    def bounds(self) -> tuple[list[float], list[float]]:
        pairs = [v.optimiser_bounds() for v in self.variables]
        return [p[0] for p in pairs], [p[1] for p in pairs]

    def pinned_variables(self, design: Mapping[str, Any]) -> tuple[BoundPin, ...]:
        """Every value in this design that is sitting on one of its own limits.

        Worth reporting because it changes what the answer means. "The best
        fillet radius is 3 mm" and "3 mm is the sharpest corner you allowed me"
        are different statements, and only the second one tells the engineer
        that widening the range might find a better part.

        It matters most where a limit is protecting the result rather than the
        design. Minimising mass pushes an internal fillet towards its smallest
        allowed radius, because a bigger fillet means more material -- so the
        search walks towards the sharpest corner permitted, which is also where
        the stress measure is least trustworthy.
        """
        pins: list[BoundPin] = []
        for variable in self.variables:
            if variable.id not in design:
                continue
            bound = variable.pinned_bound(design[variable.id])
            if bound is None:
                continue
            limit = variable.minimum if bound == "minimum" else variable.maximum
            assert limit is not None
            pins.append(
                BoundPin(
                    variable_id=variable.id,
                    label=variable.display_name,
                    value=float(design[variable.id]),
                    bound=bound,
                    limit=float(limit),
                    unit=variable.unit,
                )
            )
        return tuple(pins)

    def defaults(self) -> DesignVector:
        return self.decode({v.id: v.effective_default() for v in self.variables})

    def decode(self, values: Mapping[str, Any]) -> DesignVector:
        """Turn raw values into a canonical, validated :class:`DesignVector`.

        Inactive conditional variables are pinned to their default so that two
        designs which differ only in an irrelevant variable hash identically and
        share a cache entry.
        """
        clamped: dict[str, Any] = {}
        for variable in self.variables:
            raw = values.get(variable.id, variable.effective_default())
            clamped[variable.id] = variable.clamp(raw)

        resolved: dict[str, Any] = {}
        for variable in self.variables:
            if variable.active_when is not None and not variable.active_when.is_active(clamped):
                resolved[variable.id] = variable.effective_default()
            else:
                resolved[variable.id] = clamped[variable.id]
                self[variable.id].validate(resolved[variable.id])
        return DesignVector(values=resolved, space=self)

    def from_array(self, array: list[float]) -> DesignVector:
        if len(array) != len(self.variables):
            raise ValueError(f"Expected {len(self.variables)} values, received {len(array)}")
        raw = dict(zip(self.ids, array, strict=True))
        return self.decode(raw)


@dataclass(frozen=True)
class DesignVector:
    """A single, canonical point in the design space."""

    values: Mapping[str, Any]
    space: DesignSpace = field(repr=False)

    def __getitem__(self, key: str) -> Any:
        return self.values[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)

    def as_dict(self) -> dict[str, Any]:
        return dict(self.values)

    def to_array(self) -> list[float]:
        out: list[float] = []
        for variable in self.space:
            value = self.values[variable.id]
            if variable.type is VariableType.CATEGORICAL:
                out.append(float(variable.choices.index(value)))
            elif variable.type is VariableType.BOOLEAN:
                out.append(1.0 if value else 0.0)
            else:
                out.append(float(value))
        return out

    def canonical_text(self) -> str:
        """Deterministic textual form used for hashing and run labels.

        Floats are rounded to 9 significant figures so that arithmetic noise in
        an optimiser does not defeat the cache.
        """
        parts: list[str] = []
        for variable in self.space:
            value = self.values[variable.id]
            if isinstance(value, bool):
                text = "true" if value else "false"
            elif isinstance(value, float):
                text = f"{value:.9g}"
            else:
                text = str(value)
            parts.append(f"{variable.id}={text}")
        return "\n".join(parts)

    def digest(self) -> str:
        return hashlib.sha256(self.canonical_text().encode("utf-8")).hexdigest()[:16]
