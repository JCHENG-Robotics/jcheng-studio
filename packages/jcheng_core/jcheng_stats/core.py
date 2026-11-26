"""The statistics helpers previously copied in two experiment modules."""

from __future__ import annotations

import math
import random
import statistics
from typing import Any


def summary(values: list[float]) -> dict[str, Any]:
    """Mean/std/min/max and the normal-approximation 95 % mean interval."""
    if not values:
        return {
            "n": 0,
            "mean": None,
            "std": None,
            "min": None,
            "max": None,
            "ci95_mean": None,
        }
    count = len(values)
    mean = statistics.fmean(values)
    if count > 1:
        std = statistics.stdev(values)
        ci95 = 1.96 * std / math.sqrt(count)
    else:
        std = 0.0
        ci95 = 0.0
    return {
        "n": count,
        "mean": mean,
        "std": std,
        "min": min(values),
        "max": max(values),
        "ci95_mean": ci95,
    }


def percentile(values: list[float], fraction: float) -> float:
    """Linear-interpolated percentile."""
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * fraction
    lower = int(math.floor(rank))
    upper = int(math.ceil(rank))
    if lower == upper:
        return ordered[lower]
    weight = rank - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def bootstrap_mean_ci(
    values: list[float],
    samples: int,
    seed: int,
    confidence: float,
) -> dict[str, Any]:
    """Seeded percentile bootstrap for the mean."""
    if len(values) < 2:
        mean = values[0] if values else None
        return {
            "samples": samples,
            "seed": seed,
            "low": mean,
            "high": mean,
            "mean": mean,
        }
    generator = random.Random(seed)
    draws = []
    count = len(values)
    for _ in range(samples):
        total = 0.0
        for _ in range(count):
            total += values[generator.randrange(count)]
        draws.append(total / count)
    alpha = (1.0 - confidence) / 2.0
    return {
        "samples": samples,
        "seed": seed,
        "low": percentile(draws, alpha),
        "high": percentile(draws, 1.0 - alpha),
        "mean": statistics.fmean(draws),
    }


def exact_binomial_two_sided(successes: int, failures: int) -> float:
    """Exact two-sided sign/McNemar p-value under p=0.5."""
    total = successes + failures
    if total == 0:
        return 1.0
    tail = min(successes, failures)
    probability = 0.0
    for count in range(tail + 1):
        probability += math.comb(total, count)
    probability = 2.0 * probability / (2**total)
    return min(probability, 1.0)
