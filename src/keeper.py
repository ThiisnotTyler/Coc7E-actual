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
from src.latency import LatencyCollector
from src.state_validator import (
    ENGINE_OWNED_CHARACTER_FIELDS,
    StateDeltaValidator,
)
from src.human_keeper import HumanKeeperCancelled, build_human_keeper_packet
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


# v2.8.1.3 Part 7: scenario-critical actions must come from engine outcomes.
# The model may not have NPCs write marks, move tracked items, open or close
# tracked doors, change object state, reveal hidden clues, or change rooms
# unless the outcome packet says so.
WORLD_CHANGE_VERBS = (
    "writes", "wrote", "scribbles", "scrawls", "chalks",
    "opens", "opened", "closes", "closed", "shuts", "slams",
    "breaks", "broke", "smashes", "destroys",
    "takes", "grabs", "snatches", "picks up", "steals", "pockets",
    "moves to", "leaves", "flees", "escapes", "runs off", "walks out",
)

# v2.8.1.7 P0-5: first-visit continuity. On a character's FIRST visit the
# narrator may not claim prior familiarity, return, repeated inspection, or
# unchanged state (field: 'upon this return', 'just as they did before',
# 'You keep checking' on Patrick's first entry).
FIRST_VISIT_RES = tuple(re.compile(p) for p in (
    r"\bupon this return\b", r"\bthis return\b", r"\byour return\b",
    r"\bas they did before\b", r"\bas you did before\b",
    r"\byou keep checking\b", r"\bwhere you left it\b",
    r"\bhasn'?t moved\b", r"\bas before\b", r"\bwelcomes? you back\b",
    r"\bfamiliar\b", r"\bonce again\b",
))

# v2.8.1.7 P0-5: invented scenario facts — countdowns, deadlines, new
# monsters, front clocks, timeline events the packet never carried.
SCENARIO_FACT_RES = tuple(re.compile(p) for p in (
    r"\bcountdown\b", r"\bdeadline\b", r"\brunning out of time\b",
    r"\btime runs out\b", r"\bbefore it'?s too late\b",
    r"\bclock is ticking\b", r"\bclock ticking\b",
    r"\ba second (?:creature|monster)\b", r"\banother (?:creature|monster)\b",
))

# v2.8.1.x P1-7: unlock/key continuity. When the engine spent a key to get
# the actor through, the narration may not claim the door needed no key or
# that the key is still unspent (field: the Brass Key was 'unspent' and the
# study door 'needed no key' right after the engine used it).
KEY_DENY_RE = re.compile(
    r"needed no key|no key (?:was |is )?(?:needed|required)|"
    r"without (?:a|the|any) key|"
    r"\bkey\b[^.]{0,25}\b(?:unspent|unused)\b|"
    r"\b(?:unspent|unused)\b[^.]{0,25}\bkey\b|"
    r"wasn'?t locked|was not locked|never locked|already unlocked")

# v2.8.1.x P1-7: door continuity. The actor passed through — the way they
# came is not 'still locked' behind them.
DOOR_STILL_LOCKED_RE = re.compile(
    r"\b(?:still|remains?|remained|stays?|stayed|stood)\s+"
    r"(?:firmly\s+|tightly\s+|fast\s+)?locked\b")

# v2.8.1.x: negation cues. A state word inside a negated window is a
# REFERENCE to the current state, not a new claim — 'no blood', 'not
# knocked out', 'doesn't fall', 'far from unconscious' (field: two benign
# narrations were killed and the Keeper fell voiceless over these).
NARRATION_NEG_RE = re.compile(
    r"\b(?:no|not|n't|never|neither|nor|without|hardly)\b|"
    r"\bfar from\b|\bunhurt\b|\bunharmed\b|\bunscathed\b|\buninjured\b|"
    r"\bstill standing\b|\bremain\w* standing\b|\bstay\w* standing\b|"
    r"\bon (?:his|her|their) feet\b")

# NPC name bits that never identify a specific person — 'the' would make
# every 'The X' NPC a mention in every sentence.
_NPC_NAME_NOISE = {"the", "a", "an", "mr", "mrs", "ms", "dr", "miss", "sir"}

