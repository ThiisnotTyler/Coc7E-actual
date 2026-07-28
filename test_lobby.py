"""Lobby hotfix tests — save deletion from the scenario menu + clean exits.

Field request: 'I need a way to delete save files from the selection menu
and be able to exit out of the scenario selection screen, its really
annoying manually deleting save files.'

Pure-offline: FakeIO feeds scripted answers, scenarios/saves are built in a
temp cwd. Run from the project root:  py test_lobby.py
"""
import json
import os
import sys
import tempfile
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.lobby import (scan_scenarios, choose_scenario, choose_investigators,
                       delete_save)

CHECKS = []


def check(name, cond, detail=""):
    CHECKS.append((name, bool(cond), detail))
    print(f"  {'ok' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail and not cond else ""))


class FakeIO:
    """Scripted console: answers are consumed in order, all output recorded."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.lines = []

    def say(self, msg=""):
        self.lines.append(str(msg))

    def ask(self, prompt=""):
        self.lines.append(str(prompt))
        if not self.answers:
            raise AssertionError("FakeIO ran out of scripted answers — the menu asked again")
        return self.answers.pop(0)

    def text(self):
        return "\n".join(self.lines)


def _mk_scenario(root, sid, title="Alpha"):
    path = os.path.join(root, "data", "scenarios", sid)
    os.makedirs(path, exist_ok=True)
    with open(os.path.join(path, "scenario.json"), "w", encoding="utf-8") as f:
        json.dump({"id": sid, "title": title, "era": "1920s",
                   "expected_sessions": 1, "description": "test"}, f)
    return path


def _mk_save(root, sid, turn=7, corrupt=False):
    path = os.path.join(root, "saves", sid)
    os.makedirs(path, exist_ok=True)
    with open(os.path.join(path, "world-state.json"), "w", encoding="utf-8") as f:
        f.write("{not json" if corrupt else json.dumps({"turn": turn}))
    # the recursive-saves junk an old path bug produced must die with the save
    nested = os.path.join(path, "saves", sid)
    os.makedirs(nested, exist_ok=True)
    with open(os.path.join(nested, "world-state.json"), "w", encoding="utf-8") as f:
        json.dump({"turn": turn}, f)
    return path


class _TempCwd:
    def __enter__(self):
        self.old = os.getcwd()
        self.tmp = tempfile.mkdtemp(prefix="lobbytest_")
        os.chdir(self.tmp)
        return self.tmp

    def __exit__(self, *a):
        os.chdir(self.old)


def _char(name):
    return types.SimpleNamespace(name=name, extra={"occupation": "Cop", "age": 30},
                                 skills={"Fighting_Brawl": 40, "Dodge": 35})


print("== scan_scenarios: save detection ==")
with _TempCwd():
    _mk_scenario(".", "alpha")
    _mk_scenario(".", "beta", "Beta")
    _mk_save(".", "alpha", turn=7)
    entries = scan_scenarios()
    by_id = {e["id"]: e for e in entries}
    check("alpha save_turn read", by_id["alpha"]["save_turn"] == 7)
    check("alpha has_save True", by_id["alpha"]["has_save"] is True)
    check("beta save_turn None", by_id["beta"]["save_turn"] is None)
    check("beta has_save False", by_id["beta"]["has_save"] is False)

print("== scan_scenarios: corrupt save still deletable ==")
with _TempCwd():
    _mk_scenario(".", "alpha")
    _mk_save(".", "alpha", corrupt=True)
    e = scan_scenarios()[0]
    check("corrupt save: save_turn None", e["save_turn"] is None)
    check("corrupt save: has_save True", e["has_save"] is True)

print("== choose_scenario: delete save with confirm ==")
with _TempCwd():
    _mk_scenario(".", "alpha")
    _mk_save(".", "alpha", turn=7)
    entries = scan_scenarios()
    io = FakeIO(["d 1", "y", "1"])
    picked = choose_scenario(io, entries)
    check("menu eventually returns the entry", picked is not None and picked["id"] == "alpha")
    check("save folder deleted", not os.path.isdir(os.path.join("saves", "alpha")))
    check("badge cleared (save_turn None)", entries[0]["save_turn"] is None)
    check("badge cleared (has_save False)", entries[0]["has_save"] is False)
    check("confirm was asked", "Delete the save for Alpha" in io.text())

print("== choose_scenario: delete declined keeps the save ==")
with _TempCwd():
    _mk_scenario(".", "alpha")
    _mk_save(".", "alpha", turn=7)
    entries = scan_scenarios()
    io = FakeIO(["d 1", "n", "1"])
    picked = choose_scenario(io, entries)
    check("decline keeps save folder", os.path.isfile(os.path.join("saves", "alpha", "world-state.json")))
    check("decline keeps badge", entries[0]["save_turn"] == 7)
    check("[Kept.] printed", "[Kept.]" in io.text())

print("== choose_scenario: delete removes nested junk ==")
with _TempCwd():
    _mk_scenario(".", "alpha")
    _mk_save(".", "alpha", turn=7)  # includes saves/alpha/saves/alpha nesting
    io = FakeIO(["d 1", "y", "1"])
    choose_scenario(io, scan_scenarios())
    check("nested saves/ tree gone entirely", not os.path.exists(os.path.join("saves", "alpha")))

print("== choose_scenario: aliases and errors ==")
with _TempCwd():
    _mk_scenario(".", "alpha")
    _mk_save(".", "alpha")
    io = FakeIO(["delete 1", "yes", "1"])
    choose_scenario(io, scan_scenarios())
    check("'delete 1' alias works", not os.path.isdir(os.path.join("saves", "alpha")))
with _TempCwd():
    _mk_scenario(".", "alpha")
    io = FakeIO(["d 1", "1"])          # no save to delete
    choose_scenario(io, scan_scenarios())
    check("'d' on saveless entry says so", "has no save to delete" in io.text())
with _TempCwd():
    _mk_scenario(".", "alpha")
    io = FakeIO(["d 9", "1"])          # out of range
    choose_scenario(io, scan_scenarios())
    check("'d 9' out of range re-prompts", "pick a number from the list" in io.text())

print("== choose_scenario: quit ==")
with _TempCwd():
    _mk_scenario(".", "alpha")
    for word in ("q", "quit", "exit"):
        io = FakeIO([word])
        check(f"'{word}' returns None", choose_scenario(io, scan_scenarios()) is None)

print("== choose_scenario: menu advertises the commands ==")
with _TempCwd():
    _mk_scenario(".", "alpha")
    io = FakeIO(["1"])
    choose_scenario(io, scan_scenarios())
    check("commands line shown", "'d #' to delete that save, 'q' to quit" in io.text())

print("== choose_investigators: quit ==")
roster = [_char("Ann"), _char("Bob")]
pregens = [_char("Pre1")]
for word in ("q", "quit", "exit"):
    io = FakeIO([word])
    check(f"'{word}' returns None", choose_investigators(io, roster, pregens, on_new=None) is None)
io = FakeIO(["1,2"])
check("normal multi-pick still works",
      [c.name for c in choose_investigators(io, roster, pregens)] == ["Ann", "Bob"])
io = FakeIO(["all"])
check("'all' still works", len(choose_investigators(io, roster, pregens)) == 2)

print("== delete_save: direct behavior ==")
with _TempCwd():
    check("missing save returns False", delete_save("ghost") is False)
    _mk_save(".", "alpha")
    check("existing save returns True", delete_save("alpha") is True)
    check("folder really gone", not os.path.isdir(os.path.join("saves", "alpha")))
    check("second delete returns False", delete_save("alpha") is False)

print("== main.py lobby wiring ==")
import py_compile
for f in ("src/main.py", "src/lobby.py"):
    try:
        py_compile.compile(f, doraise=True)
        check(f"{f} compiles", True)
    except py_compile.PyCompileError as exc:
        check(f"{f} compiles", False, str(exc))
src = open("src/main.py", encoding="utf-8").read()
check("main handles scenario-menu None", "if chosen is None:" in src)
check("main handles investigator-menu None", src.count("if party is None:") >= 2)

failed = [c for c in CHECKS if not c[1]]
print(f"\n{'=' * 50}\nLOBBY TESTS: {len(CHECKS) - len(failed)}/{len(CHECKS)} passed")
if failed:
    print("FAILURES:")
    for name, _, detail in failed:
        print(f"  - {name}" + (f"  — {detail}" if detail else ""))
    sys.exit(1)
print("ALL LOBBY TESTS PASSED")
