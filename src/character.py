"""Character and Weapon models for CoC 7e.

v2.8.0:
- Replaced the legacy `weapon` field with `equipped_item_id` pointing into the
  campaign item registry.  `Character.weapon` remains a transient Weapon view
  built from the equipped ItemInstance for combat compatibility.
- `inventory` is now a list of item instance IDs, not display names.
- v2.7.x saves still load: the migration helper converts legacy `weapon` and
  string inventories into item instances on first load.
"""
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional

from src import items as _items


@dataclass
class Weapon:
    name: str
    damage: str
    base_range: int          # yards; 0 = melee only
    rof: int = 1
    ammo: int = 6
    malfunction: int = 96
    is_shotgun: bool = False
    is_short_barrel: bool = False
    impales: bool = False    # blades and BULLETS impale on Extreme/Critical
    # v2.8.1.x: the template's authored firearm skill (e.g. a rifle's
    # Firearms_Rifle_Shotgun). None -> shape-based fallback in
    # items.firearm_skill_key (the weapon in hand decides, v2.7.3).
    skill_key: "Optional[str]" = None

    def get_range_band(self, distance: float, dex: int) -> str:
        # RAW: point blank is within 1/5 DEX in FEET — the engine's distances
        # are yards, so convert (was: feet-compared-to-yards, ~3x too wide).
        point_blank = (dex / 5) / 3.0
        if distance <= point_blank:
            return "point_blank"
        if self.base_range <= 0:
            return "out_of_range"
        if distance <= self.base_range:
            return "regular"
        if distance <= self.base_range * 2:
            return "long"
        if distance <= self.base_range * 4:
            return "extreme"
        return "out_of_range"   # RAW: nothing lands past 4x base range

    def get_skill_target(self, base_skill: int, band: str) -> int:
        # CoC 7e: regular range = full skill, long = half (Hard), extreme = fifth (Extreme)
        if band in ("point_blank", "regular"):
            return base_skill
        if band == "long":
            return base_skill // 2
        return base_skill // 5


