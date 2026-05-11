"""
Storage layer for survey data.

A "survey" is a JSON file containing:
  - the floor plan filename
  - a list of measurement points, each with (x, y) pixel coords and a WifiSample

The file lives at data/survey.json by default. We keep it human-readable so
it can be inspected/edited by hand if needed.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import List, Optional

from scanner import WifiSample


@dataclass
class AccessPoint:
    """A user-placed WiFi access point location on the floor plan."""
    id: str
    x: int
    y: int
    name: str          # user label, e.g. "TP-Link Living Room"
    bssid: str = ""    # optional: link to a measured BSSID

    def to_dict(self):
        return {
            "id": self.id,
            "x": self.x,
            "y": self.y,
            "name": self.name,
            "bssid": self.bssid,
        }

    @classmethod
    def from_dict(cls, d):
        return cls(
            id=d["id"],
            x=d["x"],
            y=d["y"],
            name=d["name"],
            bssid=d.get("bssid", ""),
        )


@dataclass
class Room:
    """A named rectangular region on the floor plan."""
    id: str
    name: str
    x1: int
    y1: int
    x2: int
    y2: int

    def to_dict(self):
        return {"id": self.id, "name": self.name,
                "x1": self.x1, "y1": self.y1, "x2": self.x2, "y2": self.y2}

    @classmethod
    def from_dict(cls, d):
        return cls(id=d["id"], name=d["name"],
                   x1=d["x1"], y1=d["y1"], x2=d["x2"], y2=d["y2"])


@dataclass
class SurveyPoint:
    """A single measurement: where you clicked + what was measured."""
    id: str
    x: int           # pixel coords on the floor plan
    y: int
    timestamp: float  # unix epoch seconds
    sample: WifiSample
    all_samples: List[dict] = field(default_factory=list)
    bssid_changed: bool = False
    note: str = ""
    download_mbps: Optional[float] = None

    def to_dict(self):
        return {
            "id": self.id,
            "x": self.x,
            "y": self.y,
            "timestamp": self.timestamp,
            "sample": self.sample.to_dict(),
            "all_samples": self.all_samples,
            "bssid_changed": self.bssid_changed,
            "note": self.note,
            "download_mbps": self.download_mbps,
        }

    @classmethod
    def from_dict(cls, d):
        return cls(
            id=d["id"],
            x=d["x"],
            y=d["y"],
            timestamp=d["timestamp"],
            sample=WifiSample(**d["sample"]),
            all_samples=d.get("all_samples", []),
            bssid_changed=d.get("bssid_changed", False),
            note=d.get("note", ""),
            download_mbps=d.get("download_mbps"),
        )


@dataclass
class Survey:
    """A complete survey: a floor plan plus the points measured on it."""
    floorplan: Optional[str] = None  # filename (relative to data/)
    points: List[SurveyPoint] = field(default_factory=list)
    access_points: List[AccessPoint] = field(default_factory=list)
    rooms: List[Room] = field(default_factory=list)

    def add_point(self, x: int, y: int, sample: WifiSample,
                  all_samples=None, bssid_changed=False,
                  download_mbps=None) -> SurveyPoint:
        point = SurveyPoint(
            id=uuid.uuid4().hex[:8],
            x=x,
            y=y,
            timestamp=time.time(),
            sample=sample,
            all_samples=all_samples or [],
            bssid_changed=bssid_changed,
            download_mbps=download_mbps,
        )
        self.points.append(point)
        return point

    def remove_point(self, point_id: str) -> bool:
        before = len(self.points)
        self.points = [p for p in self.points if p.id != point_id]
        return len(self.points) < before

    def clear_points(self):
        self.points = []

    def add_access_point(self, x: int, y: int, name: str, bssid: str = "") -> AccessPoint:
        ap = AccessPoint(
            id=uuid.uuid4().hex[:8],
            x=x,
            y=y,
            name=name,
            bssid=bssid,
        )
        self.access_points.append(ap)
        return ap

    def remove_access_point(self, ap_id: str) -> bool:
        before = len(self.access_points)
        self.access_points = [a for a in self.access_points if a.id != ap_id]
        return len(self.access_points) < before

    def add_room(self, name: str, x1: int, y1: int, x2: int, y2: int) -> Room:
        room = Room(
            id=uuid.uuid4().hex[:8],
            name=name,
            x1=min(x1, x2), y1=min(y1, y2),
            x2=max(x1, x2), y2=max(y1, y2),
        )
        self.rooms.append(room)
        return room

    def remove_room(self, room_id: str) -> bool:
        before = len(self.rooms)
        self.rooms = [r for r in self.rooms if r.id != room_id]
        return len(self.rooms) < before

    def get_unique_bssids(self) -> List[dict]:
        """Return list of {bssid, ssid, count} for each AP we've seen."""
        seen = {}
        for p in self.points:
            b = p.sample.bssid
            if not b:
                continue
            if b not in seen:
                seen[b] = {"bssid": b, "ssid": p.sample.ssid, "count": 0}
            seen[b]["count"] += 1
        return sorted(seen.values(), key=lambda x: -x["count"])

    def to_dict(self):
        return {
            "floorplan": self.floorplan,
            "points": [p.to_dict() for p in self.points],
            "access_points": [a.to_dict() for a in self.access_points],
            "rooms": [r.to_dict() for r in self.rooms],
        }

    @classmethod
    def from_dict(cls, d):
        return cls(
            floorplan=d.get("floorplan"),
            points=[SurveyPoint.from_dict(p) for p in d.get("points", [])],
            access_points=[AccessPoint.from_dict(a) for a in d.get("access_points", [])],
            rooms=[Room.from_dict(r) for r in d.get("rooms", [])],
        )


class SurveyStorage:
    """Loads and saves a Survey from/to a JSON file on disk."""

    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    def load(self) -> Survey:
        if not os.path.exists(self.path):
            return Survey()
        try:
            with open(self.path, "r") as f:
                data = json.load(f)
            return Survey.from_dict(data)
        except (json.JSONDecodeError, KeyError) as e:
            # Corrupt file - start fresh but don't lose the old data
            backup = self.path + ".corrupt"
            os.rename(self.path, backup)
            print(f"WARNING: survey file was corrupt, backed up to {backup}: {e}")
            return Survey()

    def save(self, survey: Survey):
        # Atomic write: write to .tmp then rename
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(survey.to_dict(), f, indent=2)
        os.replace(tmp, self.path)
