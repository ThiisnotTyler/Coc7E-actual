"""Narration validator (v2.8.1.3 Part 7 → v2.8.1.x) — decoupled from keeper.py.

The Truth Firewall's second wall: the StateDeltaValidator guards mechanical
state writes; THIS module guards the prose. Narration may describe only
what the engine produced — a dice result, a state_delta entry, a movement
event, a forced move, or a real Character field. ASSERTING something new
(an NPC state, an object, a scenario fact) is rejected; REFERENCING what
the table already treats as present is not.

Everything here is read-only against keeper state: characters, items,
rooms, movement events, dice results go in; a list of violation strings
comes out. Functions take `keeper` explicitly (the local_voice.py /
human_keeper.py pattern). CoCKeeper keeps one-line `_validate_narration`
and `_validation_packet` delegates for the call sites and the test surface
(test_engine references them ~45 times).
"""
import re
from typing import List

from src import items as items_mod

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
    r"\bthreaten\w*\b|\balmost\b|\bnearly\b|"
    r"\bstill standing\b|\bremain\w* standing\b|\bstay\w* standing\b|"
    r"\bon (?:his|her|their) feet\b")

# NPC name bits that never identify a specific person — 'the' would make
# every 'The X' NPC a mention in every sentence.
_NPC_NAME_NOISE = {"the", "a", "an", "mr", "mrs", "ms", "dr", "miss", "sir"}

# v2.8.1.x: the rules the validator enforces, told UP FRONT — the model
# kept breaking rules it had never been told (field: two voiceless combat
# turns, four violations in one compact retry). The packet block rides every
# turn prompt and the correction prompt; the short version stands in the
# system prompt. Both stay tiny on purpose (governor budgets).
NARRATION_RULES_PACKET = (
    "NARRATION RULES: narrate ONLY what the packet says happened — a miss "
    "or lost strike connects with nothing. Never change anyone's position "
    "or consciousness (prone, down, out cold) unless the packet reports "
    "it; pain prose is fine, new body states are not. No one drops, "
    "loses, grabs, or picks up a weapon or item unless the packet says "
    "so. Name weapons exactly as the packet does (a pump shotgun is never "
    "a rifle, no bolt exists). No mechanics in prose (HP, damage, rolls, "
    "success levels). No new named objects, monsters, countdowns, or "
    "events. Never propose 'position' in state_delta — engine-owned."
)
NARRATION_RULES_SYSTEM = (
    "NARRATION RULES: narrate ONLY the packet's outcomes. Never change "
    "positions, posture, consciousness, or held items unless the packet "
    "reports it; name weapons exactly as the packet does, kind included; "
    "no mechanics in prose (HP, damage, rolls, success levels); no new "
    "named objects, monsters, countdowns, or events; never propose "
    "'position' in state_delta — it is engine-owned."
)

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

# v2.8.1.x: mechanics quoted as mechanics — success-level names, HP
# figures, damage figures, roll values. Anchored to mechanic vocabulary so
# dates, ordinals, and ordinary counts ('two men', 'the third shelf')
# stay legal. The validation text is already lowercased.
_NUMWORDS = (r"(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|"
             r"eleven|twelve)")
_MECHANICS_QUOTE_RE = re.compile(
    r"\b(?:regular|hard|extreme|critical) success\b|"
    r"\bwith a critical\b|\bcritical hit\b|"
    + _NUMWORDS + r"\s+(?:hp|hit points?)\b|"
    r"\bhp\s+(?:drops?|falls?|down)\b|"
    r"\b\d+\s*(?:of|/)\s*\d+\s*(?:hp|hit points?)\b|"
    + _NUMWORDS + r"\s+(?:damage|points?)\b|"
    r"\b(?:deals?|takes?)\s+\d+\s+(?:damage|points?)\b|"
    r"\bfor\s+\d+\s+(?:damage|points?)\b|"
    r"\b(?:his|her|their)\s+\d{1,3}\s+(?:lands?|connects?|hits?|strikes?)\b|"
    r"\brolls?\s+(?:a\s+)?\d{1,3}\b|\brolled\s+\d{1,3}\b")


def validation_packet(keeper) -> dict:
    """Ground truth for narration validation (v2.8.1.x field fix).

    The current mechanical state of every NPC in the scene (conscious,
    hp band, bleeding, position, alert) plus the room's tracked objects,
    so the validator and the compact retry VERIFY against engine truth
    instead of guessing from prose."""
    npcs = {}
    for c in keeper.characters.values():
        if c.char_type == "player" or c.location != keeper.current_scene \
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
    objects = [o.name for o in keeper.world_objects.values()
               if o.location_id == keeper.current_scene
               and o.state != "hidden"]
    return {"npcs": npcs, "room_objects": objects}


