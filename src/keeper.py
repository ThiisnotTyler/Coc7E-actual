"""CoCKeeper — orchestrates one turn: declarations -> local mechanics -> LLM
narration -> state delta -> persistence.

v2.2 fixes over the reviewed draft:
- load_scenario() no longer crashes on the guide's own scenario.json.
  NPC entries nest stats under "characteristics", weapons arrive as dicts,
  and unknown keys like "attitude" used to go straight into Character(**kw)
  -> TypeError. Now flattened properly.
- The LLM's scene_transitions are actually applied (characters move,
  occupants update, current_scene follows the players).
- Combat is mechanical: 'shoot/stab <name>' resolves through CombatEngine
  and the result is handed to the LLM as DICE RESULTS.
- Full save/load via state.py (resume protocol works).
- --mock mode: runs the whole loop offline through MockKeeperClient.

v2.8.0:
- Loads the global item catalog and per-scenario item/object overrides.
- Replaces the v2.7.6.1 weapon-instance bridge with the item registry.
- Adds item/world-object meta-commands and an in-game `help`/`list` command.
"""
import copy
import json
import os
import re
import time
from typing import Dict, List, Optional

from src.character import Character, Weapon
from src.spatial import SpatialEngine, Location
from src.dice import DiceEngine
from src.combat import CombatEngine
from src.sanity import SanityEngine
from src.mode import ModeSelector, ResolutionMode
from src.chronicle import build_chronicle
from src.charcreate import skill_base
from src import items as items_mod
from src import state as state_mod
from src import room_view
from src import local_voice
from src import narration_validator
from src import commands
from src import prompt_builder
from src import persistence
# Re-exported so existing imports (`from src.keeper import
# NARRATION_RULES_PACKET` in tests and prompts) keep working after the
# narration_validator split.
from src.narration_validator import (  # noqa: F401
    NARRATION_RULES_PACKET,
    NARRATION_RULES_SYSTEM,
)
from src.latency import LatencyCollector
from src.state_validator import (
    ENGINE_OWNED_CHARACTER_FIELDS,
    StateDeltaValidator,
)
from src.human_keeper import (
    HumanKeeperCancelled,
    _verdict_line,
    build_human_keeper_packet,
)
from src.latency_governor import GovernorDegraded, LatencyGovernor


# v2.7.1: the preroll net. Every risky declaration must meet the dice BEFORE
# the LLM sees the turn — the field log showed 'sneak up and climb onto the
# balcony' reaching the model with zero dice, so the model fiat-ed the
# outcome (the 'aura farming' bug). Ordered: first match wins, so existing
# search/listen/combat behavior is untouched. Targets come from the
# character's skills, falling back to the 7e base values in charcreate.
PREROLL_SKILLS = [
    (re.compile(r"\b(sneak|sneaking|hide|hides|hiding|stealth|creep|creeping|prowl|lurk)\b"), "Stealth"),
    (re.compile(r"\b(climb|climbing|scale|scaling|clamber)\b"), "Climb"),
    (re.compile(r"\b(jump|jumping|leap|leaping|vault)\b"), "Jump"),
    (re.compile(r"\b(swim|swimming)\b"), "Swim"),
    (re.compile(r"\b(throw|throwing|hurl|toss)\b"), "Throw"),
    (re.compile(r"\b(jimmy|lockpick|locksmith|pry|prying|prize)\b|pick\s+(the|a|that)\s+(lock|padlock)"), "Locksmith"),
    (re.compile(r"\b(pickpocket|sleight|palm\s+the)\b"), "Sleight_of_Hand"),
    (re.compile(r"\b(disguise)\b"), "Disguise"),
    (re.compile(r"\b(dodge|duck|evade)\b"), "Dodge"),
    (re.compile(r"\b(intimidate|threaten|menace|bully)\b"), "Intimidate"),
    # v2.8.1.1: social coercion meets Intimidate before narration — demands,
    # orders, warnings, and gunpoint commands are rolls, not prose.
    (re.compile(r"\b(demand|demands|demanding|order|orders|command|commands|"
                r"warn|warns|warning)\b|at\s+gunpoint|"
                r"tell\s+\w+\s+to\s+stop|make\s+\w+\s+stop|"
                r"force\s+\w+\s+to\s"), "Intimidate"),
    (re.compile(r"\b(charm|seduce|flirt)\b"), "Charm"),
    (re.compile(r"\b(bluff|fast.?talk|lie\s+to|con\s+the)\b"), "Fast_Talk"),
    (re.compile(r"\b(persuade|convince|plead|beg|negotiate|bargain)\b"), "Persuade"),
    (re.compile(r"\b(bandage|first\s+aid|patch\s+(him|her|them|up)|stanch|staunch)\b"), "First_Aid"),
    (re.compile(r"\b(track|tracking|trail|follow\s+the\s+tracks)\b"), "Track"),
    (re.compile(r"\b(drive|driving)\b"), "Drive_Auto"),
    (re.compile(r"\b(research|look\s+up|read\s+through)\b"), "Library_Use"),
]

# Answering a pending dice request: 'roll!', 'yes', 'go ahead', ...
ROLL_AFFIRM = re.compile(
    r"\b(roll|yes|yeah|yep|ok|okay|sure|go\s+ahead|do\s+it|"
    r"pull\s+the\s+trigger)\b", re.I)

# Leading articles are noise in command arguments ('take the key').
_ARTICLE = re.compile(r"^(?:the|a|an)\s+")

# v2.7.2: the commitment net. Field log: 'attempt to breach the door with
# the shotgun' got TWO turns of setup and zero dice — these verbs matched
# nothing, and shooting a door/lock matched no CHARACTER target, so the
# engine stayed silent while the model edged. Combat verbs widened, and
# attacks on inanimate objects roll too (firearm skill if a gun is in hand
# and the phrasing is ballistic, else raw STR).
# v2.7.5: verbs split by intent. ATTACK verbs harm people — a bare one with
# no named target still falls back to the nearest NPC ('shoot' mid-fight).
# FORCE verbs break THINGS and only roll with an object in the phrase —
# 'break the news to her' must never become an assault (field log: 'kick
# the door in' matched nothing because the net only knew 'kick down').
ATTACK_VERB_RE = re.compile(
    r"\b(shoot|fire|stab|attack|punch|swing|blast|breach|aim|smash)\b")
# v2.8.1.1 hotfix: target-aware melee and improvised attacks. These route to
# the combat engine (Fighting_Brawl) when a person is the target — declared
# intent meets dice BEFORE the model narrates anything.
MELEE_VERB_RE = re.compile(
    r"\b(hit|hits|hitting|strike|strikes|striking|tackle|tackles|tackling|"
    r"slam|slams|slamming)\b")
# Declared nonlethal intent: a dropping hit knocks out instead of killing.
NONLETHAL_RE = re.compile(
    r"\b(knock\s+out|knock\s+unconscious|knock\s+\w+\s+out|incapacitate|"
    r"buttstock|pistol[- ]?whip)\b")
FORCE_VERB_RE = re.compile(r"\b(kick|break|ram|shoulder|barge|burst|force)\b")
OBJECT_RE = re.compile(
    r"\b(door|lock|latch|window|hatch|padlock|deadbolt|barricade|chain|"
    r"chains|handcuffs|shackles|bulkhead|gate)\b")
GUN_CUES = ("shoot", "fire", "blast", "aim", "breach", "plug", "unload",
            "shotgun", "revolver", "pistol", "trigger")
