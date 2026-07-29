"""Human Keeper provider (v2.8.1.5).

A human host narrates outcomes instead of an AI API. The engine still owns
truth: dice, adjudication, inventory, movement, NPC condition, object state,
and every canonical field. The human replaces ONLY the narration/voice layer.

Three pieces, deliberately separate so a future remote host service can send
the packet over the wire instead of printing it locally:

  build_human_keeper_packet(keeper, mode, declarations, dice_results)
      -> the structured packet: engine facts + restrictions (JSON-safe)
  render_human_keeper_packet(packet)
      -> the compact text block shown to the host
  HumanKeeperClient.narrate(packet)
      -> local terminal I/O: display, collect, parse, log
  parse_human_keeper_response(segments, mode, status)
      -> the same internal response structure the AI clients return

There is no API timeout, no retry budget, no token estimate, no provider
cost accounting, and no strict/final retry ladder in this module. Human
turns log separately to logs/human_keeper.jsonl.
"""
import json
import os
import time
from datetime import datetime, timezone

from src import room_view
from src.dice import LEVEL_RANK


# The narration restrictions every packet ends with (v2.8.1.5 spec). These
# mirror the AI system prompt's STATE AUTHORITY / ROOM TRUTH sections: the
# human, like the model, owns voice — never mechanics.
HUMAN_KEEPER_RESTRICTIONS = (
    "narrate only the outcome;",
    "do not change mechanics;",
    "do not move tracked items;",
    "do not reveal hidden facts;",
    "do not change NPC condition;",
    "do not move characters between rooms;",
    "do not append command lists.",
)

PROTOCOL_HELP = """Human Keeper protocol:
  plain text          public narration (multiline; shown to the whole table)
  /private <char_id>  following lines are a private note for one character
  /public             return to public narration
  /end                finish — hand the narration to the engine
  /cancel             cancel — the turn is NOT consumed
  /skip               provide no narration this turn
  /help               show this help"""

HUMAN_TIMING_LOG = os.path.join("logs", "human_keeper.jsonl")


class HumanKeeperCancelled(Exception):
    """The host typed /cancel (or stdin closed): refund the turn."""


# ---------------------------------------------------------------- packet
def build_human_keeper_packet(keeper, mode, declarations, dice_results) -> dict:
    """The compact, JSON-safe packet a human Keeper narrates FROM.

    Everything in it is engine truth: the scene, who acted and what they
    declared, what the adjudication layer decided, what the dice produced,
    engine-completed world changes (movement events), the deterministic room
    view, and each acting character's first-visit status. Hidden items,
    exits, and clue data never enter the packet — room_view enforces that
    here exactly as it does for the AI prompt."""
    scene = keeper.locations.get(keeper.current_scene)
    acting = [{"id": cid, "name": keeper.characters[cid].name}
              for cid in declarations if cid in keeper.characters]
    # The packet's point of view is the acting character's room, not the
    # campaign's current_scene: when the party is split (or an escalated
    # entry just moved the actor), the host must see WHERE THE ACTION IS.
    focus = next((keeper.characters[cid] for cid in declarations
                  if cid in keeper.characters), None)
    focus_loc = focus.location if focus is not None else keeper.current_scene
    focus_scene = keeper.locations.get(focus_loc)
    visits = {
        c.id: {
            "count": keeper.visit_counts.get(c.id, {}).get(focus_loc, 0),
            "seen_before": focus_loc in keeper.visited.get(c.id, set()),
        }
        for c in keeper.characters.values() if c.char_type == "player"
    }
    return {
        "scenario": keeper.scenario_id,
        "turn": keeper.turn,
        "mode": mode.value if hasattr(mode, "value") else str(mode),
        "scene": {"id": focus_loc,
                  "name": focus_scene.name if focus_scene else "unknown"},
        "campaign_scene": {"id": keeper.current_scene,
                           "name": scene.name if scene else "unknown"},
        "acting": acting,
        "character_names": {cid: c.name for cid, c in keeper.characters.items()},
        "declarations": dict(declarations),
        "dice_results": dice_results or {},
        "movement_events": list(keeper._movement_events or []),
        "room_view": room_view.build_room_view(keeper, focus),
        "visits": visits,
        "fronts": {k: v.get("clock", 0) for k, v in keeper.fronts.items()},
        "plot_points": list(keeper.plot_points),
        "restrictions": list(HUMAN_KEEPER_RESTRICTIONS),
    }


