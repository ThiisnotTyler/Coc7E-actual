"""v2.8.1 Room Truth — deterministic room views, exit rules, offline movement.

The engine owns truth; the model owns voice. This module builds what a room
*is* from deterministic state — authored description, object state, visible
items, visible characters, exits — so the LLM never has to invent layout,
and so observe/look/move need no API call at all.

Connection state vocabulary (scenario.json -> locations.<id>.connections.<dest>):

  open       passable (default when no state is given)
  closed     passable — opened on the way through
  locked     impassable unless the mover carries the key item (template id in
             key_id); a carried matching key unlocks the exit permanently
  blocked    impassable
  hidden     impassable and not listed (discovery hooks arrive with v2.8.4)
  destroyed  passable — the barrier is gone; flagged in the view

A connection may also carry "object_id" pointing at a WorldObject (a door);
the object's state then drives the exit state, keeping object and exit truth
consistent. one_way is accepted as a documentary flag: exits are directional
entries, so a one-way passage is simply defined on the origin side only.
"""
import re
from typing import Dict, List, Optional

PASSABLE_STATES = ("open", "closed", "destroyed")
EXIT_STATES = ("open", "closed", "locked", "blocked", "hidden", "destroyed")

# Pure movement declarations. Anything longer or unmatched falls through to
# the normal Keeper path — this is not a command parser.
MOVE_RE = re.compile(
    r"^(?:i(?:'ll| will)?\s+)?"
    r"(?:go\s+to|go\s+into|go\s+back\s+to|go\s+through|head\s+to|head\s+into|"
    r"walk\s+to|walk\s+into|step\s+into|step\s+through|enter|move\s+to|"
    r"return\s+to|cross\s+to|go)\s+(.+?)\s*$",
    re.I,
)
BACK_FORMS = {
    "leave", "go back", "head back", "return", "back",
    "go outside", "step out", "step outside", "back outside",
}
_ARTICLE_RE = re.compile(r"^(?:the|a|an)\s+", re.I)


def _conn(raw) -> dict:
    return raw if isinstance(raw, dict) else {}


# ---------------------------------------------------------------- exits
def connection_state(loc, dest_id: str, objects: Dict) -> str:
    """Effective state of the exit loc -> dest_id (object state wins)."""
    conn = _conn(loc.connections.get(dest_id))
    obj = objects.get(conn.get("object_id")) if conn.get("object_id") else None
    if obj is not None:
        if obj.state in ("broken", "destroyed"):
            return "destroyed"
        if obj.state == "hidden":
            return "hidden"
        if obj.state == "open":
            return "open"
        if obj.state == "locked" or obj.properties.get("locked"):
            return "locked"
        return "closed"
    state = str(conn.get("state", "open")).lower()
    return state if state in EXIT_STATES else "open"


def connection_key_id(loc, dest_id: str, objects: Dict) -> Optional[str]:
    conn = _conn(loc.connections.get(dest_id))
    obj = objects.get(conn.get("object_id")) if conn.get("object_id") else None
    if obj is not None:
        return obj.properties.get("key_id")
    return conn.get("key_id")


def visible_exits(locations: Dict, loc_id: str, objects: Dict) -> List[dict]:
    """Exits a character can perceive from loc_id — hidden exits never listed."""
    loc = locations.get(loc_id)
    out = []
    if loc is None:
        return out
    for dest_id in sorted(loc.connections):
        dest = locations.get(dest_id)
        if dest is None:
            continue
        state = connection_state(loc, dest_id, objects)
        if state == "hidden":
            continue
        out.append({
            "id": dest_id,
            "name": dest.name,
            "state": state,
            "type": _conn(loc.connections[dest_id]).get("type", ""),
        })
    return out


def _unlock_exit(keeper, loc, dest_id: str):
    """Engine-owned unlock: connection and linked door object stay consistent."""
    conn = loc.connections.get(dest_id)
    if not isinstance(conn, dict):
        return
    conn["state"] = "open"
    obj = keeper.world_objects.get(conn.get("object_id"))
    if obj is not None:
        obj.state = "open"
        obj.properties["locked"] = False


