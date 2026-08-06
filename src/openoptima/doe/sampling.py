"""Design of experiments.

DOE runs *before* optimisation, and is not optional in any study worth trusting.
It answers questions the optimiser cannot: which variables actually matter,
where the geometry falls apart, how often meshing fails, and whether the
responses are smooth enough for a surrogate to help.  It also gives the
optimiser a spread starting population instead of a random huddle.
"""

from __future__ import annotations

import math

import numpy as np
from scipy.stats import qmc

from ..domain.variables import DesignSpace, DesignVector


def sample_unit(method: str, count: int, dimensions: int, seed: int = 1) -> np.ndarray:
    """Sample ``count`` points in the unit hypercube."""
    if count <= 0:
        return np.zeros((0, dimensions))
    if dimensions == 0:
        return np.zeros((count, 0))

    method = method.lower()
    if method == "sobol":
        engine = qmc.Sobol(d=dimensions, scramble=True, seed=seed)
        # Sobol's balance properties hold for powers of two; request the next
        # one up and trim rather than silently sampling an unbalanced set.
        exponent = max(0, math.ceil(math.log2(max(count, 1))))
        return np.asarray(engine.random_base2(m=exponent))[:count]
    if method in ("lhs", "latin_hypercube"):
        engine = qmc.LatinHypercube(d=dimensions, seed=seed)
        return np.asarray(engine.random(n=count))
    if method == "random":
        return np.asarray(np.random.default_rng(seed).random((count, dimensions)))
    if method == "factorial":
        levels = max(2, round(count ** (1.0 / dimensions)))
        axes = [np.linspace(0.0, 1.0, levels) for _ in range(dimensions)]
        grid = np.stack(np.meshgrid(*axes, indexing="ij"), axis=-1).reshape(-1, dimensions)
        return grid[:count]
    raise ValueError(
        f"unknown sampling method {method!r}; expected sobol, lhs, random or factorial"
    )


def sample_design_space(
    space: DesignSpace, method: str, count: int, seed: int = 1
) -> list[DesignVector]:
    """Generate a space-filling set of design vectors.

    Integer, boolean and categorical variables are handled by the design space's
    own decoding, so a Sobol point maps onto a legal design in every case.
    """
    unit = sample_unit(method, count, len(space), seed=seed)
    lower, upper = space.bounds()
    lower_array = np.asarray(lower)
    upper_array = np.asarray(upper)
    scaled = lower_array + unit * (upper_array - lower_array)
    return [space.from_array(row.tolist()) for row in scaled]


def include_corners(space: DesignSpace) -> list[DesignVector]:
    """The all-minimum and all-maximum designs.

    Worth evaluating explicitly: they bracket the design range, and if a region
    selector is going to become ambiguous anywhere it is usually at an extreme.
    """
    lower, upper = space.bounds()
    return [space.from_array(list(lower)), space.from_array(list(upper))]