# v2.8.1.x: nouns a player would expect to INTERACT with — furniture with
# state, containers, training props. When narration introduces one that
# room_view does not track, that is an invented physical object, not
# atmosphere (field: 'a practice dummy' materialized in a bare hall).
INTERACTABLE_NOUNS = (
    "dummy", "mannequin", "table", "desk", "chair", "bench", "stool",
    "cabinet", "chest", "crate", "barrel", "locker", "trunk", "safe",
    "shelf", "bookcase", "bookshelf", "cupboard", "wardrobe", "drawer",
    "pedestal", "altar", "statue", "rack", "stand", "stove", "furnace",
    "toolbox", "coffin", "sarcophagus", "anvil", "forge", "workbench",
    "counter", "stall", "booth", "cage", "lever", "console", "terminal",
    "generator", "machine", "bed", "bunk", "cot", "couch", "sofa",
    "ottoman", "bureau",
)


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
    @property
    def save_path(self) -> str:
        return f"saves/{self.scenario_id}/world-state.json"

    def load_scenario(self, scenario_path: str):
        with open(os.path.join(scenario_path, "scenario.json"), encoding="utf-8") as f:
            data = json.load(f)
        self.scenario_id = data.get("id", os.path.basename(scenario_path.rstrip("/")))
        # v2.8.1.6: name/tone ride the compact-retry prompt and the prompt's
        # scenario section.
        self.scenario_title = data.get("title", self.scenario_id)
        self.scenario_tone = str(data.get("description", ""))[:160]
        # v2.7.0: a local chronicle files itself under the loaded scenario
        if self.chronicle is not None and hasattr(self.chronicle, "set_scenario"):
            self.chronicle.set_scenario(self.scenario_id)
        self.fronts = data.get("fronts", {})
        self.current_scene = data.get("starting_location", "")
        self.clues = data.get("clues", [])
        self.timeline = data.get("timeline", [])

        # v2.8.0: scenario-specific template overrides and world objects.
        items_mod.merge_catalog(data.get("items", []), self.item_templates)
        for obj_data in data.get("objects", []):
            obj = items_mod.create_world_object(obj_data)
            self.world_objects[obj.id] = obj

        # v2.8.1: scenario item placement. "placed_items" creates instances in
        # rooms (or on NPCs) at campaign start:
        #   {"template": "brass_key", "location": "house_hallway",
        #    "name": optional, "quantity": optional, "tags": ["hidden", ...]}
        for place in data.get("placed_items", []):
            if not isinstance(place, dict):
                continue
            tmpl = self.item_templates.get(place.get("template"))
            if tmpl is None:
                continue
            inst = items_mod.create_instance(
                tmpl,
                owner_id=place.get("owner"),
                location_id=place.get("location"),
                quantity=int(place.get("quantity", 1)),
                name=place.get("name"),
                registry=self.item_instances,
            )
            for tag in place.get("tags", []):
                if tag not in inst.tags:
                    inst.tags.append(tag)

        for loc_id, loc_data in data.get("locations", {}).items():
            loc_data = dict(loc_data)
            loc_data["occupants"] = set(loc_data.get("occupants", []))
            self.locations[loc_id] = Location(id=loc_id, **loc_data)

        for npc_data in data.get("npcs", []):
            npc = self._character_from_scenario(npc_data, default_type="npc")
            self._register(npc)

    def _character_from_scenario(self, d: dict, default_type: str = "npc") -> Character:
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

    def _register(self, char: Character):
        self.characters[char.id] = char
        if char.location in self.locations:
            self.locations[char.location].occupants.add(char.id)

    def add_player(self, char: Character):
        char.char_type = "player"
        self._reconcile_inventory(char)
        self._register(char)

    # ------------------------------------------------- v2.8.1.1 registry P0
    def _registry_audit(self, char: Character, after: str = "") -> bool:
        """The inventory invariant is mandatory: every id resolves to a
        canonical ItemInstance. A corrupted or missing reference is pruned
        with a local audit message — the session never crashes on gear
        bookkeeping."""
        missing = [e for e in char.inventory
                   if not isinstance(e, str) or e not in self.item_instances]
        for e in missing:
            char.inventory.remove(e)
        dangling = (char.equipped_item_id
                    and char.equipped_item_id not in self.item_instances)
        if dangling:
            char.equipped_item_id = None
            char.refresh_weapon_view()
        if missing or dangling:
            print(f"  [Registry audit{': ' + after if after else ''} — removed "
                  f"{len(missing)} unresolved item reference(s).]")
        return not missing and not dangling

    def _reconcile_inventory(self, char: Character):
        """P0 root cause (field, v2.8.1.1): roster characters can carry legacy
        STRING inventory entries (display names saved before the item
        registry). The v2.8.0 save migration never ran on the roster path, so
        those names reached char.inventory with no ItemInstance behind them —
        and 'open' crashed dereferencing the .get() fallback. Migrate names
        into real instances here; anything unresolvable is pruned by audit."""
        needs = [e for e in char.inventory
                 if isinstance(e, str) and e not in self.item_instances]
        dangling = (char.equipped_item_id
                    and char.equipped_item_id not in self.item_instances)
        if not needs and not dangling:
            return
        d = char.to_dict()
        items_mod.migrate_character(d, self.item_templates, self.item_instances)
        char.inventory = [e for e in d.get("inventory", []) if isinstance(e, str)]
        char.equipped_item_id = d.get("equipped_item_id")
        char.refresh_weapon_view()
        self._registry_audit(char, after="roster reconciliation")

    # ------------------------------------------------------- v2.8.1 room truth
    def mark_visited(self, char_id: str, loc_id: str):
        self.visited.setdefault(char_id, set()).add(loc_id)
        counts = self.visit_counts.setdefault(char_id, {})
        counts[loc_id] = counts.get(loc_id, 0) + 1

    def _cmd_observe(self, char: Character):
        """Local observation: deterministic room view, no LLM, no turn."""
        view = room_view.build_room_view(self, char)
        print(room_view.render_room_text(view))
        self.mark_visited(char.id, char.location)

    def _exit_list(self, char: Character) -> str:
        exits = room_view.visible_exits(self.locations, char.location,
                                        self.world_objects)
        if not exits:
            return "none that you can see"
        return "; ".join(
            e["name"] + (f" [{e['state']}]" if e["state"] != "open" else "")
            for e in exits)

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
    def _iname(self, iid: str) -> str:
        inst = self.item_instances.get(iid)
        return inst.name if inst is not None else iid

    def _find_carried_item(self, char: Character, arg: str) -> Optional[items_mod.ItemInstance]:
        low = _ARTICLE.sub("", arg.lower().strip())
        # equipped first, then inventory
        for iid in ([char.equipped_item_id] if char.equipped_item_id else []) + list(char.inventory):
            if not iid:
                continue
            inst = self.item_instances.get(iid)
            if inst is None:
                continue
            if inst.name.lower() == low or low in inst.name.lower():
                return inst
        return None

    def _find_room_item(self, char: Character, arg: str) -> Optional[items_mod.ItemInstance]:
        low = _ARTICLE.sub("", arg.lower().strip())
        for inst in self.item_instances.values():
            if inst.owner_id is None and inst.location_id == char.location:
                if inst.name.lower() == low or low in inst.name.lower():
                    return inst
        return None

    def _find_room_object(self, char: Character, arg: str) -> Optional[items_mod.WorldObject]:
        low = arg.lower()
        for obj in self.world_objects.values():
            if obj.location_id == char.location:
                if obj.name.lower() == low or low in obj.name.lower():
                    return obj
        return None

    def _find_character_in_room(self, char: Character, arg: str) -> Optional[Character]:
        low = arg.lower()
        for c in self.characters.values():
            if c.id == char.id:
                continue
            if c.location != char.location:
                continue
            if c.name.lower() == low or low in c.name.lower() or low in c.id.replace("_", " "):
                return c
        return None

    def _show_item(self, thing) -> str:
        if thing is None:
            return "nothing"
        if isinstance(thing, items_mod.ItemInstance):
            extra = ""
            if thing.condition != "intact":
                extra += f" [{thing.condition}]"
            if thing.ammo is not None:
                extra += f" ({thing.ammo} rounds)"
            return f"{thing.name}{extra}"
        if isinstance(thing, items_mod.WorldObject):
            state = thing.state
            props = ", ".join(f"{k}={v}" for k, v in thing.properties.items())
            if props:
                return f"{thing.name} [{state}; {props}]"
            return f"{thing.name} [{state}]"
        return str(thing)

    # -------------------------------------- v2.8.1.1 command normalization
    def _visible_room_items(self, char: Character) -> list:
        return [inst for inst in self.item_instances.values()
                if inst.location_id == char.location and inst.owner_id is None
                and "hidden" not in inst.tags]

    def _carried_items(self, char: Character) -> list:
        return [self.item_instances[iid] for iid in char.inventory
                if self.item_instances.get(iid) is not None]

    def _openable_things(self, char: Character) -> list:
        things = [o for o in self.world_objects.values()
                  if o.location_id == char.location
                  and o.state not in ("open", "hidden", "broken", "destroyed")]
        things += [i for i in self._visible_room_items(char)
                   if i.item_type == "container" and not i.state.get("open")]
        return things

    def _readable_things(self, char: Character) -> list:
        return [i for i in self._carried_items(char) + self._visible_room_items(char)
                if i.item_type in ("document", "clue")
                or "document" in i.tags or "clue" in i.tags]

    def _notable_things(self, char: Character) -> list:
        things = list(self._visible_room_items(char))
        things += [o for o in self.world_objects.values()
                   if o.location_id == char.location and o.state != "hidden"]
        things += [c for c in self.characters.values()
                   if c.id != char.id and c.location == char.location
                   and not c.extra.get("hidden")]
        return things

    def _store_menu(self, char: Character, kind: str, ids: list, **extra):
        # v2.8.1.7 P0-3: pending menus carry their OWNER
        # (pending_action_owner_character_id). In hotseat play anyone may
        # type the answer, but the result always applies to the owner; a
        # future remote client may only answer its own pending menus.
        # v2.8.1.x: extra payload (e.g. verb for attack-target menus).
        char.extra["_last_menu"] = {"kind": kind, "ids": list(ids),
                                    "owner": char.id, **extra}

    def _answer_attack_menu(self, owner: Character, menu: dict, n: int) -> bool:
        """Resolve a pending attack-target menu pick (v2.8.1.x field fix).

        The numbered answer replays the original attack verb against the
        CHOSEN target as a fresh engine turn — the attack is never resolved
        against a guessed target. The menu is consumed either way."""
        pick = self._menu_pick(owner, "attack", n)
        verb = (menu or {}).get("verb", "shoot")
        owner.extra.pop("_last_menu", None)
        tgt = self.characters.get(pick) if pick else None
        if tgt is None:
            print(f"  [No target {n} — declare the attack again.]")
            return True
        self.take_turn({owner.id: f"{verb} {tgt.name}"})
        return True

    def _pending_menu(self, char: Character):
        """The pending numbered menu an answer routes to (v2.8.1.7 P0-3).

        Owner-first. Otherwise exactly one pending menu table-wide: a
        hotseat answer from another player applies to the OWNER of the
        pending action — a different actor's input never silently hijacks
        it. Returns (owner_char, menu, routed_from_other).

        v2.8.1.x P0-2: cross-player routing is consulted ONLY by the
        explicit numeric answer forms (bare '2', 'enter 2', 'take 1', ...).
        A new non-numeric declaration or command must never answer another
        player's pending menu."""
        menu = char.extra.get("_last_menu")
        if menu:
            return char, menu, False
        owners = [(c, c.extra.get("_last_menu"))
                  for c in self.characters.values()
                  if c.char_type == "player" and c.extra.get("_last_menu")]
        if len(owners) == 1:
            return owners[0][0], owners[0][1], True
        return char, None, False

    def _clear_pending_menus(self):
        """v2.8.1.x P0-2: pending menus are runtime-only and die on ANY new
        declaration, on turn completion, and before save (field: Jack's
        resolved 'enter' menu stayed alive and later stole Patrick's
        'enter', moving Jack back out of the Study)."""
        for c in self.characters.values():
            c.extra.pop("_last_menu", None)

    def _menu_pick(self, char: Character, kind: str, n: int):
        menu = char.extra.get("_last_menu") or {}
        ids = menu.get("ids") or []
        if menu.get("kind") != kind or not (1 <= n <= len(ids)):
            return None
        return ids[n - 1]

    def _resolve_menu_thing(self, owner: Character, kind: str, pick):
        """What a numbered menu pick points at. 'open' menus list WORLD
        OBJECTS (doors) alongside container items, so a pick must resolve
        against the openable pool — not item_instances (v2.8.1.x field fix:
        'open' -> '1' answered 'No selection' for the Range Door). Every
        other kind is an item instance."""
        if not pick:
            return None
        if kind == "open":
            return next((x for x in self._openable_things(owner)
                         if x.id == pick), None)
        return self.item_instances.get(pick)

    def _print_numbered(self, names: list, hint: str):
        for i, name in enumerate(names, 1):
            print(f"    {i}. {name}")
        print(f"  [{hint}]")

    def _do_unequip(self, char: Character, arg: str = "") -> bool:
        if not char.equipped_item_id:
            print(f"  [{char.name} has nothing in hand.]")
            return True
        name = self._iname(char.equipped_item_id)
        if arg and arg.lower() not in name.lower():
            print(f"  [{char.name} is holding the {name}, not a '{arg}'.]")
            return True
        char.equipped_item_id = None
        char.weapon = None
        print(f"  [{char.name} puts the {name} away.]")
        return True

    def _print_read(self, char: Character, inst):
        print(f"  [{char.name} reads the {inst.name}.]")
        desc = inst.state.get("text", "")
        tmpl = self.item_templates.get(inst.template_id)
        if not desc and tmpl is not None:
            desc = tmpl.description
        if desc:
            print(f"    {desc}")

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

    def _normalize_command(self, char: Character, text: str):
        """v2.8.1.1 hotfix: natural arguments for local commands.

        Bare commands list or use their one valid target, numbered selection
        ('take 1') picks from the last listing, unequip aliases resolve, and
        'use <room item>' suggests take/read/look instead of failing blind.
        Returns True when the input was consumed, None for normal dispatch.
        """
        t = " ".join(text.strip().lower().split())
        if not t:
            return None
        cmd = t.split()[0]
        arg = text.strip()[len(cmd):].strip()

        # v2.8.1.1 P0 desync: a bare number selects from the last numbered
        # menu ('go to' then '2'). Before this, bare digits leaked to the LLM
        # as declarations and the model narrated from the origin room.
        # v2.8.1.7 P0-3: the answer routes to the menu's OWNER — Patrick's
        # '2' moves Jack, never Patrick.
        if t.isdigit():
            owner, menu, routed = self._pending_menu(char)
            kind = (menu or {}).get("kind")
            n = int(t)
            if routed and kind:
                menu["answered_by"] = char.id
                print(f"  [menu: {char.name} answered '{n}' for "
                      f"{owner.name}'s pending {kind}.]")
            if kind == "enter":
                pick = self._menu_pick(owner, "enter", n)
                exits = room_view.visible_exits(self.locations, owner.location,
                                                self.world_objects)
                owner.extra.pop("_last_menu", None)   # answered: consumed
                if pick in {e["id"] for e in exits}:
                    self._meta_move(owner, pick)
                    return True
                print(f"  [No exit {n} — list them again with 'enter'.]")
                return True
            if kind == "attack":
                # v2.8.1.x: the attack resolves against the CHOSEN target.
                return self._answer_attack_menu(owner, menu, n)
            if kind in ("take", "equip", "drop", "reload", "open", "use",
                        "give", "read"):
                pick = self._menu_pick(owner, kind, n)
                thing = self._resolve_menu_thing(owner, kind, pick)
                owner.extra.pop("_last_menu", None)   # answered: consumed
                if thing is not None:
                    return self._meta_command(owner, f"{kind} {thing.name}")
                print(f"  [No selection {n} — list them again with '{kind}'.]")
                return True
            return None

        # v2.8.1.x: '<attack verb> <n>' answers a pending attack-target menu
        # ('shoot 1') with the same ownership rules as a bare '1'.
        if arg.isdigit() and cmd in (
                "shoot", "fire", "blast", "hit", "kick", "attack", "strike",
                "punch", "stab", "swing", "smash", "slam", "tackle", "plug"):
            owner, menu, routed = self._pending_menu(char)
            if (menu or {}).get("kind") == "attack":
                if routed:
                    menu["answered_by"] = char.id
                    print(f"  [menu: {char.name} answered '{cmd} {arg}' for "
                          f"{owner.name}'s pending attack.]")
                return self._answer_attack_menu(owner, menu, int(arg))
            return None   # no attack menu pending: normal declaration path

        # v2.8.1.1 P0: natural pickup aliases. An item transfer is engine
        # truth — the model must never narrate a pickup the engine skipped.
        if cmd in ("grab", "collect", "pocket", "snatch", "pickup") \
                or t.startswith("pick up"):
            if " and " in t or " then " in t or ", " in t or ";" in t:
                return None   # compound: the adjudicator sequences it
            if t.startswith("pick up"):
                parg = text.strip()[len("pick up"):].strip()
            else:
                parg = arg
            if not parg:
                return self._meta_command(char, "take")
            if parg.isdigit():
                pick = self._menu_pick(char, "take", int(parg))
                inst = self.item_instances.get(pick) if pick else None
                char.extra.pop("_last_menu", None)   # answered: consumed
                if inst is None:
                    print(f"  [No selection {parg} — list them again with 'take'.]")
                    return True
                return self._meta_command(char, f"take {inst.name}")
            return self._meta_command(char, f"take {parg}")

        # v2.8.1.1 P1: 'unlock <thing> [with <item>]' and 'use <item> on <thing>'
        if cmd == "unlock":
            m = re.match(r"(.+?)\s+with\s+.+$", arg, re.I)
            target_arg = (m.group(1) if m else arg).strip()
            if not target_arg:
                print("  [Unlock what?]")
                return True
            return self._meta_command(char, f"open {target_arg}")
        if cmd == "use" and re.search(r"\s+on\s+", arg, re.I):
            parts = re.split(r"\s+on\s+", arg, maxsplit=1, flags=re.I)
            if len(parts) == 2 and parts[1].strip():
                return self._meta_command(char, f"open {parts[1].strip()}")

        # unequip with optional target, plus natural aliases
        if cmd == "unequip" or t.startswith("put away") or cmd == "lower":
            if t.startswith("put away"):
                return self._do_unequip(char, text.strip()[len("put away"):].strip())
            return self._do_unequip(char, arg)

        # read <document> — readable items, carried or visible in the room
        if cmd == "read":
            docs = self._readable_things(char)
            if arg:
                inst = None
                if arg.isdigit():
                    pick = self._menu_pick(char, "read", int(arg))
                    inst = self.item_instances.get(pick) if pick else None
                    char.extra.pop("_last_menu", None)   # answered: consumed
                if inst is None:
                    low = _ARTICLE.sub("", arg.lower().strip())
                    inst = next((d for d in docs
                                 if d.name.lower() == low or low in d.name.lower()),
                                None)
                if inst is None:
                    print(f"  [No '{arg}' to read here.]")
                    return True
                self._print_read(char, inst)
                return True
            if not docs:
                print("  [Nothing to read here.]")
                return True
            if len(docs) == 1:
                self._print_read(char, docs[0])
                return True
            self._store_menu(char, "read", [d.id for d in docs])
            self._print_numbered([d.name for d in docs],
                                 "Read which? e.g. 'read 1'")
            return True

        # bare 'enter' / 'go' / 'go to' and numbered exit selection
        if (cmd in ("enter", "go")
                and (t in ("enter", "go", "go to") or arg.isdigit())):
            if arg.isdigit():
                # Explicit numbered form ('enter 2'): this MAY answer another
                # player's pending enter — one of the two allowed cross-player
                # routings (v2.8.1.7 P0-3, v2.8.1.x P0-2).
                owner, menu, routed = self._pending_menu(char)
                if routed and (menu or {}).get("kind") == "enter":
                    menu["answered_by"] = char.id
                    print(f"  [menu: {char.name} answered '{cmd} {arg}' for "
                          f"{owner.name}'s pending enter.]")
                exits = room_view.visible_exits(self.locations, owner.location,
                                                self.world_objects)
                pick = self._menu_pick(owner, "enter", int(arg))
                owner.extra.pop("_last_menu", None)   # answered: consumed
                if pick not in {e["id"] for e in exits}:
                    print(f"  [No exit {arg} — list them again with 'enter'.]")
                    return True
                self._meta_move(owner, pick)
                return True
            # v2.8.1.x P0-2: a BARE 'enter' is this player's own command. It
            # never answers — and never even lists — another player's pending
            # menu (field: Patrick's fresh 'enter' was eaten by Jack's stale
            # menu and moved Jack back out of the Study).
            exits = room_view.visible_exits(self.locations, char.location,
                                            self.world_objects)
            if not exits:
                print("  [No visible exits from here.]")
                return True
            if len(exits) == 1:
                self._meta_move(char, exits[0]["id"])
                return True
            self._store_menu(char, "enter", [e["id"] for e in exits])
            self._print_numbered(
                [e["name"] + (f" [{e['state']}]" if e["state"] != "open" else "")
                 for e in exits],
                "Enter which? e.g. 'enter 1'")
            return True

        # numbered selection for item commands
        if cmd in ("take", "equip", "drop", "reload", "open", "use") and arg.isdigit():
            owner, menu, routed = self._pending_menu(char)
            if routed and (menu or {}).get("kind"):
                menu["answered_by"] = char.id
                print(f"  [menu: {char.name} answered '{cmd} {arg}' for "
                      f"{owner.name}'s pending {menu.get('kind')}.]")
            pick = self._menu_pick(owner, cmd, int(arg))
            thing = self._resolve_menu_thing(owner, cmd, pick)
            owner.extra.pop("_last_menu", None)   # answered: consumed
            if thing is None:
                print(f"  [No selection {arg} — list them again with '{cmd}'.]")
                return True
            return self._meta_command(owner, f"{cmd} {thing.name}")

        # bare item commands: one target -> use it; many -> list; none -> say so
        if cmd in ("take", "equip", "drop", "reload", "open", "use") and not arg:
            if cmd == "take":
                pool, empty = self._visible_room_items(char), "Nothing here to take."
            elif cmd in ("equip", "drop", "use"):
                pool, empty = self._carried_items(char), \
                    f"{char.name} isn't carrying anything."
            elif cmd == "reload":
                pool = [i for i in self._carried_items(char)
                        if getattr(self.item_templates.get(i.template_id),
                                   "ammo_capacity", None) is not None]
                empty = "No carried weapon takes ammunition."
            else:
                pool, empty = self._openable_things(char), "Nothing here to open."
            if not pool:
                print(f"  [{empty}]")
                return True
            if len(pool) == 1:
                return self._meta_command(char, f"{cmd} {pool[0].name}")
            self._store_menu(char, cmd, [x.id for x in pool])
            self._print_numbered([self._show_item(x) for x in pool],
                                 f"{cmd.capitalize()} which? e.g. '{cmd} 1'")
            return True

        # 'use <room item>' — suggest the right verbs instead of failing blind
        if cmd == "use" and arg:
            if self._find_carried_item(char, arg) is None:
                low = arg.lower()
                room_inst = next((i for i in self._visible_room_items(char)
                                  if i.name.lower() == low or low in i.name.lower()),
                                 None)
                if room_inst is not None:
                    opts = [f"take {room_inst.name}"]
                    if room_inst in self._readable_things(char):
                        opts.append(f"read {room_inst.name}")
                    opts.append(f"look at {room_inst.name}")
                    print(f"  [The {room_inst.name} is right there — try "
                          + ", ".join(f"'{o}'" for o in opts) + ".]")
                    return True
            return None   # normal use dispatch

        # bare 'look at' / 'examine', and numbered picks of notable things
        look_bare = t in ("look at", "examine")
        look_pick = (cmd == "examine" and arg.isdigit()) or \
                    (cmd == "look" and arg.lower().startswith("at ")
                     and arg[3:].strip().isdigit())
        if look_bare or look_pick:
            pool = self._notable_things(char)
            if look_pick:
                n = int(arg) if cmd == "examine" else int(arg[3:].strip())
                pick = self._menu_pick(char, "look", n)
                char.extra.pop("_last_menu", None)   # answered: consumed
                thing = next((x for x in pool if x.id == pick), None)
                if thing is None:
                    print(f"  [No selection {n} — list them again with 'examine'.]")
                    return True
                return self._meta_command(char, f"examine {thing.name}")
            if not pool:
                print("  [Nothing particular here — try 'observe'.]")
                return True
            if len(pool) == 1:
                return self._meta_command(char, f"examine {pool[0].name}")
            self._store_menu(char, "look", [x.id for x in pool])
            self._print_numbered(
                [x.name for x in pool],
                "Examine which? e.g. 'examine 1'")
            return True

        # bare 'give' lists pockets; 'give 1 to <name>' selects
        if cmd == "give":
            if not arg:
                pool = self._carried_items(char)
                if not pool:
                    print(f"  [{char.name} isn't carrying anything to give.]")
                    return True
                people = [c.name for c in self.characters.values()
                          if c.id != char.id and c.location == char.location]
                self._store_menu(char, "give", [x.id for x in pool])
                hint = (f"Give what? e.g. 'give 1 to {people[0]}'"
                        if people else "Give what? e.g. 'give 1 to <name>'")
                self._print_numbered([x.name for x in pool], hint)
                return True
            mnum = re.match(r"(\d+)\s+(to\s+.+)", arg, re.I)
            if mnum:
                pick = self._menu_pick(char, "give", int(mnum.group(1)))
                inst = self.item_instances.get(pick) if pick else None
                char.extra.pop("_last_menu", None)   # answered: consumed
                if inst is None:
                    print(f"  [No selection {mnum.group(1)} — list them again with 'give'.]")
                    return True
                return self._meta_command(char, f"give {inst.name} {mnum.group(2)}")
            return None

        return None

    def _meta_command(self, char: Character, text: str) -> bool:
        """System-channel commands typed at the declaration prompt.

        These are handled by the engine and never reach the narrative. Returns
        True when the input was consumed as a command (even a failed one),
        False when it's a plain declaration and should flow to the turn.
        """
        t = text.strip().lower()
        if not t:
            return False

        # v2.8.1.1: natural-argument normalization runs before dispatch.
        norm = self._normalize_command(char, text)
        if norm is not None:
            return norm

        if t in ("inv", "inventory"):
            lines = [f"  [{char.name} — inventory]"]
            if char.equipped_item_id:
                lines.append(f"    (in hand) {self._show_item(self.item_instances.get(char.equipped_item_id))}")
            carried = [iid for iid in char.inventory if iid != char.equipped_item_id]
            if carried:
                for iid in carried:
                    lines.append(f"    {self._show_item(self.item_instances.get(iid))}")
            else:
                lines.append("    (nothing else)")
            print("\n".join(lines))
            return True

        if t.startswith("equip"):
            arg = text.strip()[len("equip"):].strip()
            if not arg:
                print("  [Equip what? Try 'inventory'.]")
                return True
            inst = self._find_carried_item(char, arg)
            if inst is None:
                print(f"  [{char.name} isn't carrying a '{arg}'.]")
                return True
            if char.equipped_item_id and char.equipped_item_id not in char.inventory:
                char.inventory.append(char.equipped_item_id)
            if inst.id not in char.inventory:
                char.inventory.append(inst.id)
            char.equipped_item_id = inst.id
            char.refresh_weapon_view()
            self._registry_audit(char, after="equip")
            print(f"  [{char.name} readies the {inst.name}.]")
            return True

        if t.startswith("take "):
            arg = text.strip()[len("take "):].strip()
            inst = self._find_room_item(char, arg)
            if inst is None:
                print(f"  [No '{arg}' here to take.]")
                return True
            inst.owner_id = char.id
            inst.location_id = None
            if inst.id not in char.inventory:
                char.inventory.append(inst.id)
            self._registry_audit(char, after="take")
            print(f"  [{char.name} takes the {inst.name}.]")
            return True

        if t.startswith("drop "):
            arg = text.strip()[len("drop "):].strip()
            inst = self._find_carried_item(char, arg)
            if inst is None:
                print(f"  [{char.name} isn't carrying a '{arg}'.]")
                return True
            if char.equipped_item_id == inst.id:
                print(f"  [{char.name} must unequip the {inst.name} before dropping it.]")
                return True
            if inst.id in char.inventory:
                char.inventory.remove(inst.id)
            inst.owner_id = None
            inst.location_id = char.location
            self._registry_audit(char, after="drop")
            print(f"  [{char.name} drops the {inst.name}.]")
            return True

        if t.startswith("give "):
            m = re.match(r"give\s+(.+?)\s+to\s+(.+)", text.strip(), re.I)
            if not m:
                print("  [Usage: give <item> to <character>]")
                return True
            item_arg, target_name = m.group(1).strip(), m.group(2).strip()
            inst = self._find_carried_item(char, item_arg)
            if inst is None:
                print(f"  [{char.name} isn't carrying a '{item_arg}'.]")
                return True
            recipient = self._find_character_in_room(char, target_name)
            if recipient is None:
                print(f"  [No one named '{target_name}' here.]")
                return True
            if char.equipped_item_id == inst.id:
                print(f"  [{char.name} must unequip the {inst.name} before giving it.]")
                return True
            if inst.id in char.inventory:
                char.inventory.remove(inst.id)
            inst.owner_id = recipient.id
            inst.location_id = None
            if inst.id not in recipient.inventory:
                recipient.inventory.append(inst.id)
            self._registry_audit(char, after="give")
            self._registry_audit(recipient, after="give")
            print(f"  [{char.name} gives the {inst.name} to {recipient.name}.]")
            return True

        if t.startswith("reload "):
            arg = text.strip()[len("reload "):].strip()
            inst = self._find_carried_item(char, arg)
            if inst is None:
                print(f"  [{char.name} isn't carrying a '{arg}'.]")
                return True
            tmpl = self.item_templates.get(inst.template_id)
            if tmpl is None or tmpl.ammo_capacity is None:
                print(f"  [The {inst.name} doesn't take ammunition.]")
                return True
            ammo = next((iid for iid in char.inventory
                         if getattr(self.item_instances.get(iid), "item_type", None) == "ammo"), None)
            if ammo is None:
                print(f"  [{char.name} has no ammunition to reload with.]")
                return True
            ammo_inst = self.item_instances[ammo]
            ammo_tmpl = self.item_templates.get(ammo_inst.template_id)
            # v2.8.0.1: ammunition must match the weapon (generic ammo fits any firearm).
            weapon_ammo_type = getattr(tmpl, "ammo_type", None)
            ammo_ammo_type = getattr(ammo_tmpl, "ammo_type", "generic") if ammo_tmpl else "generic"
            if weapon_ammo_type and ammo_ammo_type != "generic" and ammo_ammo_type != weapon_ammo_type:
                print(f"  [The {ammo_inst.name} does not fit the {inst.name}.]")
                return True
            needed = tmpl.ammo_capacity - (inst.ammo or 0)
            if needed <= 0:
                print(f"  [The {inst.name} is already full.]")
                return True
            available = ammo_inst.quantity if (ammo_tmpl and ammo_tmpl.stackable) else 1
            load = min(needed, available)
            inst.ammo = (inst.ammo or 0) + load
            if ammo_tmpl is not None and ammo_tmpl.stackable:
                ammo_inst.quantity -= load
                if ammo_inst.quantity <= 0:
                    char.inventory.remove(ammo)
                    del self.item_instances[ammo]
            else:
                char.inventory.remove(ammo)
                del self.item_instances[ammo]
            if char.equipped_item_id == inst.id:
                char.refresh_weapon_view()
            print(f"  [{char.name} reloads the {inst.name}.]")
            return True

        if t.startswith("open "):
            arg = text.strip()[len("open "):].strip()
            target = self._find_room_object(char, arg)
            if target is None:
                # Also allow opening container items in the room.
                target = self._find_room_item(char, arg)
            if target is None:
                # v2.8.1.1: the door you just walked through is not "not
                # here" — it lives on the other side of the exit you used.
                target = self._find_linked_door_across_exits(char, arg)
                if target is not None and target.state == "open":
                    print(f"  [The {target.name} is already open, behind you.]")
                    return True
            if target is None:
                print(f"  [No '{arg}' here to open.]")
                return True
            if target.state == "open":
                print(f"  [The {target.name} is already open.]")
                return True
            if getattr(target, "properties", {}).get("locked"):
                key_id = target.properties.get("key_id")
                # v2.8.1.1 P0: never dereference a {} fallback — an unresolved
                # inventory id must skip, not crash (field: roster legacy
                # string entries made 'open door' raise AttributeError).
                has_key = any(
                    getattr(self.item_instances.get(iid), "template_id", None) == key_id
                    for iid in char.inventory)
                if not has_key:
                    print(f"  [The {target.name} is locked.]")
                    return True
                # v2.8.1.x: the key did its work — the object's truth must
                # not keep saying locked=True after it opens.
                target.properties["locked"] = False
            target.state = "open"
            self._sync_exits_for_object(target)
            self._registry_audit(char, after=f"open {target.name}")
            print(f"  [{char.name} opens the {target.name}.]")
            return True

        # v2.8.1: local observation — deterministic room view, never the LLM.
        if t in ("observe", "look", "look around", "examine room",
                 "examine the room", "look at the room", "l"):
            self._cmd_observe(char)
            return True

        if t.startswith("look at ") or t.startswith("examine "):
            if t.startswith("look at "):
                arg = text.strip()[len("look at "):].strip()
            else:
                arg = text.strip()[len("examine "):].strip()
            # search inventory, room items, world objects, then people
            target = self._find_carried_item(char, arg)
            if target is None:
                target = self._find_room_item(char, arg)
            if target is None:
                target = self._find_room_object(char, arg)
            if target is None:
                target = self._find_character_in_room(char, arg)
            if target is None:
                print(f"  [No '{arg}' here to examine.]")
                return True
            desc = getattr(target, "description", "") or ""
            print(f"  [{self._show_item(target)}]")
            if desc:
                print(f"    {desc}")
            return True

        if t.startswith("use "):
            arg = text.strip()[len("use "):].strip()
            inst = self._find_carried_item(char, arg)
            if inst is None:
                print(f"  [{char.name} isn't carrying a '{arg}'.]")
                return True
            if inst.item_type == "light_source":
                on = not inst.state.get("on", False)
                inst.state["on"] = on
                print(f"  [{char.name} turns the {inst.name} {'on' if on else 'off'}.]")
                return True
            if inst.item_type == "consumable":
                inst.quantity -= 1
                if inst.quantity <= 0:
                    char.inventory.remove(inst.id)
                    del self.item_instances[inst.id]
                    print(f"  [{char.name} uses the last of the {inst.name}.]")
                else:
                    print(f"  [{char.name} uses the {inst.name}. The Keeper will narrate the effect.]")
                return True
            if inst.item_type == "tool" and "lockpicking" in inst.tags:
                print(f"  [Use the {inst.name} by declaring what lock you are working on.]")
                return True
            if inst.item_type == "ammo":
                print(f"  [Use 'reload <weapon>' to load ammunition.]")
                return True
            print(f"  [{char.name} uses the {inst.name}. The Keeper will resolve the effect.]")
            return True

        if t in ("help", "list", "?"):
            print("""Available commands:
  inventory / inv            what you are carrying
  equip <item>               ready a carried weapon or tool
  unequip                    put away whatever is in your hand
  take <item>                pick up an item in the room
  drop <item>                place an item on the ground
  give <item> to <name>      hand an item to another investigator
  reload <weapon>            reload a firearm from carried ammo
  observe / look / look around   see the room again (no LLM, no turn used)
  go to / enter <room>           move through a visible exit (no LLM when ordinary)
  leave / back / go back / return   retrace your last step ('exit' quits the game)
  enter / take / equip / open    bare forms list what you can pick; 'take 1' selects
  read <document>                read a letter, ledger, or notebook
  open <container>               open a container or door
  look at / examine <thing>      inspect an item, object, or detail
  use <item>                     use an item in a generic way
  close distance                 move within striking reach of someone here
  --- the turn contract: one declaration per investigator per turn ---
  <anything else>              your action for the turn (resolves as one party turn)
  pass / wait                    take no action this turn (blank Enter works too)
  done / resolve                 resolve the declared batch now; anyone who has
                                 not declared yet is treated as passing
  end / end turn                 end the party turn early; with no declarations
                                 this lets time pass locally (no LLM, no cost)
  help / list                    show this command list
  quit / exit / save             save and leave the game""")
            return True

        return False

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

    def build_prompt_sections(self, declarations: Dict[str, str],
                              dice_results: dict):
        """The turn prompt as Governor-trimmable sections (v2.8.1.6).

        Same content as the legacy build_prompt, but structured so the
        Latency Governor can measure each bucket, slim the room view, and
        drop low-priority sections when a tier cap bites. build_prompt()
        below joins them unchanged — mock mode and prompt-content tests see
        the identical text as before."""
        mode = self.mode_selector.select_mode(
            list(self.characters.values()), declarations, scene_tension=0)
        # v2.8.1.x party truth: a party can be split across rooms. Anyone in
        # the scene OR in a declaring player's room is active; nobody who is
        # acting this turn may be filed as off-screen.
        active_rooms = {self.current_scene}
        for cid in declarations:
            ch = self.characters.get(cid)
            if ch is not None:
                active_rooms.add(ch.location)
        active = [c for c in self.characters.values() if c.location in active_rooms]
        inactive = [c for c in self.characters.values() if c.location not in active_rooms]
        scene = self.locations.get(self.current_scene)
        # v2.8.1: the model sees the deterministic room view — visible exits
        # (hidden exits never reach the prompt), object state, visible items,
        # and who is actually present with what they have readied.
        exits = {e["id"]: e["name"] for e in room_view.visible_exits(
            self.locations, self.current_scene, self.world_objects)}
        view = room_view.build_room_view(self)
        # v2.8.1.1 first-visit continuity: the model must know whether each
        # acting character has PERSONALLY seen this room before, or it writes
        # 'back'/'still'/'where you left it' into a room nobody has visited.
        view["visits"] = {
            c.id: {
                "count": self.visit_counts.get(c.id, {}).get(self.current_scene, 0),
                "seen_before": self.current_scene in self.visited.get(c.id, set()),
            }
            for c in self.characters.values() if c.char_type == "player"
        }
        # v2.7.0 latency diet: llm.compact_prompt drops pretty-print indent
        # and separator padding from every JSON block. Tokens are latency and
        # money; the model reads compact JSON just as well. The mock client
        # parses both forms (pinned by test_engine).
        compact = bool(self.config.get("llm", {}).get("compact_prompt", False))

        def _jd(obj):
            if compact:
                return json.dumps(obj, separators=(",", ":"))
            return json.dumps(obj, indent=2)

        view_slim = {k: v for k, v in view.items() if k != "details"}
        io_hint = len(_jd({"items": view.get("items"),
                           "objects": view.get("objects")}))
        # v2.8.1.x party truth: where every investigator IS, in engine terms,
        # so narration can never lose track of a split party.
        party_locations = []
        for c in self.characters.values():
            if c.char_type != "player":
                continue
            c_loc = self.locations.get(c.location)
            party_locations.append({
                "id": c.id, "name": c.name,
                "location": c.location,
                "room": c_loc.name if c_loc else c.location,
                "with": [o.name for o in self.characters.values()
                         if o.id != c.id and o.char_type == "player"
                         and o.location == c.location],
            })
        sections = [
            {"key": "scenario", "bucket": "scenario",
             "text": f"SCENARIO: {self.scenario_title} — {self.scenario_tone}"},
            {"key": "scene_core", "bucket": "scene",
             "text": (f"TURN {self.turn}\nMODE: {mode.value}\n"
                      f"CURRENT SCENE: {self.current_scene} "
                      f"({scene.name if scene else 'unknown'})\n"
                      f"EXITS: {_jd(exits)}")},
            {"key": "room", "bucket": "scene",
             "text": f"ROOM VIEW:\n{_jd(view)}",
             "slim": f"ROOM VIEW:\n{_jd(view_slim)}"},
            {"key": "characters_active", "bucket": "characters",
             "text": "ACTIVE CHARACTERS:\n"
                     + _jd([c.to_active_format() for c in active[:self.max_active]])},
            {"key": "party_locations", "bucket": "characters",
             "text": "PARTY LOCATIONS (engine truth — where each investigator "
                     "is standing THIS turn, and which other investigators "
                     "share that room):\n" + _jd(party_locations)},
            {"key": "characters_offscreen", "bucket": "characters",
             "droppable": True,
             "text": "OFF-SCREEN CHARACTERS:\n"
                     + _jd([c.to_summary_format() for c in inactive[:8]])},
            {"key": "declarations", "bucket": "adjudication",
             "text": f"PLAYER DECLARATIONS:\n{_jd(declarations)}"},
            {"key": "dice", "bucket": "adjudication",
             "text": f"DICE RESULTS:\n{_jd(dice_results)}"},
            {"key": "fronts_plot", "bucket": "fronts/plot", "droppable": True,
             "text": (f"FRONTS: {_jd({k: v.get('clock', 0) for k, v in self.fronts.items()})}\n"
                      f"PLOT POINTS: {_jd(self.plot_points)}")},
            {"key": "items_objects_hint", "bucket": "items/objects",
             "text": "", "telemetry_chars": io_hint},
        ]
        # v2.8.1.x split-party truth: a declaring player acting in a room
        # other than the current scene still gets that room's deterministic
        # view, or the model narrates their surroundings from memory.
        seen_rooms = {self.current_scene}
        for cid in declarations:
            ch = self.characters.get(cid)
            if ch is None or ch.location in seen_rooms:
                continue
            seen_rooms.add(ch.location)
            extra = room_view.build_room_view(self, loc_id=ch.location)
            extra_slim = {k: v for k, v in extra.items() if k != "details"}
            sections.append({
                "key": f"room_view_{ch.location}", "bucket": "scene",
                "droppable": True,
                "text": (f"ROOM VIEW ({ch.location} — where {ch.name} is "
                         f"acting):\n{_jd(extra)}"),
                "slim": f"ROOM VIEW ({ch.location}):\n{_jd(extra_slim)}"})
        if self._movement_events:
            # Engine-resolved entries the model must narrate, not decide.
            sections.append({
                "key": "movement", "bucket": "adjudication",
                "text": (
                    "MOVEMENT EVENTS (engine-resolved; movement_completed=true — "
                    "the actor is INSIDE destination_location NOW. Narrate the "
                    "TRANSITION INTO the destination: the crossing, the door or "
                    "unlock if one happened, then what greets them — and END with "
                    "the actor inside destination_location. Never describe "
                    "origin_location as the actor's current location. The "
                    "destination's occupants, items, and room state are in "
                    "destination_room_view — use it, not your memory of the "
                    f"origin):\n{_jd(self._movement_events)}")})
        sections.append({"key": "task", "bucket": "other",
                         "text": "NARRATE THIS TURN."})
        return sections, mode

    def build_prompt(self, declarations: Dict[str, str], dice_results: dict):
        sections, mode = self.build_prompt_sections(declarations, dice_results)
        return "\n".join(s["text"] for s in sections if s["text"]), mode

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
        """Degraded option 3: the dice speak for themselves — one plain
        local outcome, no narration, no LLM, no cost."""
        lines = ["(The Keeper is voiceless — the engine reports plainly.)"]
        for cid, res in (dice_results or {}).items():
            name = self.characters[cid].name if cid in self.characters else cid
            skill = str(res.get("skill", "Roll")).replace("_", " ")
            roll, target, level = res.get("roll"), res.get("target"), res.get("level")
            if roll is not None and target is not None and level is not None:
                line = f"{name} — {skill} {target}%: rolled {roll} — {level}."
                if res.get("object_result"):
                    line += f" {res['object_result']}"
                lines.append(line)
        if len(lines) == 1:
            lines.append("Nothing stirred.")
        return {
            "mode": mode.value if hasattr(mode, "value") else str(mode),
            "narration": "\n".join(lines),
            "private_narrations": {},
            "state_delta": {},
            "required_actions": "What do you do?",
            "dice_requests": [],
            "mode_switch": None,
        }

    # ------------------------------------- v2.8.1.x P0-1 validation retry
    def _narration_validation_retry(self, violations, mode, declarations,
                                    dice_results, acting_ids, plan,
                                    llm_timing, turn_context):
        """ONE compact correction attempt for a rejected narration.

        Carries: COMPACT_SYSTEM_PROMPT, the compact outcome packet, the
        validator violations, and a compact correction instruction — with the
        provider-aware compact budget, the compact deadline, no strict-retry
        ladder, and no second compact retry (CallPlan.for_validation_retry).
        Returns the recovered result dict, or None on any failure (timeout,
        invalid JSON, empty, still violating) — the caller then falls back
        to the local outcome and NEVER reruns the full prompt."""
        from src.latency_governor import COMPACT_SYSTEM_PROMPT
        compact = self.governor.build_compact_prompt(
            self, mode, declarations, dice_results)
        # v2.8.1.x: hand the retry the scene's engine truth (NPC states,
        # room objects) so it verifies instead of guessing.
        packet = self._validation_packet()
        if packet["npcs"] or packet["room_objects"]:
            lines = ["\n\nSCENE STATE (engine truth — verify, do not guess):"]
            for st in packet["npcs"].values():
                lines.append(
                    f"  {st['name']}: "
                    f"{'conscious' if st['conscious'] else 'DOWN'}, "
                    f"{st['hp_band']}, bleeding={st['bleeding']}, "
                    f"position={st['position']}")
            if packet["room_objects"]:
                lines.append("  objects in this room: "
                             + "; ".join(packet["room_objects"]))
            compact += "\n".join(lines)
        correction = (
            "\n\nCORRECTION: the previous narration was rejected because it "
            "contradicted engine truth: " + "; ".join(violations) + ". "
            "Rewrite the narration to describe ONLY the outcomes in this "
            "packet. NPCs may not write marks, move tracked items, open or "
            "close tracked doors, change object state, reveal hidden clues, "
            "or move between rooms unless this packet says so; first-time "
            "visitors have no memory of this room; no injuries, positions, "
            "countdowns, or scenario facts exist beyond this packet; and use "
            "player-facing names, never internal ids.")
        cplan = plan.for_validation_retry() if plan is not None else None
        cctx = dict(turn_context or {})
        cctx["prompt_tier"] = "narration_validation_retry"
        try:
            try:
                recovered = self.gemini.query(
                    COMPACT_SYSTEM_PROMPT, compact + correction,
                    timing=llm_timing, context=cctx,
                    plan=cplan, compact_prompt=None)
            except TypeError as te:
                # Test stubs may not accept the timing/context kwargs.
                if "unexpected keyword argument" not in str(te):
                    raise
                recovered = self.gemini.query(COMPACT_SYSTEM_PROMPT,
                                              compact + correction)
        except Exception:
            return None
        if not isinstance(recovered, dict):
            return None
        n2 = str(recovered.get("narration", ""))
        if not n2.strip():
            return None
        v2 = self._validate_narration(n2, recovered, dice_results, acting_ids)
        if v2:
            if self.debug:
                print("[narration validator: compact retry unresolved: "
                      + "; ".join(v2) + " — using local outcome]")
            return None
        return recovered

    def _log_validation_fallback(self, turn_context):
        """Telemetry category 'narration_validation_local_fallback': the
        compact correction failed and the engine reported plainly — zero
        provider cost, recorded so --report can price it (v2.8.1.x P0-1)."""
        if self.mock or getattr(self.gemini, "is_human", False):
            return
        try:
            from datetime import datetime, timezone
            from src import latency as _lat
            row = {
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "version": _lat.project_version(),
                "commit": _lat.git_commit(),
                "provider": getattr(self.gemini, "provider", "unknown"),
                "model": getattr(self.gemini, "default_model", "unknown"),
                "tier": "local",
                "attempt": "narration_validation_local_fallback",
                "retry": 0,
                "stage": "local-fallback",
                "budget": 0, "prompt_chars": 0, "response_chars": 0,
                "seconds": 0.0, "ok": True,
            }
            for k in ("resolution_mode", "turn", "scenario", "source"):
                if k in (turn_context or {}):
                    row[k] = turn_context[k]
            _lat.write_timing_row(
                os.path.join("logs", "llm_timing.jsonl"), row)
        except Exception:
            pass

    def _heavy_trigger(self, mode, declarations: Dict[str, str]) -> bool:
        """v2.8.1.3: what actually earns the heavy (k3) tier.

        Heavy is reserved for CINEMATIC mode, Mythos/creature scenes, and
        fronts sitting on a trigger threshold. Routine social threats and
        combat against ordinary NPCs stay on the default model."""
        if mode == ResolutionMode.CINEMATIC:
            return True
        scene = self.locations.get(self.current_scene)
        if scene is not None and set(scene.tags) & {"mythos", "creature"}:
            return True
        for c in self.characters.values():
            if c.char_type == "player" or c.location != self.current_scene:
                continue
            nature = str(c.extra.get("nature", "")).lower()
            if nature in ("mythos", "creature", "monster", "elder"):
                return True
        for front in self.fronts.values():
            clock = front.get("clock", 0)
            if clock and any(isinstance(t, dict) and t.get("clock") == clock
                             for t in front.get("triggers", [])):
                return True
        return False

    def _validation_packet(self) -> dict:
        """Ground truth for narration validation (v2.8.1.x field fix).

        The current mechanical state of every NPC in the scene (conscious,
        hp band, bleeding, position, alert) plus the room's tracked objects,
        so the validator and the compact retry VERIFY against engine truth
        instead of guessing from prose."""
        npcs = {}
        for c in self.characters.values():
            if c.char_type == "player" or c.location != self.current_scene \
                    or c.extra.get("hidden"):
                continue
            max_hp = c.max_hp or 1
            hp = c.hp if c.hp is not None else max_hp
            band = ("unhurt" if hp >= max_hp else
                    "wounded" if hp > max_hp // 2 else
                    "badly wounded" if hp > 0 else "down")
            npcs[c.id] = {
                "name": c.name,
                "conscious": not (c.unconscious or c.dying),
                "hp_band": band,
                "bleeding": hp < max_hp,
                "position": c.position,
                "alerted": bool(getattr(c, "alerted", True)),
            }
        objects = [o.name for o in self.world_objects.values()
                   if o.location_id == self.current_scene
                   and o.state != "hidden"]
        return {"npcs": npcs, "room_objects": objects}

    @staticmethod
    def _state_claim_negated(text: str, start: int, end: int) -> bool:
        """Whether a matched state word sits in a negated window — then it
        references the current state rather than claiming a new one."""
        window = text[max(0, start - 70): end + 30]
        return bool(NARRATION_NEG_RE.search(window))

    @staticmethod
    def _object_noun_allowlisted(noun: str, allow_names) -> bool:
        """Whether an interactable noun names something the engine tracks
        in the room (objects, room items, people, their gear)."""
        for name in allow_names:
            low = name.lower()
            if noun in low:
                return True
            if noun in [w.rstrip("s") for w in low.split()]:
                return True
        return False

    def _validate_narration(self, narration: str, result: dict,
                            dice_results: dict, acting_ids=None) -> List[str]:
        """Flag narration the engine did not produce.

        v2.8.1.3 Part 7 (NPC world-changing actions), extended v2.8.1.7 P0-5:
        first-visit continuity, unsupported NPC mechanical state, and
        invented scenario facts. An action or state is legitimate only when
        the engine produced it: a dice result, a state_delta entry, a
        movement event, a forced move, or a real Character field."""
        text = (narration or "").lower()
        if not text:
            return []
        legit = set()
        for res in dice_results.values():
            if res.get("target_char"):
                legit.add(res["target_char"])
            fm = res.get("forced_move")
            if fm:
                legit.add(fm.get("npc"))
        delta = result.get("state_delta", {}) or {}
        legit.update((delta.get("characters") or {}).keys())
        for trans in (delta.get("scene_transitions") or []):
            if isinstance(trans, dict):
                legit.update(trans.keys())
        for ev in self._movement_events:
            legit.add(ev.get("character"))

        # packet support for NPC mechanical-state claims
        damaged, knocked = set(), set()
        for res in dice_results.values():
            tid = res.get("target_char")
            if not tid:
                continue
            if res.get("damage"):
                damaged.add(tid)
            notes = " ".join(res.get("notes") or []) + str(res.get("note", ""))
            if "knocked out" in notes or (
                    res.get("nonlethal")
                    and res.get("level") not in ("Failure", "Fumble")):
                knocked.add(tid)

        violations = []
        for npc in self.characters.values():
            if npc.char_type == "player":
                continue
            bits = [npc.id.replace("_", " ")] + \
                   [b for b in npc.name.lower().split()
                    if len(b) > 2 and b not in _NPC_NAME_NOISE]
            if not bits:
                continue
            name_re = "|".join(re.escape(b) for b in bits)
            mentioned = re.search(rf"\b(?:{name_re})\b", text)
            if npc.id not in legit and mentioned:
                for verb in WORLD_CHANGE_VERBS:
                    if re.search(rf"\b(?:{name_re})\b[^.]{{0,70}}\b{re.escape(verb)}\b",
                                 text):
                        violations.append(f"{npc.name} {verb}")
                        break
            if not mentioned:
                continue
            # v2.8.1.7 P0-5: NPC mechanical state needs packet support.
            # v2.8.1.x: ASSERTING a new state is rejected; REFERENCING the
            # current one is not. A state word in a negated window ('no
            # blood', 'not knocked out', 'doesn't fall') is a reference, and
            # so is a description consistent with the engine's own record
            # (a wounded NPC may be called bleeding).
            wounded = (npc.hp or 0) < (npc.max_hp or 0)
            state_rules = (
                (r"unconscious|barely conscious|knocked out|near death|"
                 r"barely alive|at death'?s door|\bdying\b",
                 "consciousness/death",
                 npc.unconscious or npc.dying or npc.id in knocked),
                (r"major wound", "major wound", npc.major_wound),
                (r"\bprone\b|\bpinned\b|on (?:his|her|their) back|"
                 r"flat on (?:his|her|their) back",
                 "position", npc.id in knocked),
                (r"broken (?:bone|arm|leg|jaw|nose|ribs)|"
                 r"shattered (?:bone|arm|leg|jaw)|catastrophically",
                 "broken bones", False),
                (r"bleeding|bloodied|gushing|\bblood\b",
                 "bleeding", npc.id in damaged or wounded),
                (r"before you (?:burst|arrived|came|got)|preexisting|"
                 r"already (?:wounded|bleeding|injured)",
                 "preexisting injury", False),
            )
            for pattern, label, supported in state_rules:
                m = re.search(pattern, text)
                if m and not supported and not self._state_claim_negated(
                        text, m.start(), m.end()):
                    violations.append(f"{npc.name} {label} (unsupported)")
                    break

        # v2.8.1.7 P0-5: first-visit continuity. An acting character who has
        # never seen this room cannot 'return' to it or recognize it.
        acting = acting_ids if acting_ids is not None else [
            c.id for c in self.characters.values() if c.char_type == "player"]
        first_timers = [cid for cid in acting
                        if self.visit_counts.get(cid, {}).get(
                            self.current_scene, 0) == 0
                        and self.current_scene
                        not in self.visited.get(cid, set())]
        if first_timers:
            for pattern in FIRST_VISIT_RES:
                m = pattern.search(text)
                if m:
                    violations.append(f"first-visit continuity: '{m.group(0)}'")
                    break

        # v2.8.1.7 P0-5: invented scenario facts — allowed only when an
        # engine trigger (front event, timeline) rode the packet.
        fact_support = any(t.startswith(("front-event:", "timeline:"))
                           for ev in self._movement_events
                           for t in ev.get("triggers", []))
        if not fact_support:
            for pattern in SCENARIO_FACT_RES:
                m = pattern.search(text)
                if m:
                    violations.append(f"invented scenario fact: '{m.group(0)}'")
                    break

        # v2.8.1.x P1-6: internal ids are not narration. Clue, front,
        # location, object, item-template, and NPC ids are engine handles —
        # the table hears player-facing names ('The Counting', never
        # 'the_counting'). Only snake_case ids are flagged: a plain-word id
        # is indistinguishable from prose.
        internal_ids = [c.get("id") for c in self.clues
                        if isinstance(c, dict)]
        internal_ids += list(self.fronts.keys())
        internal_ids += list(self.locations.keys())
        internal_ids += list(self.world_objects.keys())
        internal_ids += list(self.item_templates.keys())
        internal_ids += [c.id for c in self.characters.values()
                         if c.char_type != "player"]
        for iid in internal_ids:
            if not iid or "_" not in str(iid):
                continue
            if re.search(rf"\b{re.escape(str(iid).lower())}\b", text):
                violations.append(f"internal id in narration: '{iid}'")
                break

        # v2.8.1.x P1-7: unlock/key and door continuity. The movement packet
        # facts (key spent, way now open) may not be contradicted.
        for ev in self._movement_events:
            if ev.get("key_used") or ev.get("unlocked_with"):
                m = KEY_DENY_RE.search(text)
                if m:
                    violations.append(
                        f"key continuity: '{m.group(0)}' "
                        f"(the engine used the {ev.get('unlocked_with') or 'key'})")
                    break
        for ev in self._movement_events:
            if ev.get("movement_completed") or ev.get("door_open"):
                m = DOOR_STILL_LOCKED_RE.search(text)
                if m:
                    violations.append(
                        f"door continuity: '{m.group(0)}' "
                        "(the actor already passed through)")
                    break

        # v2.8.1.x: cross-room props. room_view owns where a tracked object
        # IS; narration may not place it in a room the engine does not track
        # it in (field: the knife 'clattered against the weapon racks' —
        # the racks are in the Testing Hall, not the Short Range).
        for obj in self.world_objects.values():
            if obj.location_id == self.current_scene or obj.state == "hidden":
                continue
            name = (obj.name or "").lower()
            if len(name) > 3 and re.search(rf"\b{re.escape(name)}\b", text):
                home = self.locations.get(obj.location_id)
                violations.append(
                    f"cross-room prop: '{obj.name}' is in "
                    f"{home.name if home else obj.location_id}, not here")
                break

        # v2.8.1.x: invented named physical objects. room_view is the
        # allowlist of what physically IS here; a newly introduced
        # interactable object (dummy, furniture, container) is a violation.
        # Atmospheric texture without interactable presence stays fine.
        allow = [o.name for o in self.world_objects.values()
                 if o.location_id == self.current_scene
                 and o.state != "hidden"]
        allow += [i.name for i in self.item_instances.values()
                  if i.location_id == self.current_scene
                  and i.owner_id is None and "hidden" not in i.tags]
        # Scenario-authored room text legitimates the props it describes
        # ('a cramped study. Every flat surface...') — those are not
        # invented, the author put them there.
        _loc = self.locations.get(self.current_scene)
        if _loc is not None:
            allow += [_loc.description, _loc.first_visit, _loc.revisit,
                      _loc.lighting]
            allow += list((_loc.details or {}).values())
        for c in self.characters.values():
            if c.location != self.current_scene:
                continue
            allow.append(c.name)
            gear = ([c.equipped_item_id] if c.equipped_item_id else []) \
                + list(c.inventory)
            for iid in gear:
                inst = self.item_instances.get(iid)
                if inst is not None:
                    allow.append(inst.name)
        for noun in INTERACTABLE_NOUNS:
            m = re.search(rf"\b(?:a|an|the)\s+(?:[a-z'-]+\s+){{0,3}}?"
                          rf"{noun}s?\b", text)
            if m and not self._object_noun_allowlisted(noun, allow):
                violations.append(
                    f"invented object: '{noun}' (not in the room)")
                break

        # v2.8.1.x: a resolved throw lands in the actor's room — narration
        # may not put the item somewhere else (packet facts are binding,
        # same rule as key/door continuity).
        for landed in getattr(self, "_landed_items", []):
            lname = (landed.get("name") or "").lower()
            if not lname or lname not in text:
                continue
            other_rooms = [(loc.name or "").lower()
                           for lid, loc in self.locations.items()
                           if lid != landed.get("room")
                           and len(loc.name or "") > 3]
            hit = next((r for r in other_rooms
                        for sent in re.split(r"[.!?]\s*", text)
                        if lname in sent
                        and re.search(rf"\b{re.escape(r)}\b", sent)), None)
            if hit:
                violations.append(
                    f"item placement: '{landed['name']}' landed in this "
                    f"room, not the {hit}")
                break
        return violations

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
    def save_state(self):
        # v2.8.1.x P0-2: pending menus are runtime-only — stripped from the
        # serialized state but kept LIVE for the next input (an attack-target
        # menu staged this turn must survive the save that ends it).
        stashed = {}
        for c in self.characters.values():
            m = c.extra.pop("_last_menu", None)
            if m is not None:
                stashed[c.id] = m
        try:
            state_mod.save_world(
                self.save_path,
                turn=self.turn, current_scene=self.current_scene,
                fronts=self.fronts, plot_points=self.plot_points,
                characters=self.characters, locations=self.locations,
                timeline=self.timeline, pending_rolls=self.pending_rolls,
                item_instances=self.item_instances,
                world_objects=self.world_objects,
                visited=self.visited,
                visit_counts=self.visit_counts,
                discovered_clues=self.discovered_clues,
            )
        finally:
            for cid, m in stashed.items():
                self.characters[cid].extra["_last_menu"] = m

    def load_state(self) -> bool:
        if not os.path.exists(self.save_path):
            return False
        with open(self.save_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        # v2.8.0: migrate v2.7.x saves into the item registry before parsing.
        items_mod.migrate_save_data(raw, self.item_templates)
        data = state_mod.load_world_from_dict(raw)
        self.turn = data.get("turn", 0)
        self.current_scene = data.get("current_scene", self.current_scene)
        self.fronts = data.get("fronts", self.fronts)
        self.plot_points = data.get("plot_points", [])
        self.timeline = data.get("timeline", self.timeline)
        self.pending_rolls = data.get("pending_rolls", [])
        self.characters = data.get("characters", {})
        # v2.8.1.x P0-2: pending menus are runtime-only — strip any that a
        # pre-hotfix save may still carry.
        for c in self.characters.values():
            c.extra.pop("_last_menu", None)
        self.locations = data.get("locations", self.locations)
        self.item_instances = data.get("item_instances", {})
        self.world_objects = data.get("world_objects", {})
        # v2.8.1: first-visit memory and clue-reveal stamps (absent on old saves)
        self.visited = {cid: set(locs) for cid, locs in data.get("visited", {}).items()}
        self.discovered_clues = set(data.get("discovered_clues", []))
        # v2.8.1.1: visit counts; older v2.8.1 saves derive count=1 per room.
        self.visit_counts = {
            cid: {loc: int(n) for loc, n in counts.items()}
            for cid, counts in data.get("visit_counts", {}).items()
        }
        for cid, locs in self.visited.items():
            counts = self.visit_counts.setdefault(cid, {})
            for loc in locs:
                counts.setdefault(loc, 1)
        items_mod.set_runtime_registry(self.item_instances)
        self.spatial = SpatialEngine(self.locations)
        self.combat = CombatEngine(self.spatial, self.dice)
        self.sanity = SanityEngine(self.dice, self.combat, self.config.get("sanity", {}))
        return True

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
                # All-pass accounting (v2.8.1.x field fix): 'Everyone passes'
                # is printed only for a genuine all-pass round — every
                # player explicitly passed, no local/meta command ran, no
                # menu is open, and nothing resolved this round.
                round_passes = 0
                round_activity = False
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
                # Surprise window closes: unaware NPCs sharing a room with a
                # player are alert from the next round onward.
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
