"""
Heatmap rendering — numpy-only (no scipy dependency).

Given a list of (x, y, rssi) measurements and a floor plan size,
generate a PNG of the interpolated signal strength.

Approach:
  - Build a regular grid over the floor plan dimensions
  - Interpolate RSSI at each grid point using IDW
    (Inverse Distance Weighted) - power=2, vectorized in numpy
  - Render with matplotlib using the RdYlGn colormap
  - Output as a transparent PNG that the browser overlays on the floor plan

We fix vmin/vmax to dBm thresholds that make sense for WiFi:
  -30 dBm = excellent (right next to AP)
  -85 dBm = barely usable
"""
from __future__ import annotations

import io
from typing import List, Tuple

import numpy as np
import matplotlib

# Headless backend - no display needed
matplotlib.use("Agg")
import matplotlib.pyplot as plt


# RSSI range for the colormap. Keep these stable so colors are comparable
# across runs. -30 = excellent, -85 = usable floor.
RSSI_MIN = -85
RSSI_MAX = -30

SNR_MIN = 5
SNR_MAX = 50

# How many grid cells per axis to interpolate over.
# 200 is a good tradeoff - smooth enough, fast enough.
GRID_RESOLUTION = 200

# IDW power: higher = more local (sharper); lower = more global (smoother).
# 2 is the classical default and gives natural-looking falloff.
IDW_POWER = 2

# Tiny epsilon to avoid division by zero when a grid point coincides
# with a sample point.
EPS = 1e-9


class HeatmapError(Exception):
    """Raised when interpolation can't produce a heatmap."""


def compute_rssi_grid(
    points: List[Tuple[int, int, int]],
    width: int,
    height: int,
    resolution: int = GRID_RESOLUTION,
    clip_min: float = RSSI_MIN,
    clip_max: float = RSSI_MAX,
) -> np.ndarray:
    """
    Compute the IDW-interpolated grid without rendering.

    Args:
        points: list of (x, y, value) tuples in pixel coords
        width: floor plan width in pixels
        height: floor plan height in pixels
        resolution: grid cells per axis (default GRID_RESOLUTION)
        clip_min: lower bound for clipping (default RSSI_MIN)
        clip_max: upper bound for clipping (default RSSI_MAX)

    Returns:
        (resolution, resolution) numpy array of clipped values.

    Raises:
        HeatmapError if fewer than 3 points.
    """
    if len(points) < 3:
        raise HeatmapError(
            f"Need at least 3 points to interpolate (have {len(points)})"
        )

    sample_xs = np.array([p[0] for p in points], dtype=float)
    sample_ys = np.array([p[1] for p in points], dtype=float)
    sample_zs = np.array([p[2] for p in points], dtype=float)

    grid_z = _idw_interpolate(
        sample_xs, sample_ys, sample_zs, width, height, resolution
    )
    return np.clip(grid_z, clip_min, clip_max)


def render_heatmap(
    points: List[Tuple[int, int, int]],
    width: int,
    height: int,
    alpha: float = 0.6,
    vmin: float = RSSI_MIN,
    vmax: float = RSSI_MAX,
) -> bytes:
    """
    Render an interpolated RSSI heatmap as a PNG.

    Args:
        points: list of (x, y, rssi) tuples in pixel coords
        width: floor plan width in pixels
        height: floor plan height in pixels
        alpha: opacity of the heatmap (0.0 to 1.0)

    Returns:
        PNG bytes (transparent background, suitable for overlay)
    """
    grid_z = compute_rssi_grid(points, width, height, GRID_RESOLUTION,
                               clip_min=vmin, clip_max=vmax)

    # Render with matplotlib
    fig = plt.figure(
        figsize=(width / 100, height / 100),
        dpi=100,
        frameon=False,
    )
    ax = fig.add_axes([0, 0, 1, 1])  # full-bleed, no margins
    ax.set_xlim(0, width)
    ax.set_ylim(height, 0)  # flip Y so (0,0) is top-left like image coords
    ax.axis("off")

    ax.imshow(
        grid_z,
        extent=(0, width, height, 0),
        cmap="RdYlGn",
        vmin=vmin,
        vmax=vmax,
        alpha=alpha,
        interpolation="bilinear",
        aspect="auto",
    )

    # Save to bytes buffer with transparent background
    buf = io.BytesIO()
    fig.savefig(
        buf,
        format="png",
        transparent=True,
        bbox_inches="tight",
        pad_inches=0,
    )
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def _idw_interpolate(
    sample_xs: np.ndarray,
    sample_ys: np.ndarray,
    sample_zs: np.ndarray,
    width: int,
    height: int,
    resolution: int,
) -> np.ndarray:
    """
    Inverse Distance Weighted interpolation, fully vectorized in numpy.

    For each grid point (gx, gy), the interpolated value is:
        z(gx, gy) = sum(w_i * z_i) / sum(w_i)
    where w_i = 1 / (distance(grid, sample_i)^p + EPS)

    Returns a (resolution, resolution) array of interpolated values.
    """
    # Grid coordinates: shape (R, R)
    grid_x, grid_y = np.meshgrid(
        np.linspace(0, width, resolution),
        np.linspace(0, height, resolution),
    )

    # Flatten grid for vectorized distance calc: shape (R*R,)
    flat_gx = grid_x.ravel()
    flat_gy = grid_y.ravel()

    # Compute distance from each grid point to each sample point.
    # Result shape: (R*R, N) where N is number of sample points.
    dx = flat_gx[:, None] - sample_xs[None, :]
    dy = flat_gy[:, None] - sample_ys[None, :]
    dist_sq = dx * dx + dy * dy

    # IDW weights. Adding EPS avoids division by zero when a grid cell
    # falls exactly on a sample point.
    weights = 1.0 / (np.power(dist_sq, IDW_POWER / 2.0) + EPS)

    # Weighted sum: numerator/denominator both shape (R*R,)
    numerator = (weights * sample_zs[None, :]).sum(axis=1)
    denominator = weights.sum(axis=1)

    interpolated = numerator / denominator
    return interpolated.reshape(resolution, resolution)


def rssi_to_color(rssi: int) -> str:
    """
    Map an RSSI value to a CSS color string for marker dots.
    Same gradient as the heatmap (RdYlGn), but as a hex color.

    Used by the frontend to color individual point markers consistent
    with the heatmap colormap.
    """
    # Normalize to 0..1 within our display range
    t = (rssi - RSSI_MIN) / (RSSI_MAX - RSSI_MIN)
    t = max(0.0, min(1.0, t))

    cmap = plt.get_cmap("RdYlGn")
    r, g, b, _ = cmap(t)
    return "#{:02x}{:02x}{:02x}".format(int(r * 255), int(g * 255), int(b * 255))
