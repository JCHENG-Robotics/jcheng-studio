"""Voxel segmentation and top-face primitives previously kept in two copies."""

from __future__ import annotations

import math
from typing import Any

import numpy as np


def voxel_labels(points: np.ndarray, pitch: float) -> np.ndarray:
    """Return per-point voxel coordinates as an (N, 3) int array."""
    return np.floor(points / pitch).astype(np.int64)


def connected_components(
    points: np.ndarray,
    pitch: float,
    min_points: int = 20,
) -> tuple[np.ndarray, list[dict[str, Any]]]:
    """Euclidean components on a voxel grid with 26-neighbour connectivity."""
    if len(points) == 0:
        return np.full(0, -1, dtype=np.int64), []
    voxels = voxel_labels(points, pitch)
    keyed: dict[tuple[int, int, int], list[int]] = {}
    for index, voxel in enumerate(map(tuple, voxels)):
        keyed.setdefault(voxel, []).append(index)

    parent = {voxel: voxel for voxel in keyed}

    def find(item: tuple[int, int, int]) -> tuple[int, int, int]:
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    def union(a: tuple[int, int, int], b: tuple[int, int, int]) -> None:
        root_a, root_b = find(a), find(b)
        if root_a != root_b:
            parent[root_b] = root_a

    neighbours = [
        (dx, dy, dz)
        for dx in (-1, 0, 1)
        for dy in (-1, 0, 1)
        for dz in (-1, 0, 1)
        if (dx, dy, dz) != (0, 0, 0)
    ]
    for voxel in keyed:
        for delta in neighbours:
            other = (
                voxel[0] + delta[0],
                voxel[1] + delta[1],
                voxel[2] + delta[2],
            )
            if other in keyed:
                union(voxel, other)

    groups: dict[tuple[int, int, int], list[int]] = {}
    for voxel in keyed:
        groups.setdefault(find(voxel), []).extend(keyed[voxel])

    clusters: list[dict[str, Any]] = []
    labels = np.full(len(points), -1, dtype=np.int64)
    for number, (_, indices) in enumerate(
        sorted(groups.items(), key=lambda item: -len(item[1]))
    ):
        if len(indices) < min_points:
            continue
        index_array = np.array(sorted(indices), dtype=np.int64)
        clusters.append(
            {
                "label": number,
                "indices": index_array,
                "points": points[index_array],
            }
        )
        labels[index_array] = number
    return labels, clusters


def min_area_rectangle(
    xy: np.ndarray, step_deg: float = 1.0
) -> tuple[float, float, float, float]:
    """Return ``(yaw_rad, width_m, depth_m, area_m2)`` for an XY set."""
    if len(xy) == 0:
        return 0.0, 0.0, 0.0, 0.0
    angles = np.deg2rad(np.arange(0.0, 90.0 + step_deg, step_deg))
    cosine = np.cos(angles)[:, None]
    sine = np.sin(angles)[:, None]
    rotated_x = xy[None, :, 0] * cosine - xy[None, :, 1] * sine
    rotated_y = xy[None, :, 0] * sine + xy[None, :, 1] * cosine
    widths = rotated_x.max(axis=1) - rotated_x.min(axis=1)
    depths = rotated_y.max(axis=1) - rotated_y.min(axis=1)
    areas = widths * depths
    best = int(np.argmin(areas))
    return (
        float(angles[best]),
        float(widths[best]),
        float(depths[best]),
        float(areas[best]),
    )


def fit_slab(points: np.ndarray, slab_m: float = 0.005) -> dict[str, Any]:
    """Fit the highest slab (top face) of a cluster."""
    if len(points) == 0:
        raise ValueError("empty cluster")
    z_max = float(points[:, 2].max())
    slab = points[points[:, 2] >= z_max - slab_m]
    centroid = slab.mean(axis=0)
    centred = slab - centroid
    _, _, vh = np.linalg.svd(centred, full_matrices=False)
    normal = vh[-1]
    if normal[2] < 0:
        normal = -normal
    tilt_deg = math.degrees(
        math.acos(max(-1.0, min(1.0, float(normal[2]))))
    )
    yaw, width, depth, area = min_area_rectangle(slab[:, :2])
    return {
        "slab_points": int(len(slab)),
        "slab_thickness_m": slab_m,
        "centroid": [float(value) for value in centroid],
        "normal": [float(value) for value in normal],
        "tilt_deg": tilt_deg,
        "yaw_rad": yaw,
        "extent_m": [width, depth],
        "area_m2": area,
        "z_max": z_max,
    }


def top_view_gate(
    top: dict[str, Any], min_extent_m: float
) -> tuple[bool, str | None, float]:
    """Reject a top face clipped by the image border."""
    extent = min(float(value) for value in top["extent_m"])
    if extent < min_extent_m:
        return False, "partial_top_view", extent
    return True, None, extent


def describe_cluster(points: np.ndarray) -> dict[str, Any]:
    """Return bounds and height statistics for a cluster."""
    minimum = points.min(axis=0)
    maximum = points.max(axis=0)
    return {
        "points": int(len(points)),
        "min": [float(value) for value in minimum],
        "max": [float(value) for value in maximum],
        "size": [float(value) for value in (maximum - minimum)],
        "centroid": [float(value) for value in points.mean(axis=0)],
    }


def select_cluster(
    clusters: list[dict[str, Any]],
    floor_z: float | None,
    gates: dict[str, Any],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Pick the component that looks like the expected floor cube."""
    reasons = []
    best = None
    for cluster in clusters:
        points = cluster["points"]
        bounds = describe_cluster(points)
        size = bounds["size"]
        centroid = bounds["centroid"]
        height = (
            bounds["max"][2] - floor_z
            if floor_z is not None
            else float("nan")
        )
        ok = True
        why = []
        if len(points) < gates["min_points"]:
            ok = False
            why.append("too_few_points")
        if max(size[0], size[1]) > gates["max_xy_m"]:
            ok = False
            why.append("too_wide")
        if not gates["min_height_m"] <= height <= gates["max_height_m"]:
            ok = False
            why.append("height_%.3f" % height)
        if not gates["x_range"][0] <= centroid[0] <= gates["x_range"][1]:
            ok = False
            why.append("x_out_of_region")
        if abs(centroid[1]) > gates["y_range"]:
            ok = False
            why.append("y_out_of_region")
        reasons.append(
            {
                "label": cluster["label"],
                "bounds": bounds,
                "height_m": float(height),
                "ok": ok,
                "why": why,
            }
        )
        if ok and (best is None or len(points) > len(best["points"])):
            best = cluster
    return best, reasons
