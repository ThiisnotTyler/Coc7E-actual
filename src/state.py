"""World-state persistence — full campaign snapshot.

v2.8.0: also round-trips the item instance registry and world object registry.
"""
import json
import os
from typing import Dict

from src.character import Character
from src.spatial import Location
from src.items import ItemInstance, WorldObject


def save_world(path: str, *, turn: int, current_scene: str, fronts: dict,
               plot_points: list, characters: Dict[str, Character],
               locations: Dict[str, Location], timeline: list,
               pending_rolls: list = None,
               item_instances: Dict[str, ItemInstance] = None,
               world_objects: Dict[str, WorldObject] = None,
               visited: Dict[str, list] = None,
               visit_counts: Dict[str, dict] = None,
               discovered_clues: list = None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = {
        "turn": turn,
        "current_scene": current_scene,
        "fronts": fronts,
        "plot_points": plot_points,
        "timeline": timeline,
        # v2.7.1: unanswered LLM dice requests survive quit/resume
        "pending_rolls": pending_rolls or [],
        "characters": {cid: c.to_dict() for cid, c in characters.items()},
        "locations": {
            lid: {
                "name": loc.name,
                "connections": loc.connections,
                "sound_propagation": loc.sound_propagation,
                "line_of_sight": loc.line_of_sight,
                "occupants": sorted(loc.occupants),
                # v2.8.1 Room Truth fields (empty on pre-v2.8.1 scenarios)
                "description": loc.description,
                "first_visit": loc.first_visit,
                "revisit": loc.revisit,
                "details": loc.details,
                "lighting": loc.lighting,
                "tags": loc.tags,
                "entry_check": loc.entry_check,
            } for lid, loc in locations.items()
        },
        # v2.8.0: canonical item/object registries
        "item_instances": {
            iid: inst.to_dict() for iid, inst in (item_instances or {}).items()
        },
        "world_objects": {
            oid: obj.to_dict() for oid, obj in (world_objects or {}).items()
        },
        # v2.8.1: first-visit memory and discovered clue ids
        "visited": {cid: sorted(locs) for cid, locs in (visited or {}).items()},
        "visit_counts": {cid: dict(counts) for cid, counts in (visit_counts or {}).items()},
        "discovered_clues": sorted(discovered_clues or []),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def load_world(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return load_world_from_dict(data)


def load_world_from_dict(data: dict) -> dict:
    """Deserialize a save dict that has already been read from JSON.

    New in v2.8.0: callers (e.g., CoCKeeper.load_state) may migrate legacy
    data before passing it here.
    """
    data.setdefault("pending_rolls", [])   # saves from before v2.7.1
    data.setdefault("visited", {})         # saves from before v2.8.1
    data.setdefault("visit_counts", {})    # saves from before v2.8.1.1
    data.setdefault("discovered_clues", [])
    data["characters"] = {
        cid: Character.from_dict(cd) for cid, cd in data.get("characters", {}).items()
    }
    data["locations"] = {
        lid: Location(
            id=lid,
            name=ld.get("name", lid),
            connections=ld.get("connections", {}),
            sound_propagation=ld.get("sound_propagation", {}),
            line_of_sight=ld.get("line_of_sight", []),
            occupants=set(ld.get("occupants", [])),
            # v2.8.1 fields default empty on older saves
            description=ld.get("description", ""),
            first_visit=ld.get("first_visit", ""),
            revisit=ld.get("revisit", ""),
            details=ld.get("details", {}),
            lighting=ld.get("lighting", ""),
            tags=ld.get("tags", []),
            entry_check=ld.get("entry_check", {}),
        ) for lid, ld in data.get("locations", {}).items()
    }
    data["item_instances"] = {
        iid: ItemInstance.from_dict(idata)
        for iid, idata in data.get("item_instances", {}).items()
    }
    data["world_objects"] = {
        oid: WorldObject.from_dict(odata)
        for oid, odata in data.get("world_objects", {}).items()
    }
    return data
