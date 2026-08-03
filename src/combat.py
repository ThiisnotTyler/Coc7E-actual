"""Combat resolution for CoC 7e.

v2.2 fixes over the reviewed draft:
- `band` was only assigned inside the firearms branch but referenced later —
  attacking unarmed/'firearms with no weapon' raised NameError. Restructured.
- Damage parser now handles signed terms: "-2", "+1D4", "1D6+1", "1D4-1".
- Melee attacks add the attacker's damage bonus, per RAW.
- Attack roll reuses DiceEngine.skill_check so fumble thresholds and
  bonus/penalty dice behave identically everywhere.

v2.8.1.x combat conversion (RAW, researched against the 7e rules):
- Melee is now OPPOSED (roll vs roll): the defender Dodges or Fights Back,
  success levels are compared by rank (Critical>Extreme>Hard>Regular>
  Failure>Fumble). Dodge wins ties; the initiator wins fight-back ties;
  both failing = nothing; a winning fight-back counter-hits the attacker
  for REGULAR damage (no extreme bonus on a fight-back).
- Extreme success on an INITIATED attack: blunt = max weapon + max DB;
  impaling = that plus one rolled weapon damage.
- 01 is always a Critical (handled in dice.py) and is the only roll that
  impales at extreme range.
- Point blank is a BONUS DIE within 1/5 DEX in feet (was: doubled damage,
  and a feet-vs-yards unit bug made the band ~3x too wide).
- Bullets impale; nothing lands past 4x base range (was: allowed at fifth).
- Firing into melee costs a penalty die; a fumble hits the lowest-Luck
  ally engaged with the target.
- Surprise: an unaware target (alerted=False) cannot defend; attacking
  alerts it. The defender's stance is engine-owned policy — never the LLM.
"""
import re
from src.character import Character
from src.spatial import SpatialEngine, POSITION_YARDS, position_yards
from src.dice import DiceEngine, LEVEL_RANK
from src import items as _items


