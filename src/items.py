"""Canonical item and object registry for the v2.8.0 campaign engine.

Provides:
- ItemTemplate: catalog definition for a kind of item.
- ItemInstance: a persistent, campaign-specific physical item.
- WorldObject: persistent room scenery (doors, containers, furniture, ...).
- Catalog loader for data/items.json plus scenario overrides.
- Instance factory and migration helpers from v2.7.6.1 saves.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional


@dataclass
class ItemTemplate:
    id: str
    name: str
    item_type: str          # weapon, ammo, key, document, consumable, tool, container, valuable, clue, light_source, misc
    tags: List[str] = field(default_factory=list)
    default_state: dict = field(default_factory=dict)
    skill_key: Optional[str] = None   # for weapons: Firearms_Handgun, Fighting_Brawl, ...
    damage: Optional[str] = None
    base_range: int = 0
    rof: int = 1
    ammo_capacity: Optional[int] = None
    malfunction: int = 100
    is_shotgun: bool = False
    is_short_barrel: bool = False
    impales: Optional[bool] = None      # None -> auto: bullets impale (base_range>0)
    stackable: bool = False
    max_stack: int = 1
    ammo_type: Optional[str] = None   # e.g., 'handgun', 'shotgun', 'generic'
    description: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ItemTemplate":
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class ItemInstance:
    id: str                 # unique in campaign, e.g., "item_<uuid>"
    template_id: str
    name: str               # display name, may differ from template
    item_type: str
    owner_id: Optional[str] = None      # character id
    location_id: Optional[str] = None   # room id (if not carried)
    container_id: Optional[str] = None  # item id of container
    quantity: int = 1
    ammo: Optional[int] = None
    condition: str = "intact"           # intact, jammed, damaged, destroyed
    state: dict = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ItemInstance":
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class WorldObject:
    id: str
    name: str
    location_id: str
    object_type: str       # door, container, furniture, obstacle, clue_surface
    state: str = "intact"  # intact, broken, open, closed, locked, hidden, destroyed
    properties: dict = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "WorldObject":
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in d.items() if k in known})


# --------------------------------------------------------------------------- registry
_RUNTIME_INSTANCES: Dict[str, ItemInstance] = {}


def set_runtime_registry(instances: Dict[str, ItemInstance]):
    """Point the compatibility layer at the current campaign's instance registry."""
    global _RUNTIME_INSTANCES
    _RUNTIME_INSTANCES = instances


def get_runtime_registry() -> Dict[str, ItemInstance]:
    return _RUNTIME_INSTANCES


def register_instance(inst: ItemInstance, registry: Optional[Dict[str, ItemInstance]] = None):
    """Register an instance in the runtime registry (and optionally a keeper registry)."""
    _RUNTIME_INSTANCES[inst.id] = inst
    if registry is not None:
        registry[inst.id] = inst


def get_instance(inst_id: Optional[str]) -> Optional[ItemInstance]:
    if not inst_id:
        return None
    return _RUNTIME_INSTANCES.get(inst_id)


def instance_to_weapon(inst: ItemInstance,
                       catalog: Optional[Dict[str, ItemTemplate]] = None) -> "Weapon":
    """Build a transient Weapon view from an item instance.

    This keeps the existing combat code small: `attacker.weapon` still returns a
    Weapon, but the canonical ammo/condition live in the ItemInstance registry.
    """
    from src.character import Weapon  # lazy: avoids circular top-level import
    tmpl = None
    if catalog is not None:
        tmpl = catalog.get(inst.template_id)
    if tmpl is None:
        # fall back to the runtime catalog built from data/items.json
        tmpl = _CATALOG.get(inst.template_id)
    if tmpl is None:
        return Weapon(name=inst.name, damage="1D3", base_range=0)

    ammo = inst.ammo
    if ammo is None and tmpl.ammo_capacity is not None:
        ammo = tmpl.ammo_capacity
    if ammo is None:
        ammo = 0
    return Weapon(
        name=inst.name,
        damage=tmpl.damage or "1D3",
        base_range=tmpl.base_range,
        rof=tmpl.rof,
        ammo=ammo,
        malfunction=tmpl.malfunction,
        is_shotgun=tmpl.is_shotgun,
        is_short_barrel=tmpl.is_short_barrel,
        # RAW: bullets impale; melee only when the template says so.
        impales=(tmpl.impales if tmpl.impales is not None
                 else tmpl.base_range > 0),
        skill_key=tmpl.skill_key,
    )