# v2.8.1.1: idioms that must never become assaults ('hit the road').
NON_COMBAT_PHRASES = (
    "hit the road", "hit the hay", "hit the sack", "hit the books",
    "hit it off", "knock on wood", "hit the nail", "strike up",
    "strike a match", "strike a chord",
)


# v2.8.1.x: the narration-validator lexicons (WORLD_CHANGE_VERBS,
# FIRST_VISIT_RES, SCENARIO_FACT_RES, KEY_DENY_RE, DOOR_STILL_LOCKED_RE,
# NARRATION_NEG_RE, NARRATION_RULES_*, INTERACTABLE_NOUNS,
# _MECHANICS_QUOTE_RE) live in src/narration_validator.py — the rules and
# their enforcer travel together. NARRATION_RULES_PACKET/SYSTEM are
# re-exported from src.keeper via the import at the top of this file.


class CoCKeeper:
    def __init__(self, config: dict, mock: bool = False):
        self.config = config
        self.mock = mock
        from src.llm_client import build_llm_client
        self.gemini = build_llm_client(config, mock=mock)
        with open("config/system-prompt.txt", encoding="utf-8") as f:
            self.system_prompt = f.read()

        self.dice = DiceEngine()
        self.mode_selector = ModeSelector()
        self.characters: Dict[str, Character] = {}
        self.locations: Dict[str, Location] = {}
        self.spatial = SpatialEngine(self.locations)
        self.combat = CombatEngine(self.spatial, self.dice)
        self.sanity = SanityEngine(self.dice, self.combat, config.get("sanity", {}))
        self.chronicle = build_chronicle(config)

        # v2.7.6.1: every model-produced state delta passes through the Truth
        # Firewall before it can touch campaign state.
        self.state_validator = StateDeltaValidator()

        # v2.8.0: canonical item/object registries.
        self.item_templates: Dict[str, items_mod.ItemTemplate] = items_mod.load_catalog()
        # Preserve any item instances created before the keeper existed (e.g.
        # pregens built in default_investigators()), then direct all future
        # instance creation into this campaign's registry.
        self.item_instances: Dict[str, items_mod.ItemInstance] = dict(items_mod.get_runtime_registry())
        self.world_objects: Dict[str, items_mod.WorldObject] = {}
        items_mod.set_runtime_registry(self.item_instances)

        self.turn = 0
        self.current_scene = ""
        self.fronts: Dict[str, dict] = {}
        self.plot_points: List[str] = []
        self.clues: List[dict] = []
        self.timeline: List[dict] = []
        self.pending_rolls: List[dict] = []   # rolls the LLM asked for (v2.7.1)
        self.scenario_id = "the-haunting"
        self.max_active = config.get("game", {}).get("max_active_per_scene", 4)

        # v2.8.1 Room Truth: per-character first-visit memory, engine-owned
        # clue-reveal stamps, and this turn's engine-resolved moves (the LLM
        # narrates escalated entries; it never gets to move anyone itself).
        # v2.8.1.1: visit COUNTS feed the prompt so the model knows whether
        # the acting character has personally seen this room before.
        self.visited: Dict[str, set] = {}
        self.visit_counts: Dict[str, Dict[str, int]] = {}
        self.discovered_clues: set = set()
        self._movement_events: List[dict] = []
        self._engine_moved: Dict[str, str] = {}
        # v2.8.1.x: items that landed in a room via a resolved throw this
        # turn — the placement facts narration may not contradict.
        self._landed_items: List[dict] = []
        # v2.8.1.x: alert states at take_turn start — an NPC that flips
        # unaware -> alert during resolution was 'affected' this turn.
        self._alerted_at_turn_start: Dict[str, bool] = {}

        # v2.8.0: turn-level latency instrumentation (debug mode only).
        self.debug = bool(config.get("llm", {}).get("debug", False))
        self.latency = LatencyCollector() if self.debug else None

        # v2.8.1.2: the adjudication layer. The engine — not the player —
        # decides when a declaration needs dice. Explicit 'roll X' stays an
        # override, never a requirement.
        from src.adjudicator import Adjudicator
        from src.action_resolver import ActionResolver
        self.adjudicator = Adjudicator.load()
        self.action_resolver = ActionResolver()

        # v2.8.1.6: the Latency Governor. Every non-mock, non-human LLM call
        # is shaped by it — prompt tier/cap, model tier, budget, deadline,
        # compact retry, degraded fallback. It never touches gameplay.
        self.governor = LatencyGovernor(config)
        self._quit_requested = False
        self.scenario_title = ""
        self.scenario_tone = ""

    # ------------------------------------------------------------------ setup
    # -------- v2.8.1.x persistence delegates (src/persistence.py)
    # Scenario loading, save/load, and the registry invariant live in their
    # own module (god-file split). save_path stays a @property — main.py
    # and the save code use it attribute-style.
    @property
    def save_path(self) -> str:
        return persistence.save_path(self)

    def load_scenario(self, scenario_path: str):
        return persistence.load_scenario(self, scenario_path)

    def _registry_audit(self, char: Character, after: str = "") -> bool:
        return persistence._registry_audit(self, char, after)

    def _reconcile_inventory(self, char: Character):
        return persistence._reconcile_inventory(self, char)

    def save_state(self):
        return persistence.save_state(self)

    def load_state(self) -> bool:
        return persistence.load_state(self)

    def _register(self, char: Character):
        self.characters[char.id] = char
        if char.location in self.locations:
            self.locations[char.location].occupants.add(char.id)

    def add_player(self, char: Character):
        char.char_type = "player"
        self._reconcile_inventory(char)
        self._register(char)