def carried_key_for_exit(keeper, char, loc, dest_id: str) -> Optional[str]:
    """Instance id of a carried key matching this exit, else None."""
    key_id = connection_key_id(loc, dest_id, keeper.world_objects)
    if not key_id:
        return None
    for iid in char.inventory:
        inst = keeper.item_instances.get(iid)
        if inst is not None and inst.template_id == key_id:
            return iid
    return None


# ------------------------------------------------------------- room view
def build_room_view(keeper, char=None, loc_id: str = None,
                    first: Optional[bool] = None) -> dict:
    """The deterministic view of a room.

    `char` is the viewer (first-visit memory is per character); when char is
    None (prompt building), the stable description is used. Hidden items,
    hidden objects, hidden exits, and clue data are never included.
    """
    lid = loc_id or (char.location if char is not None else keeper.current_scene)
    loc = keeper.locations.get(lid)
    if loc is None:
        return {"id": lid, "name": lid, "description": "", "exits": []}

    if first is None:
        first = (char is not None
                 and lid not in keeper.visited.get(char.id, set()))
    if first and loc.first_visit:
        description = loc.first_visit
    elif not first and loc.revisit:
        description = loc.revisit
    else:
        description = loc.description

    items = []
    for inst in sorted(keeper.item_instances.values(), key=lambda i: i.name):
        if inst.location_id != lid or inst.owner_id is not None:
            continue
        if "hidden" in inst.tags:
            continue
        label = inst.name
        if inst.condition != "intact":
            label += f" [{inst.condition}]"
        if inst.ammo is not None:
            label += f" ({inst.ammo} rounds)"
        items.append(label)

    objects = []
    for obj in sorted(keeper.world_objects.values(), key=lambda o: o.name):
        if obj.location_id != lid or obj.state == "hidden":
            continue
        bits = []
        if obj.state != "intact":
            bits.append(obj.state)
        if obj.properties.get("locked") and "locked" not in bits:
            bits.append("locked")
        objects.append(obj.name + (f" [{', '.join(bits)}]" if bits else ""))

    characters = []
    for cid in sorted(loc.occupants):
        c = keeper.characters.get(cid)
        if c is None or (char is not None and c.id == char.id):
            continue
        if c.extra.get("hidden"):
            continue
        entry = {
            "id": c.id,
            "name": c.name,
            "type": c.char_type,
            "condition": c.get_condition(),
        }
        # v2.8.1.x: when someone is looking, say how far away each person
        # is — band and nominal yards from the viewer (console only; the
        # prompt view has no viewer and stays unannotated).
        if char is not None:
            entry["position"] = c.position
            entry["distance_yards"] = int(round(
                keeper.combat.calc_distance(char, c)))
        # Readied means readied: only an equipped item is ever called out.
        if c.equipped_item_id:
            inst = keeper.item_instances.get(c.equipped_item_id)
            if inst is not None:
                entry["readied"] = inst.name
        characters.append(entry)

    return {
        "id": lid,
        "name": loc.name,
        "first_visit": first,
        "description": description,
        "lighting": loc.lighting,
        "details": dict(loc.details),
        "items": items,
        "objects": objects,
        "characters": characters,
        "exits": visible_exits(keeper.locations, lid, keeper.world_objects),
    }


