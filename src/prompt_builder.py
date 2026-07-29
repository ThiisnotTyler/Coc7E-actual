"""Prompt builder + LLM correction path (v2.8.1.x) — decoupled from keeper.py.

Everything that SHAPES text sent to or recovered from the model:

  build_prompt_sections / build_prompt
      the turn prompt as Governor-trimmable sections (scenario, scene,
      room view, characters, party locations, declarations, dice results
      with opposed-melee verdicts, untouched-NPC lines, movement events,
      narration rules)
  _affected_npc_ids / _untouched_npc_lines
      which scene NPCs the turn mechanically touched — and one explicit
      'untouched this turn' line for everyone else, so the model stops
      inventing bystander states from packet silence
  _heavy_trigger
      what earns the heavy (k3) tier: CINEMATIC mode, Mythos/creature
      scenes, fronts on a trigger threshold
  _narration_validation_retry / _log_validation_retry /
  _log_validation_fallback
      the ONE compact correction attempt a rejected narration earns, and
      its telemetry (resolved or not, first-attempt violations)

Functions take `keeper` explicitly (the local_voice.py /
narration_validator.py / commands.py pattern) and are read-only against
game state — the only writes are LLM calls and timing-log rows.
CoCKeeper keeps one-line delegates for the call sites and test surface.
"""
import json
import os
import re
from typing import Dict, List, Optional

from src import room_view
from src.mode import ResolutionMode
from src.human_keeper import _verdict_line
from src.narration_validator import NARRATION_RULES_PACKET


def _affected_npc_ids(keeper, dice_results):
    """Ids the engine mechanically touched this turn: targeted by a
    roll, damaged, moved (movement events), forced to move, or flipped
    unaware -> alert during resolution. Everyone else in the room is
    'untouched this turn'. (Field 2026-07-27: the packet said what DID
    happen but nothing about what did NOT — the model filled the
    silence with 'The Brawler bleeding' on an NPC never targeted,
    rolled against, or damaged.)"""
    affected = set()
    for res in (dice_results or {}).values():
        tid = res.get("target_char")
        if tid:
            affected.add(tid)
        fm = res.get("forced_move")
        if fm and fm.get("npc"):
            affected.add(fm["npc"])
    for ev in (keeper._movement_events or []):
        if ev.get("character"):
            affected.add(ev["character"])
    for c in keeper.characters.values():
        if c.char_type == "player":
            continue
        if (not keeper._alerted_at_turn_start.get(c.id, True)
                and getattr(c, "alerted", True)):
            affected.add(c.id)
    return affected

def _untouched_npc_lines(keeper, room_ids, dice_results):
    """One short engine-truth line per scene NPC the turn did NOT
    touch — the packet states what did NOT happen so the model stops
    inventing bystander states. One line per NPC, budget-trivial."""
    affected = _affected_npc_ids(keeper, dice_results)
    lines = []
    for c in keeper.characters.values():
        if (c.char_type == "player" or c.location not in room_ids
                or c.id in affected or c.extra.get("hidden")):
            continue
        cond = c.get_condition()
        state = "full HP" if cond == "healthy" else cond.replace("_", " ")
        alert = "alert" if getattr(c, "alerted", True) else "unaware"
        lines.append(
            f"{c.name}: untouched this turn — {state}, {c.position}, "
            f"{alert}; do not describe injury, blood, collapse, or any "
            f"state change.")
    return lines