def _state_claim_negated(text: str, start: int, end: int) -> bool:
    """Whether a matched state word sits in a negated window — then it
    references the current state rather than claiming a new one."""
    window = text[max(0, start - 70): end + 30]
    return bool(NARRATION_NEG_RE.search(window))


def _prop_connects_scene(keeper, obj_id) -> bool:
    """Whether a world object is a door/barrier on an exit touching the
    current scene (either side of the connection) — such a door is
    legitimately visible from here (v2.8.1.x cross-room prop exemption
    (b))."""
    scene = keeper.locations.get(keeper.current_scene)
    if scene is not None:
        for conn in scene.connections.values():
            if isinstance(conn, dict) and conn.get("object_id") == obj_id:
                return True
    for lid, loc in keeper.locations.items():
        if lid == keeper.current_scene:
            continue
        for dest_id, conn in loc.connections.items():
            if dest_id == keeper.current_scene and isinstance(conn, dict) \
                    and conn.get("object_id") == obj_id:
                return True
    return False


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


def validate_narration(keeper, narration: str, result: dict,
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
    for ev in keeper._movement_events:
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
    for npc in keeper.characters.values():
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
             r"barely alive|at death'?s door|\bdying\b|\bout cold\b|"
             r"consciousness (?:is )?(?:flickering|fading|slipping)",
             "consciousness/death",
             npc.unconscious or npc.dying or npc.id in knocked),
            (r"major wound", "major wound", npc.major_wound),
            # v2.8.1.x: down/position vocabulary widened (field: 'drops
            # to his knees before pitching face-down' with zero damage
            # dealt; 'knees buckle... goes down heavy onto the
            # padding... out of the fight' at 8/15 HP, standing).
            # Supported when the ENGINE downed them — damage this turn
            # that left them out, or a knockout packet.
            (r"\bprone\b|\bpinned\b|on (?:his|her|their) back|"
             r"flat on (?:his|her|their) back|"
             r"(?:drops?|falls?|sinks?) to (?:his|her|their) knees|"
             r"(?:his|her|their) knees (?:buckle|give)\w*|"
             r"(?:goes?|gets?|falls?) down (?:heavy|hard)?\s*"
             r"(?:onto|to) the (?:floor|ground|padding|matting|dirt)|"
             r"collapses?\b|crumples?\b|pitch\w* face-?down|topples?\b|"
             r"goes? down to the (?:floor|ground)|out of the fight",
             "position",
             npc.id in knocked or npc.unconscious or npc.dying),
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
            if m and not supported and not _state_claim_negated(
                    text, m.start(), m.end()):
                violations.append(f"{npc.name} {label} (unsupported)")
                break

    # v2.8.1.x: weapon-loss claims are engine events — the engine never
    # disarmed anyone (hook for the day a disarm maneuver lands), so a
    # weapon 'clattering from nerveless fingers' is always an assertion
    # without basis. Referencing a readied weapon stays legal.
    m = re.search(r"(?:drops?|loses?|fumbles?)\s+(?:\w+\s+){0,3}"
                  r"(?:weapon|gun|revolver|pistol|rifle|knife)\b|"
                  r"(?:weapon|gun|revolver|pistol|rifle|knife)\s+"
                  r"(?:clattering|flying)\s+from\b", text)
    if m and not _state_claim_negated(text, m.start(), m.end()):
        violations.append(f"disarm: '{m.group(0)}' (unsupported)")

    # v2.8.1.x: NPC item-possession claims. The item registry owns
    # where things ARE: a floor item stays on the floor unless the
    # engine hands it over (it never does today — hook for an engine
    # hand-off). Referencing a floor item without taking it, or an item
    # the NPC actually owns, stays legal.
    for inst in keeper.item_instances.values():
        if inst.location_id != keeper.current_scene \
                or inst.owner_id is not None or "hidden" in inst.tags:
            continue
        ibits = [b for b in inst.name.lower().split() if len(b) > 2]
        if not ibits:
            continue
        named = any(re.search(rf"\b{re.escape(b)}\b", text) for b in ibits)
        grab = None
        if named:
            bit_re = "|".join(re.escape(b) for b in ibits)
            grab = re.search(
                rf"\b(?:grabs?|yanks?|snatches?|brandishes?|picks?)\b"
                rf"[^.]{{0,60}}\b(?:{bit_re})\b|"
                rf"\b(?:{bit_re})\b[^.]{{0,60}}"
                rf"\b(?:grabs?|yanks?|snatches?|brandishes?|picks?)\b",
                text)
        if grab and not _state_claim_negated(
                text, grab.start(), grab.end()):
            violations.append(
                f"item possession: '{inst.name}' "
                "(the engine tracks it on the floor)")
            break
    if not any("item possession" in v for v in violations):
        landed = getattr(keeper, "_landed_items", [])
        if landed:
            grab = re.search(
                r"\b(?:grabs?|yanks?|snatches?|brandishes?|picks?)\s+"
                r"(?:\w+\s+){0,2}it\b", text)
            if grab and not _state_claim_negated(
                    text, grab.start(), grab.end()):
                violations.append(
                    f"item possession: '{landed[-1]['name']}' "
                    "(it landed in the room this turn; the engine "
                    "never handed it over)")

    # v2.8.1.x: PLAYER position is engine-owned exactly like NPC
    # position ('She sprawls... Jess lies exposed' with no engine
    # basis; widened with the same down-claim vocabulary as the NPC
    # rule). Negated/reference windows stay legal.
    m = re.search(r"\bsprawls?\b|\bis knocked down\b|"
                  r"\blies? (?:prone|exposed)\b|"
                  r"\bfalls? to (?:his|her|their) knees\b|"
                  r"(?:his|her|their) knees (?:buckle|give)\w*|"
                  r"(?:goes?|gets?|falls?) down (?:heavy|hard)?\s*"
                  r"(?:onto|to) the (?:floor|ground|padding|matting|dirt)|"
                  r"\bout of the fight\b|\bout cold\b|"
                  r"consciousness (?:is )?(?:flickering|fading|slipping)",
                  text)
    if m and not _state_claim_negated(text, m.start(), m.end()):
        violations.append(
            f"player position: '{m.group(0)}' (engine-owned)")

    # v2.8.1.x: never quote mechanics. Success-level names, HP figures,
    # damage figures, roll values ('Seven damage.', 'four HP', 'his 46
    # lands him a glancing blow') are engine truth, not prose. Wound
    # SEVERITY language and ordinary numbers stay legal.
    m = _MECHANICS_QUOTE_RE.search(text)
    if m:
        violations.append(
            f"mechanics quoted in narration: '{m.group(0)}'")

    # v2.8.1.7 P0-5: first-visit continuity. An acting character who has
    # never seen this room cannot 'return' to it or recognize it.
    acting = acting_ids if acting_ids is not None else [
        c.id for c in keeper.characters.values() if c.char_type == "player"]

    # v2.8.1.x: the packet weapon's KIND is engine truth. A shotgun is
    # never a 'rifle' and has no 'bolt'; a revolver is no 'automatic'
    # and has no 'slide'. Fires only on the acting character's OWN
    # equipped weapon — scenery references are untouched (the same
    # reference-vs-assertion doctrine as NPC states).
    for cid in acting:
        c = keeper.characters.get(cid)
        if c is None or not c.equipped_item_id:
            continue
        inst = keeper.item_instances.get(c.equipped_item_id)
        tmpl = keeper.item_templates.get(inst.template_id) if inst else None
        if tmpl is None:
            continue
        kind = items_mod.weapon_kind_label(tmpl, c.weapon)
        if kind.startswith("pump-action shotgun"):
            m = re.search(r"\brifle\b|\bbolt\b", text)
            if m:
                violations.append(
                    f"weapon kind: '{m.group(0)}' (the {inst.name} is a "
                    f"shotgun — never a rifle, no bolt)")
                break
        elif kind.startswith("revolver"):
            m = re.search(r"\bautomatic\b|\bslide\b", text)
            if m:
                violations.append(
                    f"weapon kind: '{m.group(0)}' (the {inst.name} is a "
                    f"revolver — no slide, not an automatic)")
                break

    first_timers = [cid for cid in acting
                    if keeper.visit_counts.get(cid, {}).get(
                        keeper.current_scene, 0) == 0
                    and keeper.current_scene
                    not in keeper.visited.get(cid, set())]
    if first_timers:
        for pattern in FIRST_VISIT_RES:
            m = pattern.search(text)
            if m:
                violations.append(f"first-visit continuity: '{m.group(0)}'")
                break

    # v2.8.1.7 P0-5: invented scenario facts — allowed only when an
    # engine trigger (front event, timeline) rode the packet.
    fact_support = any(t.startswith(("front-event:", "timeline:"))
                       for ev in keeper._movement_events
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
    internal_ids = [c.get("id") for c in keeper.clues
                    if isinstance(c, dict)]
    internal_ids += list(keeper.fronts.keys())
    internal_ids += list(keeper.locations.keys())
    internal_ids += list(keeper.world_objects.keys())
    internal_ids += list(keeper.item_templates.keys())
    internal_ids += [c.id for c in keeper.characters.values()
                     if c.char_type != "player"]
    for iid in internal_ids:
        if not iid or "_" not in str(iid):
            continue
        if re.search(rf"\b{re.escape(str(iid).lower())}\b", text):
            violations.append(f"internal id in narration: '{iid}'")
            break

    # v2.8.1.x P1-7: unlock/key and door continuity. The movement packet
    # facts (key spent, way now open) may not be contradicted.
    for ev in keeper._movement_events:
        if ev.get("key_used") or ev.get("unlocked_with"):
            m = KEY_DENY_RE.search(text)
            if m:
                violations.append(
                    f"key continuity: '{m.group(0)}' "
                    f"(the engine used the {ev.get('unlocked_with') or 'key'})")
                break
    for ev in keeper._movement_events:
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
    # Doctrine: PLACING an off-room prop in the current scene is a
    # violation; REFERENCING a door in its own doorway is not. Exempt:
    # (a) objects in this turn's movement events — the engine itself
    #     just moved the actor through that doorway; and
    # (b) door/barrier/exit objects connecting the current scene to an
    #     adjacent room — a door is visible from both sides.
    moved_through = {ev.get("blocking_object", {}).get("id")
                     for ev in keeper._movement_events
                     if isinstance(ev.get("blocking_object"), dict)}
    for obj in keeper.world_objects.values():
        if obj.location_id == keeper.current_scene or obj.state == "hidden":
            continue
        if obj.id in moved_through or _prop_connects_scene(keeper, obj.id):
            continue
        name = (obj.name or "").lower()
        if len(name) > 3 and re.search(rf"\b{re.escape(name)}\b", text):
            home = keeper.locations.get(obj.location_id)
            violations.append(
                f"cross-room prop: '{obj.name}' is in "
                f"{home.name if home else obj.location_id}, not here")
            break

    # v2.8.1.x: invented named physical objects. room_view is the
    # allowlist of what physically IS here; a newly introduced
    # interactable object (dummy, furniture, container) is a violation.
    # Atmospheric texture without interactable presence stays fine.
    # Indefinite article only ('a practice dummy'): an INDEFINITE
    # interactable noun introduces something new to the room. 'The desk'
    # merely references furniture the table already treats as present —
    # the same reference-vs-assertion rule as NPC states above, and the
    # field case ('a practice dummy set up at the far end') is caught
    # at its introduction.
    allow = [o.name for o in keeper.world_objects.values()
             if o.location_id == keeper.current_scene
             and o.state != "hidden"]
    allow += [i.name for i in keeper.item_instances.values()
              if i.location_id == keeper.current_scene
              and i.owner_id is None and "hidden" not in i.tags]
    # Scenario-authored room text legitimates the props it describes
    # ('a cramped study. Every flat surface...') — those are not
    # invented, the author put them there.
    _loc = keeper.locations.get(keeper.current_scene)
    if _loc is not None:
        allow += [_loc.description, _loc.first_visit, _loc.revisit,
                  _loc.lighting]
        allow += list((_loc.details or {}).values())
    for c in keeper.characters.values():
        if c.location != keeper.current_scene:
            continue
        allow.append(c.name)
        gear = ([c.equipped_item_id] if c.equipped_item_id else []) \
            + list(c.inventory)
        for iid in gear:
            inst = keeper.item_instances.get(iid)
            if inst is not None:
                allow.append(inst.name)
    for noun in INTERACTABLE_NOUNS:
        m = re.search(rf"\b(?:a|an)\s+(?:[a-z'-]+\s+){{0,3}}?"
                      rf"{noun}s?\b", text)
        if m and not _object_noun_allowlisted(noun, allow):
            violations.append(
                f"invented object: '{noun}' (not in the room)")
            break

    # v2.8.1.x: a resolved throw lands in the actor's room — narration
    # may not put the item somewhere else (packet facts are binding,
    # same rule as key/door continuity).
    for landed in getattr(keeper, "_landed_items", []):
        lname = (landed.get("name") or "").lower()
        if not lname or lname not in text:
            continue
        other_rooms = [(loc.name or "").lower()
                       for lid, loc in keeper.locations.items()
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