def render_room_text(view: dict) -> str:
    """Human-readable rendering of a room view (console + prompt)."""
    lines = [f"--- {view.get('name', view.get('id', 'unknown'))} ---"]
    desc = view.get("description") or ""
    if desc:
        lines.append(desc)
    if view.get("lighting"):
        lines.append(f"Light: {view['lighting']}")
    for key, text in (view.get("details") or {}).items():
        lines.append(f"  {key}: {text}")
    if view.get("items"):
        lines.append("You notice: " + "; ".join(view["items"]))
    if view.get("objects"):
        lines.append("Objects: " + "; ".join(view["objects"]))
    chars = view.get("characters") or []
    if chars:
        bits = []
        for c in chars:
            s = c["name"]
            if c.get("position") and c.get("distance_yards") is not None:
                s += f" ({c['position']}, ~{c['distance_yards']}y)"
            if c.get("readied"):
                s += f" (readied: {c['readied']})"
            bits.append(s)
        lines.append("Present: " + "; ".join(bits))
    exits = view.get("exits") or []
    if exits:
        bits = []
        for e in exits:
            s = e["name"]
            if e.get("state") not in (None, "open"):
                s += f" [{e['state']}]"
            bits.append(s)
        lines.append("Exits: " + "; ".join(bits))
    else:
        lines.append("Exits: none that you can see.")
    return "\n".join(lines)


# -------------------------------------------------------------- movement
def match_movement(keeper, char, action: str) -> Optional[dict]:
    """Resolve a declaration to a local move.

    Returns {"dest": id}, {"error": text}, or None when the input is not a
    pure movement declaration (the normal Keeper path then handles it).
    """
    t = action.strip().rstrip(".")
    low = " ".join(t.lower().split())
    loc = keeper.locations.get(char.location)
    if loc is None:
        return None

    exits = visible_exits(keeper.locations, char.location, keeper.world_objects)
    if low in BACK_FORMS:
        prev = char.extra.get("_prev_loc")
        exit_ids = {e["id"] for e in exits}
        if prev and prev in exit_ids:
            return {"dest": prev}
        if prev and prev in keeper.locations:
            return {"error": "There is no way back from here."}
        if len(exits) == 1:
            return {"dest": exits[0]["id"]}
        if not exits:
            return {"error": "There is nowhere to go from here."}
        return None   # ambiguous: let the Keeper narrate it

    m = MOVE_RE.match(t)
    if not m:
        return None
    dest_text = _ARTICLE_RE.sub("", m.group(1).strip()).strip().lower()
    if len(dest_text) < 3:
        return None

    exit_ids = {e["id"] for e in exits}
    hidden_ids = set(loc.connections) - exit_ids

    def _score(loc) -> int:
        """2 = exact, 1 = substring, 0 = no match."""
        names = [loc.name.lower(), loc.id.replace("_", " ").lower()]
        if any(dest_text == n for n in names):
            return 2
        if any(dest_text in n or n in dest_text for n in names if len(n) >= 3):
            return 1
        return 0

    connected = [(lid, _score(l)) for lid, l in keeper.locations.items()
                 if lid in exit_ids]
    connected = [(lid, s) for lid, s in connected if s]
    if connected:
        best = max(s for _, s in connected)
        winners = sorted(lid for lid, s in connected if s == best)
        if len(winners) == 1:
            return {"dest": winners[0]}
        names = "; ".join(keeper.locations[lid].name for lid in winners)
        return {"error": f"Which way? That could be: {names}."}

    elsewhere = [(lid, _score(l)) for lid, l in keeper.locations.items()]
    elsewhere = [(lid, s) for lid, s in elsewhere
                 if s and lid != char.location and lid not in hidden_ids]
    if elsewhere:
        best = max(s for _, s in elsewhere)
        winners = sorted(lid for lid, s in elsewhere if s == best)
        if len(winners) == 1:
            name = keeper.locations[winners[0]].name
            return {"error": f"You can't get to the {name} directly from here."}
    return None   # no room matched — not movement, fall back to the Keeper


