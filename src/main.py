"""Entry point.

Run from the project root either way:
    python -m src.main --mock                 # free offline test of the full loop
    python -m src.main                        # lobby -> scenario -> party -> live session
    python -m src.main --scenario data/scenarios/the-haunting   # skip the lobby
    python -m src.main --reset                # wipe the save and start fresh
    python -m src.main --new-character        # 7e investigator wizard, then exit
    python src/main.py --mock                 # also works (path shim below)

v2.7.0: startup lobby. With no --scenario and a real terminal, a scenario
menu (scan_scenarios -> choose_scenario) and, on a fresh campaign, an
investigator menu (choose_investigators) run before the session goes hot.
Headless runs (redirected stdin, CI) never prompt: they resolve the legacy
default scenario and load the whole roster / pregens exactly as pre-v2.7.
"""
import argparse
import copy
import json
import os
import sys

# Path shim: make `python src/main.py` behave like `python -m src.main`
if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "src"

from src.keeper import CoCKeeper
from src.character import Character
from src.lobby import scan_scenarios, choose_scenario, choose_investigators


def default_investigators() -> list:
    """Pregens from the setup guide — edit these or add 'investigators' to scenario.json."""
    from src.charcreate import WEAPONS   # lazy: keeps module import light
    return [
        Character(
            id="eleanor_vance", name="Eleanor Vance", char_type="player", owner="player1",
            STR=50, CON=55, SIZ=60, DEX=65, APP=70, INT=75, POW=60, EDU=80,
            skills={"Spot_Hidden": 45, "Library_Use": 70, "Persuade": 55,
                    "Firearms_Handgun": 50, "Fighting_Brawl": 30, "Listen": 40},
            # v2.7.3: a Firearms_Handgun 50 sheet with NO gun rolled every
            # 'shoot' as a brawl. She carries what her sheet says.
            weapon=copy.copy(WEAPONS[".32 revolver"]),
            location="corbitt_house_exterior",
        ),
        Character(
            id="samuel_carter", name="Samuel Carter", char_type="player", owner="player2",
            STR=45, CON=50, SIZ=55, DEX=50, APP=60, INT=70, POW=55, EDU=85,
            skills={"History": 65, "Occult": 40, "Library_Use": 60, "Spot_Hidden": 35},
            location="corbitt_house_exterior",
        ),
        Character(
            id="martha_finn", name="Martha Finn", char_type="player", owner="player3",
            STR=55, CON=60, SIZ=50, DEX=55, APP=65, INT=60, POW=65, EDU=70,
            skills={"First_Aid": 60, "Listen": 50, "Medicine": 45, "Spot_Hidden": 40},
            location="corbitt_house_exterior",
        ),
    ]


def build_parser() -> argparse.ArgumentParser:
    """CLI definition, extracted so the test-suite can pin it.

    Regression (v2.4.1, field report): a machine running a stale pre-v2.4
    main.py died on `py -m src.main --new-character` with
    "unrecognized arguments: --new-character". test_engine.py now parses
    the flag and exercises the wizard routing, so a stale or reverted
    parser fails loudly offline instead of at the table.
    """
    parser = argparse.ArgumentParser(description="CoC 7e LLM Keeper")
    parser.add_argument("--scenario", default=None,
                        help="Path to the scenario folder (default: interactive "
                             "menu at a terminal; the-haunting when headless)")
    parser.add_argument("--campaign", default="Session 1", help="Campaign label")
    parser.add_argument("--mock", action="store_true",
                        help="Offline mode: no API key needed, no tokens spent")
    parser.add_argument("--reset", action="store_true",
                        help="Delete the saved world state and start fresh")
    parser.add_argument("--new-character", action="store_true",
                        help="Run the CoC 7e investigator creation wizard, then exit")
    parser.add_argument("--debug", action="store_true",
                        help="Print per-attempt LLM timing (model, seconds, token "
                             "budget) as turns run; versioned attempt history lands "
                             "in logs/llm_timing.jsonl and per-turn pipeline rows in "
                             "logs/turn_timing.jsonl either way")
    parser.add_argument("--human-keeper", action="store_true",
                        help="A human host narrates instead of an AI API "
                             "(v2.8.1.5): the engine shows a compact outcome packet "
                             "and reads multiline narration from the terminal. No "
                             "API key, no tokens, no timeout — same as setting "
                             "llm.provider to 'human'")
    return parser


