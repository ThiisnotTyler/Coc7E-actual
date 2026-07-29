"""Persistence + scenario loading (v2.8.1.x) — decoupled from keeper.py.

Everything that moves campaign data between the engine and disk:

  load_scenario      scenario.json -> locations, items, objects, NPCs,
                     fronts, clues, timeline (the v2.1 crash-point flattener
                     _character_from_scenario included)
  save_state         full snapshot via state_mod.save_world, with the
                     pending-menu stash (menus are runtime-only but must
                     survive the save that ends their turn)
  load_state         snapshot -> engine, with save migration, first-visit
                     memory, and the engine rebuild (spatial/combat/sanity)
  save_path          saves/<scenario_id>/world-state.json
  _registry_audit / _reconcile_inventory
                     the inventory invariant: legacy string gear migrates
                     into real item instances; corrupted refs are pruned,
                     never crash (v2.8.1.1 P0)

Functions take `keeper` explicitly (the local_voice.py /
narration_validator.py / commands.py / prompt_builder.py pattern).
state.py stays the snapshot FORMAT layer this module calls.
CoCKeeper keeps one-line delegates for the call sites and test surface.
"""
import json
import os

from src.character import Character, Weapon
from src.spatial import SpatialEngine, Location
from src.combat import CombatEngine
from src.sanity import SanityEngine
from src import items as items_mod
from src import state as state_mod


def save_path(keeper) -> str:
    return f"saves/{keeper.scenario_id}/world-state.json"

def load_scenario(keeper, scenario_path: str):
    with open(os.path.join(scenario_path, "scenario.json"), encoding="utf-8") as f:
        data = json.load(f)
    keeper.scenario_id = data.get("id", os.path.basename(scenario_path.rstrip("/")))
    # v2.8.1.6: name/tone ride the compact-retry prompt and the prompt's
    # scenario section.
    keeper.scenario_title = data.get("title", keeper.scenario_id)
    keeper.scenario_tone = str(data.get("description", ""))[:160]
    # v2.7.0: a local chronicle files itself under the loaded scenario
    if keeper.chronicle is not None and hasattr(keeper.chronicle, "set_scenario"):
        keeper.chronicle.set_scenario(keeper.scenario_id)
    keeper.fronts = data.get("fronts", {})
    keeper.current_scene = data.get("starting_location", "")
    keeper.clues = data.get("clues", [])
    keeper.timeline = data.get("timeline", [])

    # v2.8.0: scenario-specific template overrides and world objects.
    items_mod.merge_catalog(data.get("items", []), keeper.item_templates)
    for obj_data in data.get("objects", []):
        obj = items_mod.create_world_object(obj_data)
        keeper.world_objects[obj.id] = obj

    # v2.8.1: scenario item placement. "placed_items" creates instances in
    # rooms (or on NPCs) at campaign start:
    #   {"template": "brass_key", "location": "house_hallway",
    #    "name": optional, "quantity": optional, "tags": ["hidden", ...]}
    for place in data.get("placed_items", []):
        if not isinstance(place, dict):
            continue
        tmpl = keeper.item_templates.get(place.get("template"))
        if tmpl is None:
            continue
        inst = items_mod.create_instance(
            tmpl,
            owner_id=place.get("owner"),
            location_id=place.get("location"),
            quantity=int(place.get("quantity", 1)),
            name=place.get("name"),
            registry=keeper.item_instances,
        )
        for tag in place.get("tags", []):
            if tag not in inst.tags:
                inst.tags.append(tag)

    for loc_id, loc_data in data.get("locations", {}).items():
        loc_data = dict(loc_data)
        loc_data["occupants"] = set(loc_data.get("occupants", []))
        keeper.locations[loc_id] = Location(id=loc_id, **loc_data)

    for npc_data in data.get("npcs", []):
        npc = _character_from_scenario(keeper, npc_data, default_type="npc")
        keeper._register(npc)

def _character_from_scenario(keeper, d: dict, default_type: str = "npc") -> Character:
    """Flatten the scenario schema into Character kwargs (the v2.1 crash point)."""
    d = dict(d)
    chars = d.pop("characteristics", {}) or {}
    for k, v in chars.items():
        d.setdefault(k, v)
    d.setdefault("char_type", default_type)
    weapon = d.get("weapon")
    if isinstance(weapon, dict):
        known = set(Weapon.__dataclass_fields__)
        d["weapon"] = Weapon(**{k: v for k, v in weapon.items() if k in known})
    known = set(Character.__dataclass_fields__)
    core = {k: v for k, v in d.items() if k in known}
    leftovers = {k: v for k, v in d.items() if k not in known}  # attitude, notes...
    char = Character(**core)
    char.extra.update(leftovers)
    return char