def firearm_skill_key(weapon=None, template=None) -> str:
    """The firearm skill a weapon actually uses (v2.8.1.x field fix —
    a Hunting Rifle rolled Handgun because only shotguns were mapped).

    Order: (a) the template's authored skill_key; (b) is_shotgun ->
    Rifle_Shotgun; (c) template tags or the weapon's name indicating a
    rifle/long arm -> Rifle_Shotgun; (d) otherwise Handgun."""
    authored = (getattr(template, "skill_key", None)
                or getattr(weapon, "skill_key", None))
    if authored:
        return authored
    if getattr(weapon, "is_shotgun", False):
        return "Firearms_Rifle_Shotgun"
    name = (getattr(weapon, "name", "") or "").lower()
    tags = {str(t).lower() for t in (getattr(template, "tags", None) or [])}
    if tags & {"rifle", "longarm", "long_arm"} or "rifle" in name:
        return "Firearms_Rifle_Shotgun"
    return "Firearms_Handgun"


# --------------------------------------------------------------------------- catalog
_CATALOG: Dict[str, ItemTemplate] = {}


def load_catalog(path: str = "data/items.json") -> Dict[str, ItemTemplate]:
    """Load the global item-template catalog.

    Returns a dict keyed by template id. Per-scenario overrides can be merged
    with merge_catalog().
    """
    global _CATALOG
    _CATALOG = {}
    if not os.path.exists(path):
        return _CATALOG
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    for entry in data.get("templates", []):
        tmpl = ItemTemplate.from_dict(entry)
        _CATALOG[tmpl.id] = tmpl
    return _CATALOG


def merge_catalog(templates: List[dict],
                  base: Optional[Dict[str, ItemTemplate]] = None) -> Dict[str, ItemTemplate]:
    """Merge a list of template dicts into a catalog."""
    base = base if base is not None else _CATALOG
    for entry in templates:
        tmpl = ItemTemplate.from_dict(entry)
        base[tmpl.id] = tmpl
    return base


def get_template(template_id: str,
                 catalog: Optional[Dict[str, ItemTemplate]] = None) -> Optional[ItemTemplate]:
    if catalog is not None:
        return catalog.get(template_id)
    return _CATALOG.get(template_id)


# --------------------------------------------------------------------------- factory
def create_instance(template: ItemTemplate,
                    *,
                    owner_id: Optional[str] = None,
                    location_id: Optional[str] = None,
                    container_id: Optional[str] = None,
                    quantity: int = 1,
                    name: Optional[str] = None,
                    registry: Optional[Dict[str, ItemInstance]] = None) -> ItemInstance:
    """Create a fresh item instance from a template and register it."""
    inst = ItemInstance(
        id=f"item_{uuid.uuid4().hex}",
        template_id=template.id,
        name=name if name is not None else template.name,
        item_type=template.item_type,
        owner_id=owner_id,
        location_id=location_id,
        container_id=container_id,
        quantity=quantity,
        ammo=template.ammo_capacity,
        condition=template.default_state.get("condition", "intact"),
        state=dict(template.default_state),
        tags=list(template.tags),
    )
    register_instance(inst, registry)
    return inst


def create_world_object(d: dict) -> WorldObject:
    return WorldObject.from_dict(d)


# --------------------------------------------------------------------------- migration from v2.7.6.1
def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _match_template_by_name(catalog: Dict[str, ItemTemplate], name: str) -> Optional[ItemTemplate]:
    if not name:
        return None
    key = _slug(name)
    # exact id match first
    if key in catalog:
        return catalog[key]
    low = name.lower().strip()
    for tmpl in catalog.values():
        if tmpl.name.lower().strip() == low:
            return tmpl
    return None


def template_from_weapon(weapon: "Weapon") -> ItemTemplate:
    """Synthesize a template from a legacy Weapon object (v2.7.x)."""
    name = weapon.name or "unknown weapon"
    key = _slug(name)
    # Best-guess skill key from weapon shape.
    if weapon.is_shotgun or "rifle" in name.lower():
        skill_key = "Firearms_Rifle_Shotgun"
    elif weapon.base_range > 0:
        skill_key = "Firearms_Handgun"
    else:
        skill_key = "Fighting_Brawl"
    tmpl = ItemTemplate(
        id=key or "migrated_weapon",
        name=name,
        item_type="weapon",
        tags=["weapon", "migrated"],
        skill_key=skill_key,
        damage=weapon.damage or "1D3",
        base_range=weapon.base_range,
        rof=weapon.rof,
        ammo_capacity=weapon.ammo,
        malfunction=weapon.malfunction,
        impales=weapon.impales or None,
        is_shotgun=weapon.is_shotgun,
        is_short_barrel=weapon.is_short_barrel,
        ammo_type="shotgun" if weapon.is_shotgun else "handgun" if weapon.base_range > 0 else None,
        default_state={"condition": "intact"},
    )
    # Keep the synthesized template findable by instance_to_weapon — without
    # this, a constructor-migrated weapon silently degraded to 1D3/melee.
    _CATALOG.setdefault(tmpl.id, tmpl)
    return tmpl


def instance_from_weapon(weapon: "Weapon",
                         owner_id: Optional[str] = None,
                         location_id: Optional[str] = None,
                         registry: Optional[Dict[str, ItemInstance]] = None) -> ItemInstance:
    tmpl = template_from_weapon(weapon)
    inst = create_instance(tmpl, owner_id=owner_id, location_id=location_id, registry=registry)
    inst.ammo = weapon.ammo
    return inst