def _verdict_line(res, name):
    """Opposed melee: one plain verdict line the narrator can lean on —
    'Outcome: {winner}'s {level} beats {loser}'s {level} — {loser}'s
    strike does not land.' (Earlier field bug: the loser's blow connected
    in prose.) A double-miss has no winner and gets no line — the roll
    notes already say both sides came up empty."""
    dr = res.get("defender_roll")
    if not dr:
        return None
    a_level, d_level = res.get("level"), dr.get("level")
    if a_level not in LEVEL_RANK or d_level not in LEVEL_RANK:
        return None
    a_rank, d_rank = LEVEL_RANK[a_level], LEVEL_RANK[d_level]
    if a_rank < LEVEL_RANK["Regular"] and d_rank < LEVEL_RANK["Regular"]:
        return None
    dname = dr.get("name", "the defender")
    if res.get("hit"):
        winner, w_level, loser, l_level = name, a_level, dname, d_level
    elif res.get("counter") or d_rank >= a_rank:
        winner, w_level, loser, l_level = dname, d_level, name, a_level
    else:
        return None
    return (f"Outcome: {winner}'s {w_level} beats {loser}'s {l_level} — "
            f"{loser}'s strike does not land.")


def _dice_lines(packet) -> list:
    """One readable line per engine roll, with its mechanical outcome."""
    names = packet.get("character_names") or {}
    lines = []
    for cid, res in (packet.get("dice_results") or {}).items():
        name = names.get(cid, cid)
        skill = str(res.get("skill", "Roll")).replace("_", " ")
        roll, target, level = res.get("roll"), res.get("target"), res.get("level")
        if roll is not None and target is not None and level is not None:
            line = f"{name} — {skill} {target}%: rolled {roll} — {level}"
            if res.get("malfunction"):
                line += " — WEAPON JAMS"
            if res.get("damage"):
                who = names.get(res.get("target_char"), res.get("target_char"))
                line += f" ({res['damage']} damage" + (f" to {who}" if who else "") + ")"
            lines.append(line)
        else:
            notes = "; ".join(res.get("notes") or []) or res.get("note", "")
            lines.append(f"{name} — {skill}: {notes or 'no roll'}")
        # mechanical outcome the narration must honor
        verdict = _verdict_line(res, name)
        if verdict:
            lines.append("  " + verdict)
        if res.get("object_result"):
            lines.append(f"  outcome: {res['object_result']}")
        if res.get("nonlethal") and res.get("level") not in ("Failure", "Fumble"):
            lines.append("  outcome: nonlethal — the target is knocked out, not killed")
        fm = res.get("forced_move")
        if fm:
            lines.append(f"  outcome: forced movement — {fm}")
        if res.get("requested"):
            lines.append(f"  (answering last turn's request: {res['requested']})")
    return lines


def _movement_lines(packet) -> list:
    names = packet.get("character_names") or {}
    lines = []
    for ev in packet.get("movement_events") or []:
        who = names.get(ev.get("character"), ev.get("character"))
        line = (f"{who}: {ev.get('origin_location')} -> "
                f"{ev.get('destination_location')} (movement completed)")
        extras = []
        if ev.get("unlocked_with"):
            extras.append(f"unlocked with {ev['unlocked_with']}")
        blocking = ev.get("blocking_object")
        if blocking:
            name = blocking.get("name") if isinstance(blocking, dict) else str(blocking)
            extras.append(f"was blocked by {name}")
        extras.append("first visit" if ev.get("first_visit") else "revisit")
        if ev.get("triggers"):
            extras.append("triggers: " + ", ".join(str(t) for t in ev["triggers"]))
        lines.append(line + " — " + "; ".join(extras))
    return lines


