"""Combat resolution for CoC 7e.

v2.2 fixes over the reviewed draft:
- `band` was only assigned inside the firearms branch but referenced later —
  attacking unarmed/'firearms with no weapon' raised NameError. Restructured.
- Damage parser now handles signed terms: "-2", "+1D4", "1D6+1", "1D4-1".
  (Build -2 investigators have damage bonus "-2"; the old parser crashed on it.)
- Melee attacks add the attacker's damage bonus, per RAW.
- Attack roll reuses DiceEngine.skill_check so fumble thresholds and
  bonus/penalty dice behave identically everywhere.
"""
import re
from src.character import Character
from src.spatial import SpatialEngine
from src.dice import DiceEngine
from src import items as _items


class CombatEngine:
    def __init__(self, spatial: SpatialEngine, dice: DiceEngine):
        self.spatial = spatial
        self.dice = dice

    def calc_distance(self, a: Character, b: Character) -> float:
        """Yards between two characters (abstract in-scene positioning)."""
        if a.location == b.location:
            dists = {"close": 2, "near": 5, "far": 10, "elevated": 8, "behind_cover": 5}
            return float(abs(dists.get(a.position, 5) - dists.get(b.position, 5)) + 1)
        d, _ = self.spatial.get_distance(a.location, b.location)
        return d * 10 if d != float("inf") else float("inf")

    def roll_damage(self, damage_str: str) -> int:
        """Parse '1D6', '1D4+2', '+1D4', '-2', '1D10+1D4+1' etc."""
        total = 0
        for term in re.findall(r"[+-]?[^+-]+", damage_str.replace(" ", "")):
            sign = -1 if term.startswith("-") else 1
            term = term.lstrip("+-")
            if not term:
                continue
            m = re.fullmatch(r"(?:(\d+))?[dD](\d+)", term)
            if m:
                count = int(m.group(1)) if m.group(1) else 1
                total += sign * self.dice.d(int(m.group(2)), count)
            else:
                total += sign * int(term)
        return total

    def resolve_attack(self, attacker: Character, target: Character,
                       attack_type: str = "firearms", nonlethal: bool = False) -> dict:
        """Resolve one attack. When nonlethal is set (buttstock, knockout
        attempts), a dropping hit knocks the target OUT instead of leaving
        them dying — the engine, not the narrator, decides."""
        result = {"hit": False, "damage": 0, "malfunction": False, "notes": []}
        if nonlethal:
            result["nonlethal"] = True
        distance = self.calc_distance(attacker, target)
        if distance == float("inf"):
            result["notes"].append("Target out of range.")
            return result

        weapon = attacker.weapon
        band = None

        # Resolve against the transient Weapon view, then persist ammo/condition
        # back into the canonical ItemInstance.
        def _sync():
            if attacker.equipped_item_id:
                inst = _items.get_instance(attacker.equipped_item_id)
                if inst is not None and weapon is not None:
                    inst.ammo = weapon.ammo
                    if result.get("malfunction"):
                        inst.condition = "jammed"

        if attack_type == "firearms" and weapon is not None:
            if weapon.ammo <= 0:
                result["notes"].append("Click. Empty.")
                return result
            # v2.7.3: the weapon in hand decides the skill — a shotgun rolls
            # Firearms_Rifle_Shotgun (7e base 25), a handgun Firearms_Handgun
            # (base 20). Every firearm used to roll off Handgun, so an
            # untrained-handgun shotgunner fired their 12-gauge at the 20%
            # base no matter what their sheet said.
            skill_name = ("Firearms_Rifle_Shotgun" if weapon.is_shotgun
                          else "Firearms_Handgun")
            skill = attacker.skills.get(
                skill_name, 25 if weapon.is_shotgun else 20)
            band = weapon.get_range_band(distance, attacker.DEX)
            if band == "out_of_range":
                result["notes"].append(f"Too far ({distance:.0f}y) for {weapon.name}.")
                return result
            target_num = weapon.get_skill_target(skill, band)
            weapon.ammo -= 1
            _sync()
        else:
            # melee (or improvised): full Fighting (Brawl) at arm's reach
            if distance > 3:
                result["notes"].append(f"Too far ({distance:.0f}y). Must close distance.")
                return result
            target_num = attacker.skills.get("Fighting_Brawl", 25)

        roll, level = self.dice.skill_check(target_num)

        # v2.7.4: record the attempt BEFORE branching — a jam must not hide
        # what was rolled from the table or the DICE RESULTS block.
        result.update({"roll": roll, "target": target_num, "level": level})
        if attack_type == "firearms" and weapon is not None and roll >= weapon.malfunction:
            result["malfunction"] = True
            result["notes"].append(f"Weapon malfunction! ({weapon.name} jams on {roll})")
            _sync()
            return result

        if level in ("Fumble", "Failure"):
            result["notes"].append(f"Miss! ({roll} vs {target_num})")
            return result

        # --- damage ---
        if attack_type == "melee" or weapon is None:
            dmg_str = (weapon.damage if weapon else "1D3")
            db = attacker.damage_bonus
            if db and db != "0":
                dmg_str += db if db.startswith("-") else f"+{db.lstrip('+')}"
        else:
            dmg_str = weapon.damage
        damage = self.roll_damage(dmg_str)

        if attack_type == "firearms" and weapon is not None and band == "point_blank":
            damage += self.roll_damage(weapon.damage)  # point-blank impale
            result["notes"].append("Point blank - impale damage!")

        net = target.take_damage(damage)
        # Nonlethal truth: a dropping hit knocks out, never leaves them dying.
        if nonlethal and target.dying:
            target.dying = False
            target.unconscious = True
        result["hit"] = True
        result["damage"] = net
        if target.dying:
            result["notes"].append(f"Hit! {net} damage. {target.name} is DYING.")
        elif target.unconscious:
            how = "is knocked out" if nonlethal else "falls unconscious"
            result["notes"].append(f"Hit! {net} damage. {target.name} {how}.")
        elif target.major_wound:
            result["notes"].append(f"Hit! {net} damage. {target.name} suffers a major wound.")
        else:
            result["notes"].append(f"Hit! {net} damage. {target.name} is hurt.")
        return result