def migrate_character(char_dict: dict,
                      catalog: Dict[str, ItemTemplate],
                      instances: Dict[str, ItemInstance]):
    """Migrate one v2.7.6.1 (or older) character dict into the item registry.

    Mutates char_dict in place:
      - 'weapon' dict becomes equipped_item_id + an item instance.
      - 'weapon_instances' become additional carried item instances.
      - string inventory entries become item instances.
    """
    char_id = char_dict.get("id")
    inv = list(char_dict.get("inventory") or [])
    old_weapon = char_dict.get("weapon")
    old_instances = char_dict.get("weapon_instances") or {}

    equipped_id = None

    # Migrate the equipped weapon.
    if isinstance(old_weapon, dict):
        tmpl = _match_template_by_name(catalog, old_weapon.get("name"))
        if tmpl is None:
            from src.character import Weapon
            tmpl = template_from_weapon(Weapon(**{
                k: v for k, v in old_weapon.items()
                if k in {"name", "damage", "base_range", "rof", "ammo",
                         "malfunction", "is_shotgun", "is_short_barrel"}
            }))
        inst = create_instance(tmpl, owner_id=char_id, registry=instances)
        inst.ammo = old_weapon.get("ammo", inst.ammo)
        equipped_id = inst.id

    # Migrate any unequipped weapon instances.
    if isinstance(old_instances, dict):
        for name, wdata in old_instances.items():
            if not isinstance(wdata, dict):
                continue
            # Skip the one we already migrated as equipped.
            if equipped_id and name == old_weapon.get("name"):
                continue
            tmpl = _match_template_by_name(catalog, wdata.get("name"))
            if tmpl is None:
                from src.character import Weapon
                tmpl = template_from_weapon(Weapon(**{
                    k: v for k, v in wdata.items()
                    if k in {"name", "damage", "base_range", "rof", "ammo",
                             "malfunction", "is_shotgun", "is_short_barrel"}
                }))
            inst = create_instance(tmpl, owner_id=char_id, registry=instances)
            inst.ammo = wdata.get("ammo", inst.ammo)
            inv.append(inst.id)

    # Migrate inventory strings to instance ids. Reuse the equipped weapon
    # instance for the FIRST inventory entry that names the same thing; any
    # additional same-name entries are legitimate duplicate gear.
    equipped_name = old_weapon.get("name") if isinstance(old_weapon, dict) else None
    new_inv: List[str] = []
    used_equipped_match = False
    for entry in inv:
        if isinstance(entry, dict):
            new_inv.append(entry.get("id"))
            continue
        if not isinstance(entry, str):
            continue
        if entry.startswith("item_"):
            new_inv.append(entry)
            continue
        if entry == equipped_name and equipped_id and not used_equipped_match:
            new_inv.append(equipped_id)
            used_equipped_match = True
            continue
        tmpl = _match_template_by_name(catalog, entry)
        if tmpl is None:
            tmpl = ItemTemplate(
                id=f"migrated_{_slug(entry)}",
                name=entry,
                item_type="misc",
                tags=["migrated"],
                description=f"Migrated from v2.7.x inventory: {entry}",
            )
        inst = create_instance(tmpl, owner_id=char_id, registry=instances)
        new_inv.append(inst.id)

    if equipped_id and equipped_id not in new_inv:
        new_inv.append(equipped_id)

    # v2.8.0.1: do not overwrite an already-migrated equipped_item_id.
    if not char_dict.get("equipped_item_id"):
        char_dict["equipped_item_id"] = equipped_id
    char_dict["inventory"] = new_inv
    char_dict.pop("weapon", None)
    char_dict.pop("weapon_instances", None)


def migrate_save_data(data: dict, catalog: Dict[str, ItemTemplate]) -> dict:
    """Migrate an entire v2.7.x save dict in place.

    Creates/updates data['item_instances'] and migrates every character.
    """
    instances: Dict[str, ItemInstance] = {}
    for cid, cd in (data.get("characters") or {}).items():
        if not isinstance(cd, dict):
            continue
        migrate_character(cd, catalog, instances)
    existing = data.get("item_instances") or {}
    if isinstance(existing, dict):
        for iid, idata in existing.items():
            if isinstance(idata, dict):
                inst = ItemInstance.from_dict(idata)
                inst.id = iid
                instances[iid] = inst
    data["item_instances"] = {iid: inst.to_dict() for iid, inst in instances.items()}
    # World objects may already exist in newer saves.
    objects = data.get("world_objects") or {}
    if isinstance(objects, dict):
        data["world_objects"] = {oid: (obj if isinstance(obj, dict) else obj.to_dict())
                                 for oid, obj in objects.items()}
    else:
        data["world_objects"] = {}
    return data
