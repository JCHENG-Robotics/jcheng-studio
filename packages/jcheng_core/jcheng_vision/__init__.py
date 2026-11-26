"""Pure-NumPy point-cloud primitives shared by Studio and phase 2."""

from .core import (
    connected_components,
    describe_cluster,
    fit_slab,
    min_area_rectangle,
    select_cluster,
    top_view_gate,
    voxel_labels,
)

__all__ = [
    "connected_components",
    "describe_cluster",
    "fit_slab",
    "min_area_rectangle",
    "select_cluster",
    "top_view_gate",
    "voxel_labels",
]