def resolve_default_scenario(root: str = "data/scenarios") -> str:
    """Headless default when no --scenario is given and nobody is there to
    ask: the-haunting if present, else the first scenario in the folder."""
    entries = scan_scenarios(root)
    if not entries:
        return "data/scenarios/the-haunting"   # legacy default; load errors read clearly
    for e in entries:
        if e["id"] == "the-haunting":
            return e["path"]
    return entries[0]["path"]


def main(argv=None):
    args = build_parser().parse_args(argv)

    with open("config/settings.json", encoding="utf-8") as f:
        config = json.load(f)

    if args.debug:
        config.setdefault("llm", {})["debug"] = True
        _llm = config["llm"]
        _eff_heavy = _llm.get("max_output_tokens_heavy") or _llm.get("max_output_tokens")
        print(f"[llm config] path=config/settings.json "
              f"default_budget={_llm.get('max_output_tokens')} "
              f"heavy_budget={_llm.get('max_output_tokens_heavy')} "
              f"override_budget=None "
              f"effective_default={_llm.get('max_output_tokens')} "
              f"effective_heavy={_eff_heavy}")

    if args.new_character:
        from src.charcreate import create_character_interactive
        create_character_interactive(config)
        return

    if args.human_keeper:
        # v2.8.1.5: the flag and "provider": "human" are the same switch.
        config.setdefault("llm", {})["provider"] = "human"
        print("[Human Keeper mode: no API calls — the host narrates "
              "from engine-built packets.]")

    keeper = CoCKeeper(config, mock=args.mock)

    # v2.7.0 startup lobby, screen one: the scenario. Only when the user is
    # actually there to answer — a terminal with no --scenario flag.
    lobby_on = bool(config.get("game", {}).get("startup_menu", True))
    interactive = sys.stdin.isatty()
    scenario_path = args.scenario
    if scenario_path is None:
        if lobby_on and interactive:
            from src.charcreate import ConsoleIO
            entries = scan_scenarios()
            scenario_path = (choose_scenario(ConsoleIO(), entries)["path"]
                             if entries else resolve_default_scenario())
        else:
            scenario_path = resolve_default_scenario()
            print(f"[Headless start: no --scenario given; defaulting to {scenario_path}]")
    keeper.load_scenario(scenario_path)

    if args.reset and os.path.exists(keeper.save_path):
        os.remove(keeper.save_path)
        print(f"[Save wiped: {keeper.save_path}]")

    if keeper.load_state():
        print(f"[Resumed from {keeper.save_path} — turn {keeper.turn}]")
    else:
        from src.charcreate import load_roster, ConsoleIO
        roster = load_roster()
        party = None
        if lobby_on and interactive:
            # Screen two: the party. 'new' runs the 7e wizard mid-menu and
            # re-lists the grown roster; the wizard import stays lazy so the
            # --new-character monkeypatch path in test_engine keeps working.
            def _run_wizard_and_reload():
                from src.charcreate import create_character_interactive, load_roster as _lr
                create_character_interactive(config)
                return _lr()

            party = choose_investigators(ConsoleIO(), roster, default_investigators(),
                                         on_new=_run_wizard_and_reload)
        if party is None:   # headless legacy behavior
            party = roster if roster else default_investigators()
        for inv in party:
            if inv.location in ("unknown", ""):
                inv.location = keeper.current_scene
            keeper.add_player(inv)
        print(f"[Fresh campaign: {len(party)} investigator(s) ready]")

    keeper.run_session()


if __name__ == "__main__":
    main()