def try_local_move(keeper, char, dest_id: str) -> dict:
    """Validate and perform an engine-owned move.

    Updates location, occupants, first-visit memory, previous-location, and
    (via the caller) the current scene. Returns a result dict with 'moved',
    and on success: 'first', 'unlocked', 'triggers'.
    """
    loc = keeper.locations.get(char.location)
    dest = keeper.locations.get(dest_id)
    if loc is None or dest is None:
        return {"moved": False, "error": "That way leads nowhere."}

    state = connection_state(loc, dest_id, keeper.world_objects)
    if state == "blocked":
        return {"moved": False, "error": f"The way to the {dest.name} is blocked."}
    if state == "hidden":
        return {"moved": False, "error": "You see no way through there."}

    unlocked = None
    if state == "locked":
        iid = carried_key_for_exit(keeper, char, loc, dest_id)
        if iid is None:
            return {"moved": False, "error": f"The way to the {dest.name} is locked."}
        unlocked = keeper.item_instances[iid].name
        _unlock_exit(keeper, loc, dest_id)
        state = "open"

    # v2.8.1.1 packet fields: the LLM must narrate a COMPLETED move, so the
    # packet carries origin, destination, and what stood between them.
    blocking = None
    conn = _conn(loc.connections.get(dest_id))
    bobj = keeper.world_objects.get(conn.get("object_id")) if conn.get("object_id") else None
    if bobj is not None:
        blocking = {"id": bobj.id, "name": bobj.name, "state": bobj.state}

    first = dest_id not in keeper.visited.get(char.id, set())
    origin = char.location
    char.extra["_prev_loc"] = origin
    keeper.spatial.move_occupant(char.id, origin, dest_id)
    char.location = dest_id
    keeper.mark_visited(char.id, dest_id)

    triggers = escalation_triggers(keeper, char, dest_id)
    # Note: a destroyed exit does NOT self-escalate — the destruction was
    # narrated (or will be) by the action that caused it; walking through the
    # hole afterwards is ordinary movement (v2.8.1.1 field-test hotfix).
    return {
        "moved": True,
        "dest": dest_id,
        "origin": origin,
        "first": first,
        "unlocked": unlocked,
        "blocking_object": blocking,
        "triggers": triggers,
    }


# ------------------------------------------------------------- escalation
def escalation_triggers(keeper, char, dest_id: str) -> List[str]:
    """Why an entry needs the Keeper's voice instead of a local view.

    Implemented now: visible NPCs, hazard/trap tags, SAN-pressure tags,
    first-time revelation of visible clues, front triggers at the current
    clock, timeline events pinned to this turn. Hooks for active combat,
    major object-state reveals, and private perception land with their
    respective roadmap phases (v2.8.2+).
    """
    reasons = []
    dest = keeper.locations.get(dest_id)

    for cid in sorted(dest.occupants) if dest else []:
        c = keeper.characters.get(cid)
        if c is None or c.char_type == "player" or c.id == char.id:
            continue
        if c.extra.get("hidden"):
            continue
        reasons.append(f"npc:{c.name}")

    if dest is not None:
        tags = set(dest.tags)
        if tags & {"hazard", "trap"}:
            reasons.append("hazard")
        if tags & {"mythos", "san"}:
            reasons.append("san-pressure")

    # Clue hook: a clue marked "visible": true is revealed on first entry —
    # the engine stamps it discovered (engine-owned); formal, skill-gated
    # clue triggers arrive in v2.8.4.
    for clue in keeper.clues:
        if not isinstance(clue, dict):
            continue
        if clue.get("location") != dest_id or not clue.get("visible"):
            continue
        cid_ = clue.get("id")
        if cid_ and cid_ not in keeper.discovered_clues:
            keeper.discovered_clues.add(cid_)
            reasons.append(f"clue-reveal:{cid_}")

    for front_id, front in (keeper.fronts or {}).items():
        clock = front.get("clock", 0)
        for trig in front.get("triggers", []):
            if isinstance(trig, dict) and trig.get("clock") == clock and clock:
                reasons.append(f"front-event:{front_id}")

    for ev in keeper.timeline or []:
        if not isinstance(ev, dict):
            continue
        if ev.get("location") == dest_id and ev.get("turn", 10**9) <= keeper.turn:
            reasons.append(f"timeline:{str(ev.get('event', ''))[:40]}")

    return reasons
