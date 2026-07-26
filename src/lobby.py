"""Startup lobby — scenario & investigator selection screens (v2.7.0).

Field request: 'I want to see a scenario/campaign selection screen on
startup of the actual game and a character selection screen prior to going
hot into the game itself.'

Both screens are pure functions over an injected IO object (ConsoleIO at the
terminal, ScriptedIO in tests) so the whole flow is testable offline without
a single prompt ever blocking CI. main.py only calls these when stdin is a
real terminal AND --scenario was not given; headless runs fall back to
resolve_default_scenario() + the legacy whole-roster/pregen behavior.
"""
import json
import os
import re


def scan_scenarios(root: str = "data/scenarios") -> list:
    """Every folder under root holding a scenario.json becomes a menu entry.

    Entry: {id, path, title, era, expected_sessions, description, save_turn}.
    save_turn is the resumed turn number if saves/<id>/world-state.json
    exists, else None — shown in the menu as '[save: turn N]'. A malformed
    scenario.json is skipped, not fatal: the lobby must never block play
    because one half-written homebrew folder is in the directory.
    """
    entries = []
    if not os.path.isdir(root):
        return entries
    for name in sorted(os.listdir(root)):
        path = os.path.join(root, name)
        sj = os.path.join(path, "scenario.json")
        if not os.path.isfile(sj):
            continue
        try:
            with open(sj, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        sid = data.get("id", name)
        save_turn = None
        save_file = os.path.join("saves", sid, "world-state.json")
        if os.path.exists(save_file):
            try:
                with open(save_file, encoding="utf-8") as f:
                    save_turn = json.load(f).get("turn")
            except (json.JSONDecodeError, OSError):
                save_turn = None
        entries.append({
            "id": sid,
            "path": path,
            "title": data.get("title", sid),
            "era": data.get("era", ""),
            "expected_sessions": data.get("expected_sessions", "?"),
            "description": data.get("description", ""),
            "save_turn": save_turn,
        })
    return entries


def choose_scenario(io, entries: list) -> dict:
    """Numbered scenario menu. Re-prompts on garbage; returns the entry."""
    io.say("\n" + "=" * 60)
    io.say(" CHOOSE YOUR SCENARIO")
    io.say("=" * 60)
    for i, e in enumerate(entries, 1):
        save = f"   [save: turn {e['save_turn']}]" if e["save_turn"] is not None else ""
        io.say(f"  {i}. {e['title']} ({e['era']}, ~{e['expected_sessions']} sessions){save}")
        if e.get("description"):
            io.say(f"       {e['description']}")
    while True:
        raw = io.ask("\nScenario # > ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(entries):
            return entries[int(raw) - 1]
        io.say("  pick a number from the list.")


def choose_investigators(io, roster: list, pregens: list, on_new=None) -> list:
    """Character selection before the session goes hot.

    Picks from the roster by number ('1,3' multi-selects, duplicates
    collapse), 'all', 'pregens', or 'new' (runs the creation wizard via the
    on_new callback, which must return the reloaded roster). An empty roster
    with no wizard falls straight back to the pregens without prompting —
    there is nothing meaningful to ask.
    """
    if not roster and on_new is None:
        io.say("[No investigators on the roster yet — using the 3 pregens. "
               "--new-character creates your own.]")
        return list(pregens)
    while True:
        io.say("\n" + "-" * 60)
        io.say(" CHOOSE YOUR INVESTIGATORS  (comma-separated, e.g. 1,3)")
        io.say("-" * 60)
        for i, c in enumerate(roster, 1):
            occ = c.extra.get("occupation", "?")
            age = c.extra.get("age", "?")
            top = sorted(c.skills.items(), key=lambda kv: -kv[1])[:3]
            tops = ", ".join(f"{s.replace('_', ' ')} {v}%" for s, v in top) or "no skills"
            io.say(f"  {i}. {c.name} — {occ}, {age}  [{tops}]")
        io.say("  a. all of the above")
        io.say("  p. use the 3 pregens instead")
        if on_new:
            io.say("  n. create a new investigator (wizard)")
        raw = io.ask("\nInvestigators > ").strip().lower()
        if raw in ("a", "all"):
            return list(roster)
        if raw in ("p", "pre", "pregens"):
            return list(pregens)
        if raw in ("n", "new") and on_new:
            grown = on_new()
            if grown:
                roster = grown
            continue
        toks = [t for t in re.split(r"[,\s]+", raw) if t]
        picks, seen, ok = [], set(), bool(toks)
        for t in toks:
            if not t.isdigit() or not 1 <= int(t) <= len(roster):
                ok = False
                break
            if int(t) not in seen:
                seen.add(int(t))
                picks.append(roster[int(t) - 1])
        if ok and picks:
            return picks
        io.say("  pick numbers from the list, 'all', 'pregens'"
               + (", or 'new'." if on_new else "."))
