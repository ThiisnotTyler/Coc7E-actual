"""Local voice (v2.8.1.x) — the voiceless fallback, decoupled from keeper.py.

When the LLM fails (provider timeout on both attempts, or a narration that
cannot pass the validator even after the compact correction), the engine
still owes the table an answer. This module composes that answer: one
deterministic sentence per outcome, interpolated ONLY from engine truth —
names, weapon/item names, level, damage, wound band, alert status. No LLM,
no invention, no randomness beyond the dice already rolled.

Field 2026-07-27: a clean 'Throw 75% — Hard, 4 damage' hit went voiceless
and the dice-dump fallback read like a crash dump — 'the single biggest
feel-killer left in the game'. Every composed sentence is built to pass
CoCKeeper._validate_narration clean (the test_engine local-voice section
proves it case by case).

Functions take `keeper` explicitly (the human_keeper.py pattern) so the
module reads engine state without owning it. CoCKeeper keeps a one-line
`_minimal_outcome_result` delegate for the call sites and tests.
"""
from src import room_view

# The header is part of the contract: the table must always know the LLM
# failed. Keep it byte-identical forever.
VOICE_HEADER = "(The Keeper is voiceless — the engine reports plainly.)"

# Wound bands as plain speech, straight from Character.get_condition().
_VOICE_BANDS = {"healthy": "hurt", "wounded": "wounded",
                "major_wound": "suffers a major wound",
                "unconscious": "knocked unconscious", "dying": "dying"}


def _voice_wound_clause(who, damage):
    """'{Name} is wounded (damage: N).' — the band from get_condition(),
    the figure the engine dealt. The figure rides as 'damage: N', never
    'N damage': the voiceless report must itself pass the narration
    validator, and _MECHANICS_QUOTE_RE anchors on the number-first
    form. Engine truth either way; only the phrasing bends."""
    band = _VOICE_BANDS.get(who.get_condition(), "hurt")
    clause = (f"{who.name} suffers a major wound"
              if band == "suffers a major wound"
              else f"{who.name} is {band}")
    if damage:
        clause += f" (damage: {damage})"
    return clause + "."


def _voice_malfunction_note(res):
    """The engine's own malfunction/fumble note, verbatim, when present."""
    notes = [str(res["note"])] if res.get("note") else []
    notes.extend(str(n) for n in (res.get("notes") or []))
    for n in notes:
        low = n.lower()
        if "malfunction" in low or "fumble" in low or "jam" in low:
            return n
    return ""


def _voice_attack(char, name, res, tgt, level):
    """Weapon attack voice: hit, miss, fumble, and opposed melee (both
    rolls, then the verdict and the winner)."""
    unarmed = char is None or char.weapon is None
    weapon = "fists" if unarmed else char.weapon.name
    mark = "find their mark" if unarmed else "finds its mark"
    wide = "go wide" if unarmed else "goes wide"
    dr = res.get("defender_roll")
    if dr:
        stance = ("fights back" if res.get("stance") == "fight_back"
                  else "dodges")
        dname = dr.get("name", "the defender")
        counter = res.get("counter")
        if res.get("hit"):
            line = (f"{name}'s {weapon} {mark} — {level}. "
                    f"{dname} {stance} — {dr.get('level')}. "
                    f"{name} comes out on top. "
                    + _voice_wound_clause(tgt, res.get("damage")))
        elif counter:
            line = (f"{name}'s {weapon} — {level}. "
                    f"{dname} {stance} — {dr.get('level')}. "
                    f"{dname} comes out on top.")
            if char is not None and counter.get("damage"):
                line += (" " + _voice_wound_clause(
                    char, counter["damage"]))
        else:
            # a successful dodge, or both sides coming up empty
            line = (f"{name}'s {weapon} — {level}. "
                    f"{dname} {stance} — {dr.get('level')}. "
                    f"{dname} comes out on top.")
    elif res.get("hit") or res.get("damage"):
        line = f"{name}'s {weapon} {mark} — {level}."
        if res.get("damage"):
            line += " " + _voice_wound_clause(tgt, res["damage"])
    else:
        line = f"{name}'s {weapon} {wide} — {tgt.name} is unscathed."
    malfunction = _voice_malfunction_note(res)
    if malfunction:
        line += " " + malfunction
    return line


