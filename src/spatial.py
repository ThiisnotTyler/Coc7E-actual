"""Spatial engine: location graph, BFS distance, perception, sound.

v2.2: restores the can_hear() method from the setup guide (the review draft
dropped it), keeps the review draft's safer BFS that tolerates missing nodes,
and adds occupant move helpers used by scene transitions.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Set, Tuple
from collections import deque


@dataclass
class Location:
    id: str
    name: str
    connections: Dict[str, dict] = field(default_factory=dict)
    sound_propagation: Dict[str, str] = field(default_factory=dict)
    line_of_sight: List[str] = field(default_factory=list)
    occupants: Set[str] = field(default_factory=set)
    # v2.8.1 Room Truth: stable authored text and presentation metadata.
    # All fields default empty so pre-v2.8.1 scenarios and saves load unchanged.
    description: str = ""                 # stable room description (fallback text)
    first_visit: str = ""                 # shown the first time a character sees the room
    revisit: str = ""                     # shown on later visits
    details: Dict[str, str] = field(default_factory=dict)   # public authored details
    lighting: str = ""                    # e.g. "dim", "pitch black"
    tags: List[str] = field(default_factory=list)           # e.g. "hazard", "mythos"
    # v2.8.1.3: authored passive entry check ({"skill": "Spot_Hidden"}).
    # Without it, entering a room never grants an inspection roll.
    entry_check: dict = field(default_factory=dict)
    # v2.8.1.x Phase 1: authored room scale — "small" | "medium" | "large".
    # Scales the nominal yards of position bands (position_yards below);
    # absent on older scenarios/saves, which read as "medium" (zero drift).
    span: str = "medium"


# Nominal yards per position band at MEDIUM span (the shipped baseline —
# combat's reach math and the 'distance' readout both derive from these).
POSITION_YARDS = {"close": 2, "near": 5, "far": 10, "elevated": 8,
                  "behind_cover": 5}

# v2.8.1.x Phase 1: how a room's authored span scales band yards. Body-scale
# absolutes (the 3y melee reach, point blank) are NOT scaled — span spreads
# positions apart, it does not change the physics of a fight.
SPAN_SCALE = {"small": 0.5, "medium": 1.0, "large": 3.0}


def position_yards(position: str, span: str = "medium") -> float:
    """Nominal yards for a position band in a room of the given span.
    Unknown spans read as medium — a typo never silently grows a room."""
    return POSITION_YARDS.get(position, 5) * SPAN_SCALE.get(span, 1.0)


class SpatialEngine:
    def __init__(self, locations: Dict[str, Location]):
        self.locations = locations
        self._cache = {}

    def get_distance(self, a: str, b: str) -> Tuple[float, List[str]]:
        """Hop distance between two location ids, plus the path taken."""
        if a == b:
            return 0.0, [a]
        if (a, b) in self._cache:
            return self._cache[(a, b)]
        queue = deque([(a, [a])])
        visited = {a}
        while queue:
            cur, path = queue.popleft()
            node = self.locations.get(cur)
            if node is None:
                continue
            for nxt in node.connections:
                if nxt == b:
                    result = (float(len(path)), path + [nxt])
                    self._cache[(a, b)] = self._cache[(b, a)] = result
                    return result
                if nxt not in visited:
                    visited.add(nxt)
                    queue.append((nxt, path + [nxt]))
        return float("inf"), []

    def get_perception_level(self, observer_loc: str, target_loc: str) -> str:
        d, _ = self.get_distance(observer_loc, target_loc)
        if d == 0:
            return "SAME"
        if d == 1:
            return "ADJACENT"
        if d == 2:
            return "DISTANT"
        return "OFF_SCREEN"

    def can_hear(self, listener_loc: str, sound_loc: str, noise: int) -> Tuple[bool, str]:
        """noise: 1 whisper, 2 conversation, 3 loud, 4 gunshot."""
        d, _ = self.get_distance(listener_loc, sound_loc)
        if d == 0:
            return True, "clear"
        if d == 1:
            loc = self.locations.get(listener_loc)
            prop = loc.sound_propagation.get(sound_loc, "none") if loc else "none"
            if prop == "none":
                return False, "none"
            if noise >= 3 or prop == "clear":
                return True, prop
            if noise >= 2 and prop == "muffled":
                return True, "muffled"
            return False, "none"
        if d == 2 and noise >= 4:
            return True, "very_faint"
        return False, "none"

    def move_occupant(self, char_id: str, from_loc: str, to_loc: str):
        if from_loc in self.locations:
            self.locations[from_loc].occupants.discard(char_id)
        if to_loc in self.locations:
            self.locations[to_loc].occupants.add(char_id)
