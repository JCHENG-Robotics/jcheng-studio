"""Dependency-free paired-trial statistics shared by Studio and phase 3."""

from .core import (
    bootstrap_mean_ci,
    exact_binomial_two_sided,
    percentile,
    summary,
)

__all__ = [
    "bootstrap_mean_ci",
    "exact_binomial_two_sided",
    "percentile",
    "summary",
]