def _registry_audit(keeper, char: Character, after: str = "") -> bool:
    """The inventory invariant is mandatory: every id resolves to a
    canonical ItemInstance. A corrupted or missing reference is pruned
    with a local audit message — the session never crashes on gear
    bookkeeping."""
    missing = [e for e in char.inventory
               if not isinstance(e, str) or e not in keeper.item_instances]
    for e in missing:
        char.inventory.remove(e)
    dangling = (char.equipped_item_id
                and char.equipped_item_id not in keeper.item_instances)
    if dangling:
        char.equipped_item_id = None
        char.refresh_weapon_view()
    if missing or dangling:
        print(f"  [Registry audit{': ' + after if after else ''} — removed "
              f"{len(missing)} unresolved item reference(s).]")
    return not missing and not dangling

def _reconcile_inventory(keeper, char: Character):
    """P0 root cause (field, v2.8.1.1): roster characters can carry legacy
    STRING inventory entries (display names saved before the item
    registry). The v2.8.0 save migration never ran on the roster path, so
    those names reached char.inventory with no ItemInstance behind them —
    and 'open' crashed dereferencing the .get() fallback. Migrate names
    into real instances here; anything unresolvable is pruned by audit."""
    needs = [e for e in char.inventory
             if isinstance(e, str) and e not in keeper.item_instances]
    dangling = (char.equipped_item_id
                and char.equipped_item_id not in keeper.item_instances)
    if not needs and not dangling:
        return
    d = char.to_dict()
    items_mod.migrate_character(d, keeper.item_templates, keeper.item_instances)
    char.inventory = [e for e in d.get("inventory", []) if isinstance(e, str)]
    char.equipped_item_id = d.get("equipped_item_id")
    char.refresh_weapon_view()
    _registry_audit(keeper, char, after="roster reconciliation")

def save_state(keeper):
    # v2.8.1.x P0-2: pending menus are runtime-only — stripped from the
    # serialized state but kept LIVE for the next input (an attack-target
    # menu staged this turn must survive the save that ends it).
    stashed = {}
    for c in keeper.characters.values():
        m = c.extra.pop("_last_menu", None)
        if m is not None:
            stashed[c.id] = m
    try:
        state_mod.save_world(
            keeper.save_path,
            turn=keeper.turn, current_scene=keeper.current_scene,
            fronts=keeper.fronts, plot_points=keeper.plot_points,
            characters=keeper.characters, locations=keeper.locations,
            timeline=keeper.timeline, pending_rolls=keeper.pending_rolls,
            item_instances=keeper.item_instances,
            world_objects=keeper.world_objects,
            visited=keeper.visited,
            visit_counts=keeper.visit_counts,
            discovered_clues=keeper.discovered_clues,
        )
    finally:
        for cid, m in stashed.items():
            keeper.characters[cid].extra["_last_menu"] = m

def load_state(keeper) -> bool:
    if not os.path.exists(keeper.save_path):
        return False
    with open(keeper.save_path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    # v2.8.0: migrate v2.7.x saves into the item registry before parsing.
    items_mod.migrate_save_data(raw, keeper.item_templates)
    data = state_mod.load_world_from_dict(raw)
    keeper.turn = data.get("turn", 0)
    keeper.current_scene = data.get("current_scene", keeper.current_scene)
    keeper.fronts = data.get("fronts", keeper.fronts)
    keeper.plot_points = data.get("plot_points", [])
    keeper.timeline = data.get("timeline", keeper.timeline)
    keeper.pending_rolls = data.get("pending_rolls", [])
    keeper.characters = data.get("characters", {})
    # v2.8.1.x P0-2: pending menus are runtime-only — strip any that a
    # pre-hotfix save may still carry.
    for c in keeper.characters.values():
        c.extra.pop("_last_menu", None)
    keeper.locations = data.get("locations", keeper.locations)
    keeper.item_instances = data.get("item_instances", {})
    keeper.world_objects = data.get("world_objects", {})
    # v2.8.1: first-visit memory and clue-reveal stamps (absent on old saves)
    keeper.visited = {cid: set(locs) for cid, locs in data.get("visited", {}).items()}
    keeper.discovered_clues = set(data.get("discovered_clues", []))
    # v2.8.1.1: visit counts; older v2.8.1 saves derive count=1 per room.
    keeper.visit_counts = {
        cid: {loc: int(n) for loc, n in counts.items()}
        for cid, counts in data.get("visit_counts", {}).items()
    }
    for cid, locs in keeper.visited.items():
        counts = keeper.visit_counts.setdefault(cid, {})
        for loc in locs:
            counts.setdefault(loc, 1)
    items_mod.set_runtime_registry(keeper.item_instances)
    keeper.spatial = SpatialEngine(keeper.locations)
    keeper.combat = CombatEngine(keeper.spatial, keeper.dice)
    keeper.sanity = SanityEngine(keeper.dice, keeper.combat, keeper.config.get("sanity", {}))
    return True