# ------------------------------------------------------- v2.8.1 room truth
    def mark_visited(self, char_id: str, loc_id: str):
        self.visited.setdefault(char_id, set()).add(loc_id)
        counts = self.visit_counts.setdefault(char_id, {})
        counts[loc_id] = counts.get(loc_id, 0) + 1

    # ---------------- v2.8.1.x command-interpreter delegates (src/commands.py)
    # The command layer lives in its own module (god-file split; the seam a
    # future UI talks to). These one-line delegates keep run_session,
    # take_turn, the movement helpers, action_resolver, and the ~70
    # _meta_command test references working unchanged.
    def _meta_command(self, char: Character, text: str) -> bool:
        return commands._meta_command(self, char, text)

    def _cmd_observe(self, char: Character):
        return commands._cmd_observe(self, char)

    def _exit_list(self, char: Character) -> str:
        return commands._exit_list(self, char)

    def _find_room_object(self, char: Character, arg: str):
        return commands._find_room_object(self, char, arg)

    def _store_menu(self, char: Character, kind: str, ids: list, **extra):
        return commands._store_menu(self, char, kind, ids, **extra)

    def _print_numbered(self, names: list, hint: str):
        return commands._print_numbered(self, names, hint)

    def _clear_pending_menus(self):
        return commands._clear_pending_menus(self)

    def _update_scene_after_move(self):
        """current_scene follows the players when they are together."""
        players = [c for c in self.characters.values() if c.char_type == "player"]
        if players and all(p.location == players[0].location for p in players):
            self.current_scene = players[0].location

    def _handle_move_result(self, char: Character, result: dict, action: str = "") -> bool:
        """Shared outcome for an engine-resolved move (v2.8.1.2).

        Prints the result, and when the destination escalates, stages the
        movement packet so the LLM narrates the COMPLETED crossing.
        Returns True when the destination escalated."""
        if not result.get("moved"):
            print(f"  [{result['error']}]")
            print(f"  [Exits from here: {self._exit_list(char)}]")
            return False
        dest_name = self.locations[result["dest"]].name
        if result.get("unlocked"):
            print(f"  [{char.name} unlocks the way with the {result['unlocked']}.]")
        self._update_scene_after_move()
        # Surprise (combat conversion): unaware NPCs in the destination are
        # defenseless until the round ends — say so the moment you walk in.
        for c in self.characters.values():
            if c.char_type == "npc" and not c.alerted \
                    and c.location == result["dest"]:
                print(f"  [You catch {c.name} unaware — you have the drop "
                      f"on them.]")
        if result.get("triggers"):
            print(f"  [{char.name} -> {dest_name} — the moment calls for the Keeper.]")
            self._engine_moved[char.id] = result["dest"]
            self._movement_events.append(self._movement_packet(char, result))
            return True
        print(f"  [{char.name} -> {dest_name}]")
        view = room_view.build_room_view(self, char, first=result["first"])
        print(room_view.render_room_text(view))
        return False

    def _alert_check(self):
        """Unalerted NPCs become alert once they share a room with a player
        (checked at the end of each round). Being ATTACKED also alerts —
        handled inside combat resolution.

        v2.8.1.x field fix: the entry round is always a FULL round of
        surprise. An NPC alerts only at the end of a round that BEGAN with
        a player already in its room — run_session snapshots player rooms
        at round start; entering mid-round never alerts before the player's
        first action."""
        player_rooms = set(getattr(self, "_round_start_player_rooms", {})
                           .values())
        if not player_rooms:
            # no snapshot (direct call outside a session round): current rooms
            player_rooms = {c.location for c in self.characters.values()
                            if c.char_type == "player"
                            and not c.dying and not c.unconscious}
        for c in self.characters.values():
            if c.char_type == "npc" and not c.alerted \
                    and c.location in player_rooms:
                c.alerted = True
                print(f"[{c.name} is now alert to your presence.]")

    def _resolve_local_movement(self, declarations: Dict[str, str]) -> Dict[str, str]:
        """v2.8.1 offline movement. Pure movement declarations are resolved by
        the engine: graph-validated, exit-state-aware, no LLM. Entries with
        escalation triggers (NPC, hazard, clue reveal, ...) stay in the turn —
        the engine has already decided what happened; the LLM only narrates.
        Returns the declarations that still need the Keeper."""
        remaining = {}
        for cid, action in declarations.items():
            char = self.characters.get(cid)
            if char is None:
                continue
            verdict = room_view.match_movement(self, char, action)
            if verdict is None:
                remaining[cid] = action
                continue
            if "error" in verdict:
                print(f"  [{verdict['error']}]")
                print(f"  [Exits from here: {self._exit_list(char)}]")
                continue
            result = room_view.try_local_move(self, char, verdict["dest"])
            if self._handle_move_result(char, result, action):
                remaining[cid] = action
        return remaining

    # ----------------------------------------------------------------- turns
    def _skill_target(self, char: Character, skill: str) -> int:
        """The character's skill value, else the 7e base from charcreate."""
        if skill in char.skills:
            return char.skills[skill]
        return skill_base(skill, {"DEX": char.DEX, "EDU": char.EDU})

    def _preroll(self, char: Character, action: str) -> Optional[dict]:
        a = action.lower()
        if any(w in a for w in ["search", "spot", "look for", "examine", "inspect"]):
            target = self._skill_target(char, "Spot_Hidden")
            roll, level = self.dice.skill_check(target)
            return {"skill": "Spot_Hidden", "roll": roll, "target": target, "level": level}
        if "listen" in a:
            target = self._skill_target(char, "Listen")
            roll, level = self.dice.skill_check(target)
            return {"skill": "Listen", "roll": roll, "target": target, "level": level}
        is_attack = bool(ATTACK_VERB_RE.search(a) or MELEE_VERB_RE.search(a)
                         or NONLETHAL_RE.search(a))
        if is_attack or FORCE_VERB_RE.search(a):
            # v2.8.1.1: idioms are not assaults ('hit the road', 'strike up').
            if any(p in a for p in NON_COMBAT_PHRASES):
                return None
            named = self._named_npc(a)
            obj = OBJECT_RE.search(a)
            # A person is the target when one is named, or when an ATTACK
            # verb has no object phrase to claim (bare 'shoot' mid-fight).
            if named or (is_attack and not obj):
                target_char = self._find_target(char, a)
                if target_char is None:
                    return {"skill": "Combat", "note": "No reachable target named in that action."}
                attack_type = ("firearms" if ("shoot" in a or "fire at" in a)
                               and char.weapon else "melee")
                nonlethal = bool(NONLETHAL_RE.search(a))
                res = self.combat.resolve_attack(
                    char, target_char, attack_type, nonlethal=nonlethal,
                    others=list(self.characters.values()))
                res["skill"] = ("Firearms_Rifle_Shotgun"
                                if (attack_type == "firearms" and char.weapon
                                    and char.weapon.is_shotgun)
                                else "Firearms_Handgun" if attack_type == "firearms"
                                else "Fighting_Brawl")
                res["target_char"] = target_char.id
                return res
            if obj:
                # Inanimate target: blast the lock, kick the door in.
                # v2.8.1.1: the engine consumes the shell, checks the jam, and
                # breaks the object/exit — the model only narrates the result.
                # v2.8.1.2: the mechanics live in roll_object_attack, shared
                # with the adjudication resolver.
                if any(cue in a for cue in GUN_CUES):
                    return self.roll_object_attack(char, obj.group(0))
                return self.roll_object_attack(char, obj.group(0),
                                               force_skill="STR")
            return None   # force verb with nothing to force ('break the news')
        # v2.7.1: the wide net — every other risky declaration meets the dice
        for pattern, skill in PREROLL_SKILLS:
            if pattern.search(a):
                target = self._skill_target(char, skill)
                roll, level = self.dice.skill_check(target)
                return {"skill": skill, "roll": roll, "target": target, "level": level}
        return None

    def roll_object_attack(self, char: Character, obj_word: str,
                           force_skill: Optional[str] = None) -> dict:
        """Resolve an attack on an object or exit (v2.8.1.1 mechanics, shared
        by the preroll net and the v2.8.1.2 action resolver).

        Gun in hand and no forced skill: consumes one shell, checks the jam,
        breaks the object/exit on a hit, and tags the noise. Otherwise a
        raw STR (or explicit skill) attempt with the same state changes."""
        a = str(obj_word or "").lower()
        has_gun = bool(char.weapon and char.weapon.base_range > 0)
        if has_gun and force_skill is None:
            weapon = char.weapon
            # v2.8.1.x: the weapon in hand decides — template skill first.
            _inst = (items_mod.get_instance(char.equipped_item_id)
                     if char.equipped_item_id else None)
            _tmpl = (items_mod.get_template(_inst.template_id)
                     if _inst else None)
            skill = items_mod.firearm_skill_key(weapon, _tmpl)
            if weapon.ammo <= 0:
                return {"skill": skill, "note": "Click. Empty."}
            target = self._skill_target(char, skill)
            roll, level = self.dice.skill_check(target)
            weapon.ammo -= 1
            char.sync_weapon_to_instance()
            res = {"skill": skill, "roll": roll, "target": target,
                   "level": level, "object": a, "noise": 4}
            if roll >= weapon.malfunction:
                res["malfunction"] = True
                inst = (items_mod.get_instance(char.equipped_item_id)
                        if char.equipped_item_id else None)
                if inst is not None:
                    inst.condition = "jammed"
                char.refresh_weapon_view()
                res["note"] = f"Weapon malfunction! ({weapon.name} jams on {roll})"
                return res
            if level not in ("Failure", "Fumble"):
                applied = self._apply_object_attack(char, a)
                if applied:
                    res["object_result"] = applied
            return res
        skill = force_skill or "STR"
        target = char.STR if skill == "STR" else self._skill_target(char, skill)
        roll, level = self.dice.skill_check(target)
        res = {"skill": skill, "roll": roll, "target": target,
               "level": level, "object": a}
        if level not in ("Failure", "Fumble"):
            applied = self._apply_object_attack(char, a)
            if applied:
                res["object_result"] = applied
        return res

    def _apply_object_attack(self, char: Character, obj_word: str) -> Optional[str]:
        """v2.8.1.1: apply a successful attack to an object or exit.

        A blasted lock stays blasted: the WorldObject breaks and any exit
        linked to it (or a matching locked/closed exit) becomes passable.
        Returns a short engine-truth note for the outcome packet, or None when
        nothing breakable matched.
        """
        target = self._find_room_object(char, obj_word)
        if target is not None:
            target.state = "broken"
            target.properties["locked"] = False
            self._sync_exits_for_object(target)
            return f"{target.name} is broken."
        loc = self.locations.get(char.location)
        if loc is not None:
            for dest_id, conn in loc.connections.items():
                if not isinstance(conn, dict):
                    continue
                state = room_view.connection_state(loc, dest_id, self.world_objects)
                if state in ("locked", "closed", "blocked"):
                    conn["state"] = "destroyed"
                    dest = self.locations.get(dest_id)
                    return (f"The way to the {dest.name if dest else dest_id} "
                            f"is broken open.")
        return None

    def _sync_exits_for_object(self, obj):
        """Keep exits consistent with a mutated door/object (engine truth)."""
        for loc in self.locations.values():
            for conn in loc.connections.values():
                if isinstance(conn, dict) and conn.get("object_id") == obj.id:
                    conn["state"] = ("destroyed" if obj.state in ("broken", "destroyed")
                                     else "open" if obj.state == "open"
                                     else "closed" if obj.state == "closed"
                                     else conn.get("state", "open"))

    def _resolve_pending_rolls(self, declarations: Dict[str, str],
                               dice_results: dict):
        """Rolls the LLM asked for last turn, answered now ('roll!').

        A pending request resolves when its investigator declares a roll
        affirmation; it is abandoned when that investigator declares a
        clearly different new action instead (the moment passed), and it
        simply waits while other investigators act.
        """
        for req in list(self.pending_rolls):
            cid = req.get("character")
            if cid not in declarations or cid not in self.characters:
                continue
            action = declarations[cid]
            if not ROLL_AFFIRM.search(action):
                self.pending_rolls.remove(req)
                continue
            char = self.characters[cid]
            skill = req.get("skill", "Luck")
            target = req.get("target")
            if not isinstance(target, int) or target <= 0:
                target = self._skill_target(char, skill)
            roll, level = self.dice.skill_check(target)
            dice_results[cid] = {
                "skill": skill, "roll": roll, "target": target, "level": level,
                "requested": req.get("reason", ""),
            }
            self.pending_rolls.remove(req)

    def _harvest_dice_requests(self, result: dict):
        """Queue the rolls the LLM asked for. Merges: requests for other
        investigators stay queued across turns until answered or abandoned."""
        new = []
        for req in (result.get("dice_requests") or []):
            if not isinstance(req, dict):
                continue
            cid, skill = req.get("character"), req.get("skill")
            if cid not in self.characters or not isinstance(skill, str) or not skill:
                continue
            target = req.get("target")
            new.append({
                "character": cid, "skill": skill,
                "target": target if isinstance(target, int) and target > 0 else None,
                "reason": str(req.get("reason", ""))[:120],
            })
        if new:
            fresh = {r["character"] for r in new}
            self.pending_rolls = [r for r in self.pending_rolls
                                  if r["character"] not in fresh]
            self.pending_rolls.extend(new)

    # ------------------------------------------------------------- item helpers
    def _movement_packet(self, char: Character, result: dict) -> dict:
        """v2.8.1.1: the canonical movement packet the LLM narrates FROM.

        Movement is engine truth completed BEFORE the call: the packet says
        where the actor started, where they are now, what blocked the way,
        and exactly what the destination looks like."""
        dest = result["dest"]
        packet = {
            "character": char.id,
            "origin_location": result.get("origin"),
            "destination_location": dest,
            "current_location_after_action": dest,
            "movement_completed": True,
            "unlocked_with": result.get("unlocked"),
            "blocking_object": result.get("blocking_object"),
            "first_visit": result.get("first"),
            # v2.8.1.x P1-7: explicit continuity facts. The narrator may
            # describe the crossing, but may not contradict these: whether a
            # key was spent, and that the way now stands open behind the
            # actor (field: 'the door needed no key', the key 'unspent').
            "key_used": bool(result.get("unlocked")),
            "door_open": True,
            "triggers": result.get("triggers", []),
            "destination_room_view": room_view.build_room_view(
                self, char, first=result.get("first")),
        }
        # v2.8.1.3 Part 8: a room grants a passive inspection on entry ONLY
        # when the scenario authors an entry_check. No entry_check, no roll.
        dest_loc = self.locations.get(dest)
        chk = (dest_loc.entry_check or {}) if dest_loc else {}
        if chk.get("skill"):
            target = self._skill_target(char, chk["skill"])
            roll, level = self.dice.skill_check(target)
            packet["entry_check"] = {"skill": chk["skill"], "roll": roll,
                                     "target": target, "level": level}
        return packet

    def _find_linked_door_across_exits(self, char: Character, arg: str):
        """The door you just walked through: door objects linked to any
        connection ADJACENT to the current room, whichever side the link
        was authored on."""
        low = _ARTICLE.sub("", arg.lower().strip())
        for lid, loc in self.locations.items():
            for dest_id, conn in loc.connections.items():
                if lid != char.location and dest_id != char.location:
                    continue
                if not isinstance(conn, dict):
                    continue
                obj = self.world_objects.get(conn.get("object_id"))
                if obj is not None and (obj.name.lower() == low
                                        or low in obj.name.lower()):
                    return obj
        return None

    def _meta_move(self, char: Character, dest_id: str, origin_text: str = ""):
        """Local move from a numbered/bare 'enter' — no LLM unless the
        destination escalates; then the engine stages the packet and the
        Keeper narrates the COMPLETED crossing."""
        result = room_view.try_local_move(self, char, dest_id)
        if not result.get("moved"):
            print(f"  [{result['error']}]")
            return
        dest_name = self.locations[result["dest"]].name
        if result.get("unlocked"):
            print(f"  [{char.name} unlocks the way with the {result['unlocked']}.]")
        self._update_scene_after_move()
        if result.get("triggers"):
            print(f"  [{char.name} -> {dest_name} — the moment calls for the Keeper.]")
            self._engine_moved[char.id] = result["dest"]
            self._movement_events.append(self._movement_packet(char, result))
            self.take_turn({char.id: origin_text or f"enter the {dest_name}"})
            return
        print(f"  [{char.name} -> {dest_name}]")
        view = room_view.build_room_view(self, char, first=result["first"])
        print(room_view.render_room_text(view))

    def _announce_rolls(self, dice_results: dict):
        """The table sees what the engine saw (v2.7.1). Field log: a whole
        infiltration resolved with the player never shown a single die."""
        for cid, res in dice_results.items():
            name = self.characters[cid].name if cid in self.characters else cid
            skill = str(res.get("skill", "Roll")).replace("_", " ")
            roll, target, level = res.get("roll"), res.get("target"), res.get("level")
            if roll is not None and target is not None and level is not None:
                line = f"  » {name} — {skill} {target}%: rolled {roll} — {level}"
                if res.get("malfunction"):
                    line += " — WEAPON JAMS"
                if res.get("damage"):
                    line += f" ({res['damage']} damage)"
                if res.get("object"):
                    line += f"   (object: {res['object']})"
                if res.get("requested"):
                    line += f"   (requested: {res['requested']})"
                print(line)
                # v2.8.1.x: opposed melee shows BOTH rolls — the defender's
                # Dodge/Fight Back is why a melee hit or missed.
                dr = res.get("defender_roll")
                if dr:
                    dskill = str(dr.get("skill", "Dodge")).replace("_", " ")
                    stance = ("fights back" if res.get("stance") == "fight_back"
                              else "dodges")
                    print(f"  » {dr.get('name', 'Defender')} — {dskill} "
                          f"{dr.get('target')}%: rolled {dr.get('roll')} — "
                          f"{dr.get('level')} ({stance})")
                continue
            notes = "; ".join(res.get("notes") or []) or res.get("note", "")
            if notes:
                print(f"  » {name} — {skill}: {notes}")

    def _named_npc(self, action: str) -> Optional[Character]:
        """An NPC explicitly named in the action (v2.7.2). Keeps 'blast the
        door' off the combat engine's nearest-NPC fallback: an inanimate
        target phrase only yields to a CHARACTER when one is actually named."""
        for c in self.characters.values():
            if c.char_type == "player":
                continue
            bits = [c.id.replace("_", " ").lower()] + c.name.lower().split()
            if any(bit and bit in action for bit in bits):
                return c
        return None

    def _find_target(self, attacker: Character, action: str) -> Optional[Character]:
        """Match an NPC mentioned by name in the action, preferring same-location."""
        candidates = [c for c in self.characters.values()
                      if c.id != attacker.id and c.char_type != "player"]
        same_room = [c for c in candidates if c.location == attacker.location]
        for pool in (same_room, candidates):
            for c in pool:
                name_bits = [c.id.replace("_", " ").lower()] + c.name.lower().split()
                if any(bit and bit in action for bit in name_bits):
                    return c
        return same_room[0] if same_room else (candidates[0] if candidates else None)

    # ------------------------------------- v2.8.1.x untouched-NPC truth
    # --------- v2.8.1.x prompt-builder delegates (src/prompt_builder.py)
    # Prompt shaping and the LLM correction path live in their own module
    # (god-file split). These delegates keep take_turn, the governor, and
    # the test surface working unchanged.
    def build_prompt_sections(self, declarations: Dict[str, str],
                              dice_results: dict):
        return prompt_builder.build_prompt_sections(
            self, declarations, dice_results)

    def build_prompt(self, declarations: Dict[str, str], dice_results: dict):
        return prompt_builder.build_prompt(self, declarations, dice_results)

    def _untouched_npc_lines(self, room_ids, dice_results):
        return prompt_builder._untouched_npc_lines(
            self, room_ids, dice_results)

    def _heavy_trigger(self, mode, declarations: Dict[str, str]) -> bool:
        return prompt_builder._heavy_trigger(self, mode, declarations)

    def _narration_validation_retry(self, violations, mode, declarations,
                                    dice_results, acting_ids, plan,
                                    llm_timing, turn_context):
        return prompt_builder._narration_validation_retry(
            self, violations, mode, declarations, dice_results, acting_ids,
            plan, llm_timing, turn_context)

    def _log_validation_retry(self, violations, resolved, turn_context):
        return prompt_builder._log_validation_retry(
            self, violations, resolved, turn_context)

    def _log_validation_fallback(self, turn_context):
        return prompt_builder._log_validation_fallback(self, turn_context)

    def take_turn(self, declarations: Dict[str, str]):
        self.turn += 1
        # v2.8.1.x P0-2: any player committing a new declaration kills every
        # pending numbered menu — a stale menu can never steal a later input.
        self._clear_pending_menus()
        # v2.8.1.1: a meta-move escalation (numbered 'enter' into a trigger
        # room) stages its packet BEFORE this call; keep staged events, and
        # clear them when the turn ends so they never leak into the next one.
        self._movement_events = list(self._movement_events or [])
        self._engine_moved = dict(self._engine_moved or {})
        # v2.8.1.x: snapshot alert states BEFORE resolution — combat and
        # throws alert their targets inside the dice, and the packet must
        # know who the turn mechanically touched.
        self._alerted_at_turn_start = {
            c.id: bool(getattr(c, "alerted", True))
            for c in self.characters.values()}
        # v2.8.1.x: thrown-item placement facts are per-turn.
        self._landed_items = []
        dice_results = {}
        for cid, action in declarations.items():
            char = self.characters.get(cid)
            if not char:
                continue
            char.declared_action = action
        # v2.8.1: pure movement resolves locally before anything else. The
        # preroll net, pending rolls, and the LLM only see what's left.
        declarations = self._resolve_local_movement(declarations)
        if not declarations and not self._movement_events:
            # Every declaration was ordinary local movement — no narrative
            # turn is consumed, but the world state still persists.
            self.turn -= 1
            self.save_state()
            return None
        # Rolls the LLM requested last turn get answered first ('roll!')...
        self._resolve_pending_rolls(declarations, dice_results)
        # ...then risky declarations meet the adjudication layer (v2.8.1.2):
        # intent frames, target binding, skill scoring, and the engine — not
        # the player — deciding what needs dice.
        for cid, action in list(declarations.items()):
            if cid in dice_results:
                continue
            char = self.characters.get(cid)
            if not char:
                continue
            if cid in self._engine_moved:
                # v2.8.1.x P0-3: an engine-resolved move is DONE. It is
                # narrated from the movement packet, never re-adjudicated as
                # a fresh natural-language action — entering a room named
                # 'Study' is not the verb 'study', and grants no Spot Hidden
                # unless the scenario authors an entry_check.
                continue
            frames = self.adjudicator.adjudicate(self, char, action)
            if self.debug:
                for f in frames:
                    print(f"  [adjudicate] {f.debug_line()}")
            outcome = self.action_resolver.resolve(self, char, frames)
            if outcome.get("dice"):
                dice_results[cid] = outcome["dice"]
            if outcome.get("consumed"):
                declarations.pop(cid, None)
        if not declarations and not dice_results and not self._movement_events:
            # Everything resolved locally (compound takes/reads, a Keeper
            # clarification) — no narrative turn is consumed.
            self.turn -= 1
            self.save_state()
            return None
        # The table sees every roll the engine made (v2.7.1).
        if dice_results:
            self._announce_rolls(dice_results)

        t_prompt = time.perf_counter()
        sections, mode = self.build_prompt_sections(declarations, dice_results)
        prompt = "\n".join(s["text"] for s in sections if s["text"])
        prompt_build = time.perf_counter() - t_prompt

        use_heavy = (mode == ResolutionMode.INDIVIDUAL)
        # v2.5.1: escalation policy. v2.8.1.3 rewrite: threatening language
        # and combat against ORDINARY NPCs no longer buy the k3 tier — field
        # data had routine social threats burning heavy budgets. Policies:
        #   "individual" (legacy) — every INDIVIDUAL turn heavy
        #   "never"               — always the default model
        #   anything else (incl. the shipped "combat") — heavy only for
        #   CINEMATIC mode, Mythos/creature scenes, or a front at a trigger
        #   threshold (see _heavy_trigger).
        policy = str(self.config.get("llm", {}).get("heavy_escalation", "individual")).lower()
        if policy == "never":
            use_heavy = False
        elif policy != "individual":
            use_heavy = use_heavy and self._heavy_trigger(mode, declarations)
        is_human = getattr(self.gemini, "is_human", False)
        # v2.8.1.6: the Latency Governor shapes every real LLM call. Mock
        # sessions keep the legacy prompt (the suites pin it); tests force
        # the governed path with _force_governor.
        governed = (not self.mock or getattr(self, "_force_governor", False)) \
            and not is_human
        plan = compact_prompt = telemetry = None
        if governed:
            # v2.8.1.7 P0-6: escalation facts (movement triggers, combat
            # outcomes, multi-character outcomes) ride the tier decision.
            escalations = [t for ev in self._movement_events
                           for t in ev.get("triggers", [])]
            if any(res.get("damage") or res.get("malfunction")
                   or res.get("forced_move") for res in dice_results.values()):
                escalations.append("active-combat")
            if len(dice_results) > 1:
                escalations.append("multi-character outcome")
            plan = self.governor.plan(
                mode, declarations,
                has_movement_events=bool(self._movement_events),
                heavy_hint=use_heavy,
                escalations=escalations,
                provider=getattr(self.gemini, "provider", None))
            prompt, telemetry = self.governor.assemble(
                sections, plan, system_prompt=self.system_prompt)
            use_heavy = plan.model_tier == "heavy"
            compact_prompt = self.governor.build_compact_prompt(
                self, mode, declarations, dice_results)
            if self.debug:
                self.governor.dump_debug_prompt(self.system_prompt, prompt,
                                                telemetry)
                print(f"[governor] tier={plan.prompt_tier} "
                      f"model={plan.model_tier} budget={plan.budget} "
                      f"timeout={plan.timeout:.0f}s "
                      f"dynamic={telemetry['dynamic_prompt_chars']}ch "
                      f"system={telemetry['system_prompt_chars']}ch "
                      f"total={telemetry['total_prompt_chars']}ch "
                      f"cap={plan.prompt_cap}ch "
                      f"trimmed={telemetry['trimmed'] or '-'} "
                      f"reasons={';'.join(plan.tier_reasons) or '-'}")
        if not self.mock:
            if is_human:
                # v2.8.1.5: the engine resolved everything; a human narrates.
                print("\n[The engine has resolved the turn — "
                      "the human Keeper narrates.]")
            else:
                model = self.gemini.heavy_model if use_heavy else self.gemini.default_model
                provider = getattr(self.gemini, "provider", "gemini")
                tail = (f" — governor: {plan.prompt_tier} tier, "
                        f"{plan.timeout:.0f}s deadline" if plan else "")
                print(f"\n[Querying {provider} ({model}){tail}...]")

        # v2.8.0: versioned pipeline rows. The context rides along with the
        # LLM call so every attempt row in logs/llm_timing.jsonl knows the
        # resolution mode, turn, and caller; llm_timing comes back with the
        # api_wait/parse split for the per-turn row below.
        turn_context = {
            "resolution_mode": mode.value,
            "turn": self.turn,
            "scenario": self.scenario_id,
            "source": "keeper",
            "prompt_build": round(prompt_build, 4),
        }
        if telemetry:
            # v2.8.1.7 P0-1: timing rows carry the same honest accounting
            # as the governor debug line.
            turn_context.update({
                "dynamic_prompt_chars": telemetry["dynamic_prompt_chars"],
                "system_prompt_chars": telemetry["system_prompt_chars"],
                "total_prompt_chars": telemetry["total_prompt_chars"],
            })
        llm_timing = {}
        try:
            if is_human:
                # v2.8.1.5: packet out, narration in. No API call, no
                # timeout, no retry ladder — the host answers exactly once.
                packet = build_human_keeper_packet(self, mode, declarations,
                                                   dice_results)
                result = self.gemini.narrate(packet, timing=llm_timing,
                                             context=turn_context)
            else:
                try:
                    result = self.gemini.query(self.system_prompt, prompt,
                                               use_heavy=use_heavy,
                                               timing=llm_timing,
                                               context=turn_context,
                                               plan=plan,
                                               compact_prompt=compact_prompt)
                except TypeError as e:
                    # Test stubs may not accept the timing/context kwargs.
                    if "unexpected keyword argument" not in str(e):
                        raise
                    result = self.gemini.query(self.system_prompt, prompt,
                                               use_heavy=use_heavy)
                    llm_timing.clear()
        except HumanKeeperCancelled:
            # /cancel (or a closed terminal): same refund semantics as an
            # LLM error — the turn was NOT consumed; re-declare when ready.
            self.turn -= 1
            self._movement_events = []
            self._engine_moved = {}
            print("\n[Human Keeper cancelled the narration. "
                  "Your turn was NOT consumed.]")
            return None
        except GovernorDegraded as e:
            # v2.8.1.6: initial call timed out AND the compact retry failed.
            # The turn is preserved while the table picks the fallback.
            result = self._provider_degraded(
                plan, compact_prompt, mode, declarations, dice_results,
                use_heavy, llm_timing, turn_context, str(e))
            if result is None:
                return None
            is_human = getattr(self.gemini, "is_human", False)
        except Exception as e:
            # An LLM hiccup must never crash game night: refund the turn,
            # keep the session alive, let the players re-declare.
            self.turn -= 1
            self._movement_events = []
            self._engine_moved = {}
            print("\n" + "!" * 60)
            print("The Keeper fell silent (LLM error). Your turn was NOT consumed.")
            print(f"Error: {e}")
            print("Check the logs/ folder for raw responses, then simply re-enter")
            print("your actions. If this repeats, see docs/LLM-PROVIDERS.md.")
            print("!" * 60 + "\n")
            return None

        narration = result.get("narration", "The Keeper is silent...")
        # v2.8.1.3 Part 7: NPC world-changing actions need engine outcomes.
        acting_ids = list(declarations)
        violations = self._validate_narration(str(narration), result,
                                              dice_results, acting_ids)
        if violations and is_human:
            # v2.8.1.5: the human Keeper is warned, not hard-retried — only
            # the packet outcomes stand. (The AI gets a strict retry below.)
            print("  [Keeper warning — the narration introduces world changes "
                  "the engine did not produce: " + "; ".join(violations)
                  + ". Only the packet outcomes stand.]")
        elif violations:
            # v2.8.1.x P0-1: a rejected narration earns exactly ONE compact
            # correction attempt — compact system prompt, compact outcome
            # packet, compact budget/deadline, no retry ladder. The v2.8.1.7
            # path re-sent the full 13k prompt through the whole ladder, so a
            # rejected narration cost more than the turn itself (field:
            # 'inspect letter' = 4 paid calls / 535.5s).
            recovered = self._narration_validation_retry(
                violations, mode, declarations, dice_results, acting_ids,
                plan, llm_timing, turn_context)
            # v2.8.1.x FIX B: the FIRST attempt's violations are telemetry
            # whether the retry resolves or not — before any rule tuning,
            # measure which rule the model breaks most.
            self._log_validation_retry(violations, recovered is not None,
                                       turn_context)
            if recovered is not None:
                result = recovered
                narration = str(recovered.get("narration", narration))
                violations = []
            if violations:
                # v2.8.1.7 P0-5: the local outcome — unsupported narration is
                # never accepted, and the full prompt is never re-sent.
                self._log_validation_fallback(turn_context)
                result = self._minimal_outcome_result(mode, dice_results)
                narration = result["narration"]
                violations = []
        print("\n" + "=" * 60)
        print(narration)
        # v2.7.6: private narrations are only private if they stay private.
        # A player's own thoughts always reach their screen; an NPC's
        # thoughts are keeper-view and print only in debug mode — tagged
        # [KEEPER — name] so the spoiler channel stays shut at the table.
        for cid, priv in (result.get("private_narrations") or {}).items():
            c = self.characters.get(cid)
            name = c.name if c else cid
            if c and c.char_type != "player":
                if self.debug:
                    print(f"\n[KEEPER — {name}] {priv}")
                continue
            print(f"\n[PRIVATE — {name}] {priv}")
        print(f"\n{result.get('required_actions', 'What do you do?')}")
        print("=" * 60 + "\n")

        t_apply = time.perf_counter()
        self._apply_state_delta(result.get("state_delta", {}) or {})
        state_apply = time.perf_counter() - t_apply
        # v2.8.1: engine-resolved moves are truth. If the model's delta tried
        # to move an engine-moved character somewhere else, the engine wins.
        for cid, dest in self._engine_moved.items():
            char = self.characters.get(cid)
            if char is not None and char.location != dest:
                self.spatial.move_occupant(cid, char.location, dest)
                char.location = dest
        if self._engine_moved:
            self._update_scene_after_move()
        # Queue any rolls the LLM asked for — next turn's 'roll!' carries
        # real dice (v2.7.1; the channel existed since v2.2 but was ignored).
        self._harvest_dice_requests(result)
        if self.chronicle:
            self.chronicle.append(self.turn, narration, result.get("state_delta", {}) or {})

        t_save = time.perf_counter()
        self.save_state()
        save_time = time.perf_counter() - t_save

        if self.latency:
            self.latency.record(
                self.turn,
                prompt_build=prompt_build,
                api_wait=llm_timing.get("api_wait", 0.0),
                parse=llm_timing.get("parse", 0.0),
                state_apply=state_apply,
                save=save_time,
            )
            if self.mock and len(self.latency.turns) == 3:
                self.latency.print_profile()

        # v2.8.0: per-turn pipeline row for real sessions (mock turns would
        # pollute the measurements — they never touch the network).
        if not self.mock:
            from datetime import datetime, timezone
            from src import latency as _lat
            total = (prompt_build + llm_timing.get("api_wait", 0.0)
                     + llm_timing.get("parse", 0.0) + state_apply + save_time)
            _lat.write_timing_row(_lat.TURN_TIMING_LOG, {
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "version": _lat.project_version(),
                "commit": _lat.git_commit(),
                "scenario": self.scenario_id,
                "turn": self.turn,
                "resolution_mode": mode.value,
                "tier": "heavy" if use_heavy else "default",
                "prompt_chars": len(self.system_prompt) + len(prompt),
                "narration_chars": len(str(narration)),
                "prompt_build": round(prompt_build, 4),
                "api_wait": round(llm_timing.get("api_wait", 0.0), 3),
                "parse": round(llm_timing.get("parse", 0.0), 4),
                "state_apply": round(state_apply, 4),
                "save": round(save_time, 4),
                "total_s": round(total, 2),
            })

        # Staged movement packets live for exactly one turn.
        self._movement_events = []
        self._engine_moved = {}
        self._landed_items = []
        # Pending menus staged DURING this turn (e.g. an attack-target
        # clarification) stay alive for the next input; they die on the next
        # declaration (take_turn start) or when answered (v2.8.1.x P0-2).
        return result

    # ------------------------------------------------- v2.8.1.6 degraded mode
    def _provider_degraded(self, plan, compact_prompt, mode, declarations,
                           dice_results, use_heavy, llm_timing, turn_context,
                           reason=""):
        """The provider timed out on the initial call AND the compact retry.

        The turn is preserved (not yet consumed) while the table chooses:
        retry compact, hand the voice to a human host, accept a plain local
        outcome, or save and quit. Options 1-3 produce a result and the turn
        counts; option 4 refunds it."""
        print("\n" + "!" * 60)
        print("The Keeper fell silent twice (provider timeout). "
              "Your turn is NOT consumed yet.")
        if reason:
            print(f"({reason})")
        while True:
            print("Choose how to continue:")
            print("  1. retry compact")
            print("  2. switch to the Human Keeper (a human host narrates)")
            print("  3. use minimal local outcome text (no narration)")
            print("  4. save and quit")
            try:
                choice = input("degraded [1-4]: ").strip()
            except EOFError:
                choice = "4"   # a closed terminal must never hang the table
            if choice == "1":
                # v2.8.1.7 P0-2: retry COMPACT means the stored compact
                # prompt, the compact system prompt, and the compact
                # CallPlan (compact budget, compact deadline, exactly one
                # attempt) — never the ordinary prompt path.
                from src.latency_governor import COMPACT_SYSTEM_PROMPT
                compact_plan = (plan.for_compact_retry() if plan is not None
                                else None)
                try:
                    print("[Retrying with the compact prompt...]")
                    return self.gemini.query(
                        COMPACT_SYSTEM_PROMPT, compact_prompt,
                        use_heavy=use_heavy, timing=llm_timing,
                        context=turn_context, plan=compact_plan,
                        compact_prompt=None)
                except Exception as e:
                    print(f"[Still failing: {e}]")
                    continue
            if choice == "2":
                from src.human_keeper import HumanKeeperClient
                print("[Switching to the Human Keeper for this session.]")
                self.gemini = HumanKeeperClient(config=self.config,
                                                debug=self.debug)
                packet = build_human_keeper_packet(self, mode, declarations,
                                                   dice_results)
                try:
                    return self.gemini.narrate(packet, timing=llm_timing,
                                               context=turn_context)
                except HumanKeeperCancelled:
                    self.turn -= 1
                    self._movement_events = []
                    self._engine_moved = {}
                    print("[Human Keeper cancelled. Your turn was NOT consumed.]")
                    return None
            if choice == "3":
                return self._minimal_outcome_result(mode, dice_results)
            if choice == "4":
                self.turn -= 1
                self._movement_events = []
                self._engine_moved = {}
                self._shutdown()
                self._quit_requested = True
                return None
            print("[Choose 1, 2, 3, or 4.]")

    def _minimal_outcome_result(self, mode, dice_results):
        """Degraded option 3: the engine speaks for itself — one composed
        local-voice sentence per outcome, no LLM, no cost, no invention.
        The implementation lives in src/local_voice.py (v2.8.1.x split);
        this delegate keeps the call sites and the test surface stable."""
        return local_voice.minimal_outcome_result(self, mode, dice_results)

    # ------------------------------------- v2.8.1.x P0-1 validation retry
    def _validation_packet(self) -> dict:
        """Ground truth for narration validation — scene NPC states plus
        the room's tracked objects. Implementation lives in
        src/narration_validator.py (v2.8.1.x god-file split); this delegate
        keeps the retry path and the test surface stable."""
        return narration_validator.validation_packet(self)

    def _validate_narration(self, narration: str, result: dict,
                            dice_results: dict, acting_ids=None) -> List[str]:
        """Flag narration the engine did not produce — the Truth
        Firewall's prose wall. The rules and the implementation
        live in src/narration_validator.py (v2.8.1.x god-file
        split); this delegate keeps the call sites and the ~45
        test references stable."""
        return narration_validator.validate_narration(
            self, narration, result, dice_results, acting_ids)

    def _apply_state_delta(self, delta: dict):
        """Apply the safe subset of a model-produced state delta.

        v2.7.6.1 -- the Truth Firewall. The model may narrate and propose, but
        canonical mechanical state is engine-owned. Rejected writes are kept in
        the validation report and shown in debug mode; they never crash a turn.
        """
        report = self.state_validator.validate(
            delta,
            characters=self.characters,
            fronts=self.fronts,
            locations=self.locations,
        )
        debug = bool(self.config.get("llm", {}).get("debug", False))
        if debug and report.rejected:
            print("\n[STATE REJECTED]")
            for rejection in report.rejected:
                print(f" - {rejection.format()}")

        delta = report.delta
        for cid, changes in (delta.get("characters") or {}).items():
            char = self.characters.get(cid)
            if not char or not isinstance(changes, dict):
                continue
            old_loc = char.location
            for k, v in changes.items():
                # Belt-and-braces: even a direct internal call cannot use this
                # path to overwrite canonical mechanical state.
                if k in ENGINE_OWNED_CHARACTER_FIELDS:
                    continue
                if hasattr(char, k):
                    setattr(char, k, v)
            if char.location != old_loc:
                self.spatial.move_occupant(cid, old_loc, char.location)

        for k, v in (delta.get("fronts") or {}).items():
            if k in self.fronts and isinstance(v, (int, float)):
                maximum = int(self.fronts[k].get("max", int(v)))
                self.fronts[k]["clock"] = max(0, min(int(v), maximum))

        for p in (delta.get("plot_points") or []):
            if p not in self.plot_points:
                self.plot_points.append(p)

        for trans in (delta.get("scene_transitions") or []):
            for cid, dest in trans.items():
                char = self.characters.get(cid)
                if char and dest in self.locations:
                    self.spatial.move_occupant(cid, char.location, dest)
                    char.location = dest

        players = [c for c in self.characters.values() if c.char_type == "player"]
        if players and all(p.location == players[0].location for p in players):
            self.current_scene = players[0].location

    # ------------------------------------------------------------- persistence
    # ------------------------------------------------------------------- loop
    def run_session(self):
        from src import __version__
        print("\n" + "=" * 60)
        print(f" CALL OF CTHULHU 7th — LLM KEEPER  v{__version__}"
              + ("  [MOCK MODE]" if self.mock else "")
              + ("  [HUMAN KEEPER]" if getattr(self.gemini, "is_human", False) else ""))
        print("=" * 60)
        print(f"Scenario: {self.scenario_id} | Turn: {self.turn} | Scene: {self.current_scene}")
        print("Type 'quit' to save and exit. "
              "Gear commands: inventory, equip, unequip, take, drop, give, "
              "reload, open, look at, use. 'help' lists all commands.\n")
        while True:
            try:
                declarations = {}
                players = [(cid, char) for cid, char in list(self.characters.items())
                           if char.char_type == "player"
                           and not char.dying and not char.unconscious]
                # Surprise accounting (v2.8.1.x): who was where when the
                # round BEGAN — _alert_check may only use this snapshot, so
                # the entry round is always a full round of surprise.
                self._round_start_player_rooms = {
                    cid: char.location for cid, char in players}
                # v2.8.1.x FIX A: the surprise window closes only after a
                # round that CONSUMED a turn. A clarify menu refunds its
                # turn (take_turn's turn -= 1), so the turn counter — not
                # the declarations dict — marks a resolved round (field
                # 2026-07-29: 'throw knife at guman' -> menu -> alert lines
                # BEFORE the '2' answer and the roll).
                turn_at_round_start = self.turn
                # All-pass accounting (v2.8.1.x field fix): 'Everyone passes'
                # is printed only for a genuine all-pass round — every
                # player explicitly passed, no local/meta command ran, no
                # menu is open, and nothing resolved this round.
                round_passes = 0
                round_activity = False
                time_pass = False
                resolve_now = False
                idx = 0
                while idx < len(players) and not resolve_now:
                    cid, char = players[idx]
                    # v2.8.1.x party-turn contract: the prompt says where you
                    # are, who is with you, and what each turn keyword does.
                    loc = self.locations.get(char.location)
                    room_name = loc.name if loc else char.location
                    companions = [
                        c.name for c in self.characters.values()
                        if c.id != cid and c.char_type == "player"
                        and not c.dying and not c.unconscious
                        and c.location == char.location]
                    where = (room_name + (" — with " + ", ".join(companions)
                                          if companions else ""))
                    action = input(
                        f"{char.name} ({char.owner or 'player'}) [{where}] "
                        f"[Enter=pass, 'done'=resolve, 'end'=time passes]: "
                    ).strip()
                    low = action.lower()
                    if low in ("quit", "exit", "save"):
                        self._shutdown()
                        return
                    if low in ("done", "resolve", "end", "end turn"):
                        if declarations:
                            # Resolve the batch now; anyone who has not
                            # declared is treated as passing this turn.
                            skipped = [c.name for _cid, c in players[idx:]
                                       if _cid not in declarations]
                            if low in ("done", "resolve"):
                                note = "[Resolving the current batch"
                            else:
                                note = "[Ending the turn early"
                            if skipped:
                                note += (" — no declaration from "
                                         + ", ".join(skipped)
                                         + "; treated as passing")
                            print(note + ".]")
                            resolve_now = True
                        elif low in ("end", "end turn"):
                            # Ending an empty turn is a local time-pass:
                            # no narration, no call, but the clock moves.
                            self.turn += 1
                            time_pass = True
                            print(f"[Time passes — turn {self.turn}. "
                                  "No one acts; the house does not wait "
                                  "forever.]")
                            resolve_now = True
                        else:
                            # 'done' with nothing collected must not eat the
                            # rest of the party's chance to declare.
                            print("[Nothing to resolve yet — no declarations. "
                                  "The rest of the party may still act.]")
                        idx += 1
                        continue
                    if low in ("pass", "wait") or not action:
                        print(f"[{char.name} passes.]")
                        round_passes += 1
                        idx += 1
                        continue
                    # System channel: engine commands never become declarations.
                    if self._meta_command(char, action):
                        round_activity = True
                        idx += 1
                        continue
                    declarations[cid] = action
                    idx += 1
                if declarations:
                    self.take_turn(declarations)
                    if self._quit_requested:
                        return
                elif not resolve_now and players:
                    # Only a genuine all-pass round is announced: everyone
                    # explicitly passed, no local/meta command ran, no menu
                    # is open, and nothing resolved (field: the line printed
                    # after look/inv/grab rounds and after narrated moves).
                    menu_open = any(c.extra.get("_last_menu")
                                    for _cid, c in players)
                    if round_passes == len(players) and not round_activity \
                            and not menu_open:
                        print("[Everyone passes — the moment holds. "
                              "Type 'end' to let time pass.]")
                # Surprise window (v2.8.1.x field fix): it closes ONLY at
                # the end of a round that RESOLVED something — a consumed
                # narrative turn or an explicit time pass. Rounds of free
                # commands (look, distance, inventory), clarify menus (the
                # turn was refunded, so the counter never moved), and pure
                # passes keep the window open: the player always gets first
                # shot. The round-start snapshot semantics are unchanged,
                # so the entry round itself is always safe.
                if self.turn != turn_at_round_start or time_pass:
                    self._alert_check()
            except KeyboardInterrupt:
                print()
                self._shutdown()
                return
            except EOFError:
                # A closed terminal must save, not crash.
                print()
                self._shutdown()
                return

    def _shutdown(self):
        if self.chronicle:
            try:
                self.chronicle.flush()
            except Exception as e:
                print(f"[Chronicle flush failed: {e}]")
        self.save_state()
        print("Session saved. The shadows wait...")