def render_human_keeper_packet(packet) -> str:
    """The compact text block the host reads before narrating."""
    L = ["=" * 64]
    L.append(f"HUMAN KEEPER PACKET — turn {packet.get('turn')}  "
             f"[{packet.get('scenario')} / {packet.get('mode')}]")
    scene = packet.get("scene") or {}
    L.append(f"Scene: {scene.get('name', 'unknown')} ({scene.get('id', '?')})")
    acting = packet.get("acting") or []
    L.append("Acting: " + (", ".join(f"{a['name']} ({a['id']})" for a in acting)
                           or "(none)"))
    names = {a["id"]: a["name"] for a in acting}
    decl = packet.get("declarations") or {}
    if decl:
        L.append("Player declaration(s):")
        for cid, text in decl.items():
            L.append(f"  {names.get(cid, cid)}: \"{text}\"")
    dice = _dice_lines(packet)
    L.append("Adjudication & dice (engine-resolved, authoritative):")
    if dice:
        L.extend(("  " + d) for d in dice)
    else:
        L.append("  (no rolls this turn)")
    moves = _movement_lines(packet)
    L.append("World changes (already applied by the engine):")
    if moves:
        L.extend(("  " + m) for m in moves)
    else:
        L.append("  (none)")
    L.append("Relevant facts:")
    for line in room_view.render_room_text(packet.get("room_view") or {}).splitlines():
        L.append("  " + line)
    visits = packet.get("visits") or {}
    if visits:
        bits = []
        for cid, v in visits.items():
            who = (packet.get("character_names") or {}).get(cid, cid)
            bits.append(f"{who}: {'revisit' if v.get('seen_before') else 'FIRST VISIT'}"
                        f" (visits: {v.get('count', 0)})")
        L.append("  Visit status: " + "; ".join(bits))
    if packet.get("fronts"):
        L.append("  Fronts: " + json.dumps(packet["fronts"]))
    L.append("Restrictions:")
    L.extend(("  - " + r) for r in packet.get("restrictions") or HUMAN_KEEPER_RESTRICTIONS)
    L.append("-" * 64)
    L.append("Narrate now. Plain lines are public; /private <char_id>, /public,")
    L.append("/end to finish, /cancel to refund the turn, /skip for none, /help.")
    L.append("=" * 64)
    return "\n".join(L)


# ---------------------------------------------------------------- parser
def parse_human_keeper_response(segments, mode, status="ok") -> dict:
    """Convert collected host input into the internal response contract.

    `segments` is an ordered list of (channel, char_id, [lines]) tuples where
    channel is "public" or "private". The result is exactly the structure the
    AI clients return, with the channels a human cannot own left empty:
    dice_requests [] and state_delta {} (the human may not write state —
    proposal-only fields stay an AI/roadmap channel)."""
    public_parts = []
    private = {}
    for channel, char_id, lines in segments:
        text = "\n".join(lines).strip()
        if not text:
            continue
        if channel == "private" and char_id:
            private[char_id] = (private[char_id] + "\n" + text).strip() \
                if char_id in private else text
        else:
            public_parts.append(text)
    narration = "" if status == "skipped" else "\n".join(public_parts).strip()
    return {
        "mode": mode,
        "narration": narration,
        "private_narrations": private,
        "state_delta": {},
        "required_actions": "What do you do?",
        "dice_requests": [],
        "mode_switch": None,
    }