class CombatEngine:
    def __init__(self, spatial: SpatialEngine, dice: DiceEngine):
        self.spatial = spatial
        self.dice = dice

    def calc_distance(self, a: Character, b: Character) -> float:
        """Yards between two characters (abstract in-scene positioning).

        v2.8.1.x Phase 1: the room's authored span scales the band yards
        (a gymnasium's 'far' is not a cellar's 'far'); same-band pairs
        still read as adjacent (+1)."""
        if a.location == b.location:
            loc = self.spatial.locations.get(a.location)
            span = getattr(loc, "span", "medium") if loc else "medium"
            return float(abs(position_yards(a.position, span)
                             - position_yards(b.position, span)) + 1)
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

    def max_damage(self, damage_str: str) -> int:
        """The maximum of a damage expression (extreme-success rule)."""
        total = 0
        for term in re.findall(r"[+-]?[^+-]+", damage_str.replace(" ", "")):
            sign = -1 if term.startswith("-") else 1
            term = term.lstrip("+-")
            if not term:
                continue
            m = re.fullmatch(r"(?:(\d+))?[dD](\d+)", term)
            if m:
                count = int(m.group(1)) if m.group(1) else 1
                total += sign * count * int(m.group(2))
            else:
                total += sign * int(term)
        return total

    # ------------------------------------------------------------- melee
    @staticmethod
    def defender_stance(target: Character) -> str:
        """Engine-owned defense policy: 'none' (helpless or unaware),
        'dodge', or 'fight_back' — never the narrator's call.
        v2.8.1.x Phase 2: an aware, able PLAYER'S chosen stance
        (`stance` command) overrides the skill policy; NPCs always use
        the policy."""
        if target.unconscious or target.dying or not getattr(target, "alerted", True):
            return "none"
        chosen = getattr(target, "stance", None)
        if target.char_type == "player" and chosen in ("dodge", "fight_back", "none"):
            return chosen
        brawl = target.skills.get("Fighting_Brawl", 25)
        dodge = target.skills.get("Dodge", target.DEX // 2)
        return "fight_back" if brawl >= dodge else "dodge"

    @staticmethod
    def _with_db(dmg_str: str, db: str) -> str:
        if db and db != "0":
            dmg_str += db if db.startswith("-") else f"+{db.lstrip('+')}"
        return dmg_str

    def resolve_melee(self, attacker: Character, target: Character,
                      nonlethal: bool = False, stance: str = None) -> dict:
        """CoC 7e opposed melee. When nonlethal is set, a dropping hit knocks
        the target OUT instead of leaving them dying."""
        result = {"hit": False, "damage": 0, "malfunction": False,
                  "notes": [], "attack_type": "melee"}
        if nonlethal:
            result["nonlethal"] = True
        distance = self.calc_distance(attacker, target)
        if distance == float("inf"):
            result["notes"].append("Target out of range.")
            return result
        if distance > 3:
            result["notes"].append(f"Too far ({distance:.0f}y). Must close distance.")
            return result

        weapon = attacker.weapon
        target_num = attacker.skills.get("Fighting_Brawl", 25)
        stance = stance if stance is not None else self.defender_stance(target)
        result["stance"] = stance
        if stance == "none" and not (target.unconscious or target.dying):
            if not getattr(target, "alerted", True):
                result["notes"].append(
                    f"{target.name} is caught unaware — no defense possible.")
            else:
                # an aware defender who chose 'stance none'
                result["notes"].append(f"{target.name} offers no defense.")
        roll, level = self.dice.skill_check(target_num)
        result.update({"roll": roll, "target": target_num, "level": level,
                       "skill": "Fighting_Brawl"})
        # an attack — hit or miss — burns the element of surprise
        if not getattr(target, "alerted", True):
            target.alerted = True

        def _melee_hit():
            dmg_str = self._with_db(weapon.damage if weapon else "1D3",
                                    attacker.damage_bonus)
            if level in ("Extreme", "Critical"):
                impales = weapon.impales if weapon else False
                damage = self.max_damage(dmg_str)
                if impales:
                    damage += self.roll_damage(weapon.damage)
                    result["notes"].append("Impale!")
                result["notes"].append("Extreme success — maximum damage!")
            else:
                damage = self.roll_damage(dmg_str)
            net = target.take_damage(damage)
            if nonlethal and target.dying:
                target.dying = False
                target.unconscious = True
            result["hit"] = True
            result["damage"] = net
            result["notes"].append(self._hit_note(target, net, nonlethal))

        a_rank = LEVEL_RANK[level]
        if stance == "none":
            if a_rank < LEVEL_RANK["Regular"]:
                result["notes"].append(f"Miss! ({roll} vs {target_num})")
                return result
            _melee_hit()
            return result

        # opposed: the defender rolls Dodge or Fighting (Brawl)
        if stance == "dodge":
            d_skill_name = "Dodge"
            d_target = target.skills.get("Dodge", target.DEX // 2)
        else:
            d_skill_name = "Fighting_Brawl"
            d_target = target.skills.get("Fighting_Brawl", 25)
        d_roll, d_level = self.dice.skill_check(d_target)
        result["defender_roll"] = {"roll": d_roll, "level": d_level,
                                   "skill": d_skill_name, "target": d_target,
                                   "name": target.name}
        # v2.8.1.x: the exchange is engine truth the table and the narrator
        # may both cite — the defender's roll is never invisible.
        result["notes"].append(
            f"{target.name} rolls {d_skill_name.replace('_', ' ')} "
            f"{d_target}%: {d_roll} — {d_level} "
            f"({'fights back' if stance == 'fight_back' else 'dodges'})")
        d_rank = LEVEL_RANK[d_level]
        if a_rank < LEVEL_RANK["Regular"] and d_rank < LEVEL_RANK["Regular"]:
            result["notes"].append(
                f"Both miss — {attacker.name} ({level}) and {target.name} "
                f"({d_level}) come up empty.")
            return result
        if stance == "dodge":
            if a_rank > d_rank:
                _melee_hit()
            else:
                result["notes"].append(
                    f"{target.name} dodges ({d_level} vs {level}).")
            return result
        # fight back: the initiator wins ties; a better defender roll
        # counter-hits for REGULAR damage only (RAW: no extreme bonus).
        if a_rank >= d_rank:
            _melee_hit()
            return result
        c_weapon = target.weapon
        c_dmg = self.roll_damage(self._with_db(
            c_weapon.damage if c_weapon else "1D3", target.damage_bonus))
        c_net = attacker.take_damage(c_dmg)
        result["counter"] = {"damage": c_net, "attacker": target.name,
                             "roll": d_roll, "level": d_level}
        result["notes"].append(
            f"{target.name} fights back and wins the exchange "
            f"({d_level} vs {level}) — {c_net} damage to {attacker.name}.")
        return result

    # ----------------------------------------------------------- firearms
    @staticmethod
    def _engaged_allies(attacker: Character, target: Character,
                        others) -> list:
        """Characters within melee reach of the TARGET (firing-into-melee)."""
        engaged = []
        for c in (others or []):
            if c is attacker or c is target or c.unconscious or c.dying:
                continue
            if c.location != target.location:
                continue
            engaged.append(c)
        return [c for c in engaged
                if CombatEngine._static_distance(c, target) <= 3]

    @staticmethod
    def _static_distance(a: Character, b: Character) -> float:
        # Body-scale proximity (firing into melee): base yards, deliberately
        # NOT span-scaled — room size does not change what 'adjacent' means.
        if a.location == b.location:
            return float(abs(POSITION_YARDS.get(a.position, 5)
                             - POSITION_YARDS.get(b.position, 5)) + 1)
        return float("inf")

    def resolve_attack(self, attacker: Character, target: Character,
                       attack_type: str = "firearms", nonlethal: bool = False,
                       others=None) -> dict:
        """Resolve one attack. Melee delegates to the opposed system."""
        if attack_type == "melee" or attacker.weapon is None:
            return self.resolve_melee(attacker, target, nonlethal=nonlethal)

        result = {"hit": False, "damage": 0, "malfunction": False,
                  "notes": [], "attack_type": "firearms"}
        if nonlethal:
            result["nonlethal"] = True
        distance = self.calc_distance(attacker, target)
        if distance == float("inf"):
            result["notes"].append("Target out of range.")
            return result

        weapon = attacker.weapon

        def _sync():
            if attacker.equipped_item_id:
                inst = _items.get_instance(attacker.equipped_item_id)
                if inst is not None and weapon is not None:
                    inst.ammo = weapon.ammo
                    if result.get("malfunction"):
                        inst.condition = "jammed"

        if weapon.ammo <= 0:
            result["notes"].append("Click. Empty.")
            return result
        # v2.7.3: the weapon in hand decides the skill. v2.8.1.x: that means
        # the template's authored skill_key first — a rifle is not a handgun
        # (field: the Hunting Rifle rolled Firearms Handgun 20%).
        _inst = (_items.get_instance(attacker.equipped_item_id)
                 if attacker.equipped_item_id else None)
        _tmpl = _items.get_template(_inst.template_id) if _inst else None
        skill_name = _items.firearm_skill_key(weapon, _tmpl)
        skill = attacker.skills.get(
            skill_name, 25 if skill_name == "Firearms_Rifle_Shotgun" else 20)
        band = weapon.get_range_band(distance, attacker.DEX)
        if band == "out_of_range":
            result["notes"].append(f"Too far ({distance:.0f}y) for {weapon.name}.")
            return result
        target_num = weapon.get_skill_target(skill, band)
        weapon.ammo -= 1
        _sync()

        # RAW situational dice: point blank = bonus die; firing into melee
        # = penalty die (bonus/penalty cancel 1:1 inside skill_check).
        bonus = 1 if band == "point_blank" else 0
        engaged = self._engaged_allies(attacker, target, others)
        penalty = 1 if engaged else 0
        roll, level = self.dice.skill_check(target_num, bonus=bonus,
                                            penalty=penalty)

        # v2.7.4: record the attempt BEFORE branching.
        result.update({"roll": roll, "target": target_num, "level": level,
                       "skill": skill_name})
        if roll >= weapon.malfunction:
            result["malfunction"] = True
            result["notes"].append(f"Weapon malfunction! ({weapon.name} jams on {roll})")
            _sync()
            return result

        if level in ("Fumble", "Failure"):
            # RAW: a fumbled shot into a melee hits an ally instead — the
            # one with the lowest Luck.
            if level == "Fumble" and engaged:
                ally = min(engaged, key=lambda c: c.luck)
                a_dmg = ally.take_damage(self.roll_damage(weapon.damage))
                result["notes"].append(
                    f"Fumble into the melee — the shot hits {ally.name} "
                    f"instead! {a_dmg} damage.")
                result["hit_ally"] = {"id": ally.id, "name": ally.name,
                                      "damage": a_dmg}
                return result
            result["notes"].append(f"Miss! ({roll} vs {target_num})")
            return result

        # --- damage ---
        # Bullets/blades impale on an Extreme success; at extreme range only
        # a Critical (01) impales. Impale = max weapon damage + one roll.
        if level in ("Extreme", "Critical") and weapon.impales \
                and (band != "extreme" or level == "Critical"):
            damage = self.max_damage(weapon.damage) \
                + self.roll_damage(weapon.damage)
            result["notes"].append("Impale!")
        else:
            damage = self.roll_damage(weapon.damage)

        net = target.take_damage(damage)
        if nonlethal and target.dying:
            target.dying = False
            target.unconscious = True
        result["hit"] = True
        result["damage"] = net
        result["notes"].append(self._hit_note(target, net, nonlethal))
        return result

    @staticmethod
    def _hit_note(target: Character, net: int, nonlethal: bool) -> str:
        if target.dying:
            return f"Hit! {net} damage. {target.name} is DYING."
        if target.unconscious:
            how = "is knocked out" if nonlethal else "falls unconscious"
            return f"Hit! {net} damage. {target.name} {how}."
        if target.major_wound:
            return f"Hit! {net} damage. {target.name} suffers a major wound."
        return f"Hit! {net} damage. {target.name} is hurt."