@dataclass
class Character:
    id: str
    name: str
    char_type: str                       # "player" | "npc"
    owner: Optional[str] = None
    STR: int = 50
    CON: int = 50
    SIZ: int = 50
    DEX: int = 50
    APP: int = 50
    INT: int = 50
    POW: int = 50
    EDU: int = 50
    hp: Optional[int] = None             # None -> derive (max_hp)
    max_hp: Optional[int] = None         # None -> derive (CON+SIZ)/10
    san: Optional[int] = None            # None -> derive (POW)
    max_san: Optional[int] = None        # None -> derive 99 - mythos
    mp: Optional[int] = None             # None -> derive POW/5
    luck: int = 50
    build: int = 0
    damage_bonus: str = "0"
    move: int = 8
    skills: Dict[str, int] = field(default_factory=dict)
    checked_skills: List[str] = field(default_factory=list)
    major_wound: bool = False
    dying: bool = False
    unconscious: bool = False
    temporarily_insane: bool = False
    indefinitely_insane: bool = False
    cthulhu_mythos: int = 0
    san_loss_today: int = 0
    # v2.8.0: canonical equipped item is referenced by instance id.  The
    # `weapon` field below is a transient Weapon view kept for combat
    # compatibility and is rebuilt from the instance whenever it changes.
    equipped_item_id: Optional[str] = None
    weapon: Optional[Weapon] = None
    armor: int = 0
    scars: List[str] = field(default_factory=list)
    phobias: List[str] = field(default_factory=list)
    manias: List[str] = field(default_factory=list)
    key_items: List[str] = field(default_factory=list)
    # v2.8.0: list of item instance IDs.  Managed by engine meta-commands.
    inventory: List[str] = field(default_factory=list)
    location: str = "unknown"
    position: str = "close"              # close/near/far/elevated/behind_cover
    alerted: bool = True                 # False = unaware; surprise defenseless
    declared_action: str = ""
    personal_log: List[str] = field(default_factory=list)
    extra: Dict = field(default_factory=dict)   # attitude, notes, anything scenario-specific

    def __post_init__(self):
        if self.max_hp is None:
            self.max_hp = (self.CON + self.SIZ) // 10
        if self.hp is None:
            self.hp = self.max_hp
        if self.san is None:
            self.san = self.POW
        if self.max_san is None:
            self.max_san = 99 - self.cthulhu_mythos
        if self.mp is None:
            self.mp = self.POW // 5

        str_siz = self.STR + self.SIZ
        if str_siz <= 64:
            self.build, self.damage_bonus = -2, "-2"
        elif str_siz <= 84:
            self.build, self.damage_bonus = -1, "-1"
        elif str_siz <= 124:
            self.build, self.damage_bonus = 0, "0"
        elif str_siz <= 164:
            self.build, self.damage_bonus = 1, "+1D4"
        else:
            self.build, self.damage_bonus = 2, "+1D6"

        if self.DEX < self.SIZ and self.STR < self.SIZ:
            self.move = 7
        elif self.DEX > self.SIZ and self.STR > self.SIZ:
            self.move = 9
        else:
            self.move = 8

        # Ensure inventory is a list (migration safety).
        if not isinstance(self.inventory, list):
            self.inventory = []

        # Migrate a legacy Weapon passed at construction into a real item instance.
        if not self.equipped_item_id and self.weapon:
            inst = _items.instance_from_weapon(self.weapon, owner_id=self.id)
            self.equipped_item_id = inst.id
            self.weapon = _items.instance_to_weapon(inst)
            if inst.id not in self.inventory:
                self.inventory.append(inst.id)

        # Sync the transient Weapon view from the equipped instance.
        if self.equipped_item_id:
            inst = _items.get_instance(self.equipped_item_id)
            if inst is not None:
                self.weapon = _items.instance_to_weapon(inst)
                if self.equipped_item_id not in self.inventory:
                    self.inventory.append(self.equipped_item_id)

        # Ensure the equipped item is always listed in inventory.
        if self.equipped_item_id and self.equipped_item_id not in self.inventory:
            self.inventory = list(self.inventory) + [self.equipped_item_id]

    # ------------------------------------------------------------------ sync
    def refresh_weapon_view(self):
        """Rebuild the transient Weapon view from the equipped item instance."""
        if self.equipped_item_id:
            inst = _items.get_instance(self.equipped_item_id)
            self.weapon = _items.instance_to_weapon(inst) if inst else None
        else:
            self.weapon = None

    def sync_weapon_to_instance(self):
        """Write the transient Weapon's ammo/condition back to the instance."""
        if self.equipped_item_id and self.weapon:
            inst = _items.get_instance(self.equipped_item_id)
            if inst is not None:
                inst.ammo = self.weapon.ammo

    # ---- status -----------------------------------------------------------
    def get_condition(self) -> str:
        if self.dying:
            return "dying"
        if self.unconscious:
            return "unconscious"
        if self.major_wound:
            return "major_wound"
        if self.hp is not None and self.hp <= self.max_hp // 2:
            return "wounded"
        return "healthy"

    def take_damage(self, damage: int):
        net = max(0, damage - self.armor)
        if net >= self.max_hp // 2:
            self.major_wound = True
        self.hp = max(0, self.hp - net)
        if self.hp == 0:
            if self.major_wound:
                self.dying = True
            else:
                self.unconscious = True
        return net

    # ---- prompt serialization ---------------------------------------------
    def to_active_format(self) -> dict:
        weapon_name = None
        if self.equipped_item_id:
            inst = _items.get_instance(self.equipped_item_id)
            if inst is not None:
                weapon_name = inst.name
        if weapon_name is None and self.weapon:
            weapon_name = self.weapon.name

        inv_names = []
        for iid in self.inventory:
            inst = _items.get_instance(iid)
            inv_names.append(inst.name if inst is not None else iid)

        return {
            "id": self.id, "name": self.name, "type": self.char_type,
            "hp": self.hp, "max_hp": self.max_hp, "san": self.san,
            "condition": self.get_condition(),
            "key_skills": dict(list(self.skills.items())[:5]),
            "weapon": weapon_name,
            "inventory": inv_names,
            "position": self.position,
            "action": self.declared_action,
        }

    def to_summary_format(self) -> dict:
        return {
            "id": self.id, "name": self.name,
            "condition": self.get_condition(),
            "location": self.location,
            "status": self.declared_action or "Idle",
        }

    # ---- persistence --------------------------------------------------------
    def to_dict(self) -> dict:
        # v2.8.0.1: `weapon` is a transient view rebuilt from equipped_item_id.
        # Serializing it would cause migration to create a duplicate instance on
        # every load.  Exclude it from the save format.
        excluded = {"weapon"}
        return {k: v for k, v in asdict(self).items() if k not in excluded}

    @classmethod
    def from_dict(cls, d: dict) -> "Character":
        d = dict(d)
        w = d.get("weapon")
        if isinstance(w, dict):
            known = set(Weapon.__dataclass_fields__)
            d["weapon"] = Weapon(**{k: v for k, v in w.items() if k in known})
        # Keep equipped_item_id and inventory ids as-is; migration happens in
        # items.migrate_save_data before this is called.
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in d.items() if k in known})