# ---------------------------------------------------------------- client
class HumanKeeperClient:
    """Duck-types the LLM client surface the Keeper uses, for a human host.

    `input_fn`/`output_fn` are injectable so tests (and future remote
    transports) never touch real stdin/stdout. There is intentionally no
    `query()` retry ladder: `narrate()` asks the host exactly once."""

    provider = "human"
    is_human = True
    default_model = heavy_model = "human"

    def __init__(self, config=None, debug=False, input_fn=None, output_fn=None,
                 timing_log=None):
        self.config = config or {}
        self.debug = debug
        self.input_fn = input_fn or input
        self.output_fn = output_fn or (lambda text="": print(text))
        self.timing_log = timing_log or HUMAN_TIMING_LOG

    def describe(self):
        return "human (local host narration)"

    def _print(self, text=""):
        self.output_fn(text)

    # ------------------------------------------------------------- input
    def _collect(self):
        """Read host lines until /end, /cancel, or /skip.

        Returns (status, segments); status is "ok", "cancelled", or
        "skipped". EOF on stdin is treated as /cancel — a closed terminal
        must never silently consume a turn."""
        segments = []          # [(channel, char_id, [lines])]
        channel, target = "public", None
        while True:
            prompt = "keeper> " if channel == "public" else f"keeper[{target}]> "
            try:
                line = self.input_fn(prompt)
            except EOFError:
                return "cancelled", segments
            cmd = line.strip()
            low = cmd.lower()
            if low == "/end":
                return "ok", segments
            if low == "/cancel":
                return "cancelled", segments
            if low == "/skip":
                return "skipped", segments
            if low == "/help":
                self._print(PROTOCOL_HELP)
                continue
            if low == "/public":
                channel, target = "public", None
                continue
            if low.startswith("/private"):
                parts = cmd.split(None, 1)
                if len(parts) < 2 or not parts[1].strip():
                    self._print("  [Usage: /private <character_id>]")
                    continue
                channel, target = "private", parts[1].strip()
                continue
            if low.startswith("/"):
                self._print(f"  [Unknown command '{cmd}' — /help lists the protocol.]")
                continue
            if segments and segments[-1][0] == channel and segments[-1][1] == target:
                segments[-1][2].append(line)
            else:
                segments.append((channel, target, [line]))

    # ------------------------------------------------------------- logging
    def _log(self, packet, status, elapsed, result, context=None):
        """One row per human turn — separate from the API meter.

        No budget, no tokens, no cost: those knobs do not exist for a human
        narrator. Never raises (diagnostics must not break a session)."""
        from src import latency as _lat
        rec = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "version": _lat.project_version(),
            "commit": _lat.git_commit(),
            "provider": "human",
            "scenario": packet.get("scenario"),
            "turn": packet.get("turn"),
            "resolution_mode": packet.get("mode"),
            "status": status,
            "elapsed_host_time": round(elapsed, 2),
            "narration_chars": len(result.get("narration") or ""),
            "private_note_count": len(result.get("private_narrations") or {}),
        }
        if context:
            for k in ("resolution_mode", "turn", "scenario", "source"):
                if k in context:
                    rec[k] = context[k]
        _lat.write_timing_row(self.timing_log, rec)

    # ------------------------------------------------------------- narrate
    def narrate(self, packet, timing=None, context=None) -> dict:
        """Show the packet, collect the host's narration, return the contract.

        Raises HumanKeeperCancelled on /cancel so the Keeper can refund the
        turn exactly like an LLM error — without consuming it."""
        t0 = time.perf_counter()
        self._print(render_human_keeper_packet(packet))
        status, segments = self._collect()
        elapsed = time.perf_counter() - t0
        result = parse_human_keeper_response(
            segments, packet.get("mode", "individual"), status=status)
        self._log(packet, status, elapsed, result, context)
        if self.debug:
            self._print("[human keeper — parsed response]\n"
                        + json.dumps(result, indent=2))
        if isinstance(timing, dict):
            timing["api_wait"] = 0.0     # no API: the meter stays at zero
            timing["parse"] = 0.0
        if status == "cancelled":
            raise HumanKeeperCancelled("narration cancelled by the human Keeper")
        return result

    def query(self, *args, **kwargs):
        """The human provider never answers AI-style queries."""
        raise RuntimeError(
            "HumanKeeperClient has no query() path — the Keeper calls "
            "narrate(packet) with an engine-built packet. provider=human "
            "makes no API calls, ever.")
