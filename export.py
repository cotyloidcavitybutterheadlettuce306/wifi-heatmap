"""
PNG export — composites floor plan + heatmap + markers + legend.

Separated from app.py so the Flask routes stay thin.
"""
from __future__ import annotations

import colorsys
import io
from typing import List

from PIL import Image, ImageDraw, ImageFont

from heatmap import render_heatmap, rssi_to_color, HeatmapError, RSSI_MIN, RSSI_MAX


def hex_to_rgb(hex_color: str) -> tuple:
    h = hex_color.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def bssid_color_rgb(bssid: str) -> tuple:
    if not bssid:
        return (100, 100, 100)
    h = 0
    for c in bssid:
        h = ((h << 5) - h) + ord(c)
        h &= 0xFFFFFFFF
    hue = (h % 360) / 360.0
    r, g, b = colorsys.hls_to_rgb(hue, 0.55, 0.7)
    return (int(r * 255), int(g * 255), int(b * 255))


def get_font(size: int):
    for path in [
        "/System/Library/Fonts/SFNSMono.ttf",
        "/System/Library/Fonts/Menlo.ttc",
        "/System/Library/Fonts/Helvetica.ttc",
    ]:
        try:
            return ImageFont.truetype(path, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def draw_legend(draw: ImageDraw.ImageDraw, w: int, h: int) -> None:
    """Draw RSSI color legend in the bottom-right corner."""
    lw, lh = 200, 12
    margin = 16
    x0 = w - lw - margin
    y0 = h - lh - margin - 20

    draw.rectangle([x0 - 10, y0 - 10, x0 + lw + 10, y0 + lh + 26],
                    fill=(21, 24, 29, 200))

    colors = ["#a50026", "#d73027", "#f46d43", "#fdae61", "#fee08b",
              "#d9ef8b", "#a6d96a", "#66bd63", "#1a9850", "#006837"]
    seg_w = lw / len(colors)
    for i, c in enumerate(colors):
        rgb = hex_to_rgb(c)
        draw.rectangle([x0 + i * seg_w, y0, x0 + (i + 1) * seg_w, y0 + lh],
                        fill=rgb)

    font = get_font(10)
    draw.text((x0, y0 + lh + 4), f"{RSSI_MIN} dBm",
              fill=(255, 255, 255, 180), font=font)
    draw.text((x0 + lw - 45, y0 + lh + 4), f"{RSSI_MAX} dBm",
              fill=(255, 255, 255, 180), font=font)


def render_export(
    floorplan_path: str,
    survey,
    bssid_filter: str = None,
    show_heatmap: bool = False,
    alpha: float = 0.6,
) -> bytes:
    """
    Composite floor plan + heatmap + markers + AP labels + legend.

    Returns PNG bytes.
    """
    base = Image.open(floorplan_path).convert("RGBA")
    w, h = base.size

    # Heatmap overlay
    if show_heatmap:
        candidates = [
            p for p in survey.points
            if p.sample.rssi is not None and (
                bssid_filter is None or p.sample.bssid == bssid_filter
            )
        ]
        triples = [(p.x, p.y, p.sample.rssi) for p in candidates]
        if len(triples) >= 3:
            try:
                png_bytes = render_heatmap(triples, w, h, alpha=alpha)
                hm = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
                if hm.size != base.size:
                    hm = hm.resize(base.size, Image.LANCZOS)
                base = Image.alpha_composite(base, hm)
            except HeatmapError:
                pass

    draw = ImageDraw.Draw(base)

    # Measurement markers
    filtered = [
        p for p in survey.points
        if p.sample.rssi is not None and (
            bssid_filter is None or p.sample.bssid == bssid_filter
        )
    ]
    for p in filtered:
        color_rgb = hex_to_rgb(rssi_to_color(p.sample.rssi))
        bc = bssid_color_rgb(p.sample.bssid)
        draw.ellipse([p.x - 10, p.y - 10, p.x + 10, p.y + 10],
                      fill=bc, outline=bc)
        draw.ellipse([p.x - 7, p.y - 7, p.x + 7, p.y + 7],
                      fill=color_rgb + (255,), outline=(255, 255, 255, 200))
        if p.bssid_changed:
            draw.ellipse([p.x + 5, p.y - 10, p.x + 13, p.y - 2],
                          fill=(92, 216, 213, 255))

    # AP markers
    font = get_font(11)
    for ap in survey.access_points:
        draw.ellipse([ap.x - 13, ap.y - 13, ap.x + 13, ap.y + 13],
                      fill=(92, 216, 213, 220), outline=(255, 255, 255, 200),
                      width=2)
        draw.text((ap.x + 16, ap.y - 7), ap.name,
                  fill=(255, 255, 255, 220), font=font)

    draw_legend(draw, w, h)

    buf = io.BytesIO()
    base.save(buf, format="PNG")
    buf.seek(0)
    return buf.read()