def _local_voice_line(keeper, char, name, res):
    """One composed sentence for one engine outcome, interpolated ONLY
    from engine truth — names, weapon/item names, level, damage, wound
    band. No LLM, no invention, no randomness beyond the dice already
    rolled."""
    skill = str(res.get("skill", "Roll")).replace("_", " ")
    roll, target, level = res.get("roll"), res.get("target"), res.get("level")
    if roll is None or target is None or level is None:
        notes = "; ".join(res.get("notes") or []) or res.get("note", "")
        return f"{name} — {skill}: {notes}" if notes else None
    tgt = keeper.characters.get(res.get("target_char") or "")
    if skill == "Throw" and res.get("thrown_item"):
        item = res["thrown_item"]
        if tgt is not None and res.get("damage"):
            return (f"{name}'s thrown {item} strikes {tgt.name} — "
                    f"{level}. "
                    + _voice_wound_clause(tgt, res["damage"]))
        if tgt is not None:
            return (f"{name}'s thrown {item} misses {tgt.name} and "
                    f"skitters across the floor.")
        return f"{name}'s thrown {item} skitters across the floor."
    if tgt is not None and res.get("attack_type") in ("melee", "firearms"):
        return _voice_attack(char, name, res, tgt, level)
    # a plain skill roll — '{Name} — {skill}: {level}.' plus the
    # engine's own object_result/note text
    line = f"{name} — {skill}: {level}."
    if tgt is not None and res.get("damage"):
        line += " " + _voice_wound_clause(tgt, res["damage"])
    if res.get("object_result"):
        line += f" {res['object_result']}"
    if res.get("note"):
        line += f" {res['note']}"
    return line


def minimal_outcome_result(keeper, mode, dice_results):
    """Degraded option 3: the engine speaks for itself — one composed
    local-voice sentence per outcome, no LLM, no cost, no invention.

    Each line comes from engine fields ONLY via _local_voice_line; the
    header stays exactly as it was so the table always knows the LLM
    failed; an engine-resolved entry still gets the plain room report
    from room_view (field: an escalated entry's fallback once said
    'Nothing stirred.' while the player stood in a new room with two
    NPCs)."""
    lines = [VOICE_HEADER]
    for cid, res in (dice_results or {}).items():
        char = keeper.characters.get(cid)
        name = char.name if char else cid
        line = _local_voice_line(keeper, char, name, res)
        if line:
            lines.append(line)
    # An engine-resolved move is real: report the room, not silence.
    if keeper._movement_events:
        ev = keeper._movement_events[-1]
        dest = (ev.get("current_location_after_action")
                or keeper.current_scene)
        mover = keeper.characters.get(ev.get("character"))
        view = room_view.build_room_view(keeper, mover, loc_id=dest,
                                         first=False)
        lines.append(f"You are in the {view['name']}.")
        # the location's STABLE description, from room truth
        dest_loc = keeper.locations.get(dest)
        desc = ((dest_loc.description or "") if dest_loc else "") \
            or view.get("description", "")
        if desc:
            lines.append(desc)
        for c in view.get("characters", []):
            who = keeper.characters.get(c["id"])
            unaware = (who is not None
                       and not getattr(who, "alerted", True))
            cond = str(c.get("condition", "")).replace("_", " ")
            line = f"Present: {c['name']} ({cond})"
            if unaware:
                line += " — has not noticed you"
            lines.append(line)
        exits = "; ".join(
            e["name"] + (f" [{e['state']}]" if e["state"] != "open" else "")
            for e in view.get("exits", []))
        lines.append("Exits: " + (exits or "none that you can see."))
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