def build_prompt_sections(keeper, declarations: Dict[str, str],
                          dice_results: dict):
    """The turn prompt as Governor-trimmable sections (v2.8.1.6).

    Same content as the legacy build_prompt, but structured so the
    Latency Governor can measure each bucket, slim the room view, and
    drop low-priority sections when a tier cap bites. build_prompt(keeper)
    below joins them unchanged — mock mode and prompt-content tests see
    the identical text as before."""
    mode = keeper.mode_selector.select_mode(
        list(keeper.characters.values()), declarations, scene_tension=0)
    # v2.8.1.x party truth: a party can be split across rooms. Anyone in
    # the scene OR in a declaring player's room is active; nobody who is
    # acting this turn may be filed as off-screen.
    active_rooms = {keeper.current_scene}
    for cid in declarations:
        ch = keeper.characters.get(cid)
        if ch is not None:
            active_rooms.add(ch.location)
    active = [c for c in keeper.characters.values() if c.location in active_rooms]
    inactive = [c for c in keeper.characters.values() if c.location not in active_rooms]
    scene = keeper.locations.get(keeper.current_scene)
    # v2.8.1: the model sees the deterministic room view — visible exits
    # (hidden exits never reach the prompt), object state, visible items,
    # and who is actually present with what they have readied.
    exits = {e["id"]: e["name"] for e in room_view.visible_exits(
        keeper.locations, keeper.current_scene, keeper.world_objects)}
    view = room_view.build_room_view(keeper)
    # v2.8.1.1 first-visit continuity: the model must know whether each
    # acting character has PERSONALLY seen this room before, or it writes
    # 'back'/'still'/'where you left it' into a room nobody has visited.
    view["visits"] = {
        c.id: {
            "count": keeper.visit_counts.get(c.id, {}).get(keeper.current_scene, 0),
            "seen_before": keeper.current_scene in keeper.visited.get(c.id, set()),
        }
        for c in keeper.characters.values() if c.char_type == "player"
    }
    # v2.7.0 latency diet: llm.compact_prompt drops pretty-print indent
    # and separator padding from every JSON block. Tokens are latency and
    # money; the model reads compact JSON just as well. The mock client
    # parses both forms (pinned by test_engine).
    compact = bool(keeper.config.get("llm", {}).get("compact_prompt", False))

    def _jd(obj):
        if compact:
            return json.dumps(obj, separators=(",", ":"))
        return json.dumps(obj, indent=2)

    view_slim = {k: v for k, v in view.items() if k != "details"}
    io_hint = len(_jd({"items": view.get("items"),
                       "objects": view.get("objects")}))
    # v2.8.1.x: opposed melee gets a plain verdict line the model can
    # lean on (field: the loser's blow connecting in prose).
    verdicts = []
    for cid, res in (dice_results or {}).items():
        c = keeper.characters.get(cid)
        v = _verdict_line(res, c.name if c is not None else cid)
        if v:
            verdicts.append(v)
    dice_text = f"DICE RESULTS:\n{_jd(dice_results)}"
    if verdicts:
        dice_text += "\n" + "\n".join(verdicts)
    # v2.8.1.x: the packet says what did NOT happen — one short line
    # per scene NPC the turn never touched.
    untouched = _untouched_npc_lines(keeper, active_rooms, dice_results)
    # v2.8.1.x party truth: where every investigator IS, in engine terms,
    # so narration can never lose track of a split party.
    party_locations = []
    for c in keeper.characters.values():
        if c.char_type != "player":
            continue
        c_loc = keeper.locations.get(c.location)
        party_locations.append({
            "id": c.id, "name": c.name,
            "location": c.location,
            "room": c_loc.name if c_loc else c.location,
            "with": [o.name for o in keeper.characters.values()
                     if o.id != c.id and o.char_type == "player"
                     and o.location == c.location],
        })
    sections = [
        {"key": "scenario", "bucket": "scenario",
         "text": f"SCENARIO: {keeper.scenario_title} — {keeper.scenario_tone}"},
        {"key": "scene_core", "bucket": "scene",
         "text": (f"TURN {keeper.turn}\nMODE: {mode.value}\n"
                  f"CURRENT SCENE: {keeper.current_scene} "
                  f"({scene.name if scene else 'unknown'})\n"
                  f"EXITS: {_jd(exits)}")},
        {"key": "room", "bucket": "scene",
         "text": f"ROOM VIEW:\n{_jd(view)}",
         "slim": f"ROOM VIEW:\n{_jd(view_slim)}"},
        {"key": "characters_active", "bucket": "characters",
         "text": "ACTIVE CHARACTERS:\n"
                 + _jd([c.to_active_format() for c in active[:keeper.max_active]])},
        {"key": "party_locations", "bucket": "characters",
         "text": "PARTY LOCATIONS (engine truth — where each investigator "
                 "is standing THIS turn, and which other investigators "
                 "share that room):\n" + _jd(party_locations)},
        {"key": "characters_offscreen", "bucket": "characters",
         "droppable": True,
         "text": "OFF-SCREEN CHARACTERS:\n"
                 + _jd([c.to_summary_format() for c in inactive[:8]])},
        {"key": "npcs_untouched", "bucket": "characters",
         "text": ("UNTOUCHED NPCS THIS TURN (engine truth — not "
                  "targeted, damaged, moved, or alerted):\n"
                  + "\n".join(untouched)) if untouched else ""},
        {"key": "declarations", "bucket": "adjudication",
         "text": f"PLAYER DECLARATIONS:\n{_jd(declarations)}"},
        {"key": "dice", "bucket": "adjudication",
         "text": dice_text},
        {"key": "fronts_plot", "bucket": "fronts/plot", "droppable": True,
         "text": (f"FRONTS: {_jd({k: v.get('clock', 0) for k, v in keeper.fronts.items()})}\n"
                  f"PLOT POINTS: {_jd(keeper.plot_points)}")},
        {"key": "items_objects_hint", "bucket": "items/objects",
         "text": "", "telemetry_chars": io_hint},
    ]
    # v2.8.1.x split-party truth: a declaring player acting in a room
    # other than the current scene still gets that room's deterministic
    # view, or the model narrates their surroundings from memory.
    seen_rooms = {keeper.current_scene}
    for cid in declarations:
        ch = keeper.characters.get(cid)
        if ch is None or ch.location in seen_rooms:
            continue
        seen_rooms.add(ch.location)
        extra = room_view.build_room_view(keeper, loc_id=ch.location)
        extra_slim = {k: v for k, v in extra.items() if k != "details"}
        sections.append({
            "key": f"room_view_{ch.location}", "bucket": "scene",
            "droppable": True,
            "text": (f"ROOM VIEW ({ch.location} — where {ch.name} is "
                     f"acting):\n{_jd(extra)}"),
            "slim": f"ROOM VIEW ({ch.location}):\n{_jd(extra_slim)}"})
    if keeper._movement_events:
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
                f"origin):\n{_jd(keeper._movement_events)}")})
    sections.append({"key": "narration_rules", "bucket": "other",
                     "text": NARRATION_RULES_PACKET})
    sections.append({"key": "task", "bucket": "other",
                     "text": "NARRATE THIS TURN."})
    return sections, mode

def build_prompt(keeper, declarations: Dict[str, str], dice_results: dict):
    sections, mode = build_prompt_sections(keeper, declarations, dice_results)
    return "\n".join(s["text"] for s in sections if s["text"]), mode

def _narration_validation_retry(keeper, violations, mode, declarations,
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
    compact = keeper.governor.build_compact_prompt(
        keeper, mode, declarations, dice_results)
    # v2.8.1.x: hand the retry the scene's engine truth (NPC states,
    # room objects) so it verifies instead of guessing.
    packet = keeper._validation_packet()
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
        "\n\n" + NARRATION_RULES_PACKET +
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
            recovered = keeper.gemini.query(
                COMPACT_SYSTEM_PROMPT, compact + correction,
                timing=llm_timing, context=cctx,
                plan=cplan, compact_prompt=None)
        except TypeError as te:
            # Test stubs may not accept the timing/context kwargs.
            if "unexpected keyword argument" not in str(te):
                raise
            recovered = keeper.gemini.query(COMPACT_SYSTEM_PROMPT,
                                          compact + correction)
    except Exception:
        return None
    if not isinstance(recovered, dict):
        return None
    n2 = str(recovered.get("narration", ""))
    if not n2.strip():
        return None
    v2 = keeper._validate_narration(n2, recovered, dice_results, acting_ids)
    if v2:
        if keeper.debug:
            print("[narration validator: compact retry unresolved: "
                  + "; ".join(v2) + " — using local outcome]")
        return None
    return recovered

def _log_validation_retry(keeper, violations, resolved, turn_context):
    """Telemetry category 'narration_validation_retry' (v2.8.1.x FIX B):
    one JSONL row per compact correction attempt carrying the FIRST
    attempt's violation strings and whether the retry resolved. Pure
    instrumentation — no behavior change. Written to turn_timing.jsonl
    so a session's retry pattern reads in one file."""
    try:
        from datetime import datetime, timezone
        from src import latency as _lat
        row = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "version": _lat.project_version(),
            "commit": _lat.git_commit(),
            "provider": getattr(keeper.gemini, "provider", "unknown"),
            "attempt": "narration_validation_retry",
            "violations": [str(v) for v in violations],
            "resolved": bool(resolved),
        }
        for k in ("resolution_mode", "turn", "scenario", "source"):
            if k in (turn_context or {}):
                row[k] = turn_context[k]
        _lat.write_timing_row(_lat.TURN_TIMING_LOG, row)
    except Exception:
        pass

def _log_validation_fallback(keeper, turn_context):
    """Telemetry category 'narration_validation_local_fallback': the
    compact correction failed and the engine reported plainly — zero
    provider cost, recorded so --report can price it (v2.8.1.x P0-1)."""
    if keeper.mock or getattr(keeper.gemini, "is_human", False):
        return
    try:
        from datetime import datetime, timezone
        from src import latency as _lat
        row = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "version": _lat.project_version(),
            "commit": _lat.git_commit(),
            "provider": getattr(keeper.gemini, "provider", "unknown"),
            "model": getattr(keeper.gemini, "default_model", "unknown"),
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

def _heavy_trigger(keeper, mode, declarations: Dict[str, str]) -> bool:
    """v2.8.1.3: what actually earns the heavy (k3) tier.

    Heavy is reserved for CINEMATIC mode, Mythos/creature scenes, and
    fronts sitting on a trigger threshold. Routine social threats and
    combat against ordinary NPCs stay on the default model."""
    if mode == ResolutionMode.CINEMATIC:
        return True
    scene = keeper.locations.get(keeper.current_scene)
    if scene is not None and set(scene.tags) & {"mythos", "creature"}:
        return True
    for c in keeper.characters.values():
        if c.char_type == "player" or c.location != keeper.current_scene:
            continue
        nature = str(c.extra.get("nature", "")).lower()
        if nature in ("mythos", "creature", "monster", "elder"):
            return True
    for front in keeper.fronts.values():
        clock = front.get("clock", 0)
        if clock and any(isinstance(t, dict) and t.get("clock") == clock
                         for t in front.get("triggers", [])):
            return True
    return False
