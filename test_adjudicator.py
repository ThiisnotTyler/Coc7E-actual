"""Offline adjudication suite (v2.8.1.2) — deterministic, no API, no dice.

Evaluates the action_phrases.jsonl corpus against the adjudicator's intent
frames (decision / skill / action_type / target / segment count), plus the
required live cases end-to-end through the resolver.

Run from the project root:  py test_adjudicator.py
Exit code 0 when every phrase adjudicates as expected.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.keeper import CoCKeeper
from src.character import Character
from src import items as items_mod

CORPUS = os.path.join("tests", "action_phrases.jsonl")

PASS = 0
FAILURES = []


def check(name, cond, detail=""):
    global PASS
    if cond:
        PASS += 1
    else:
        FAILURES.append(f"{name}  {detail}")


def make_fixture():
    """One room with everything the corpus needs: an NPC, a locked door
    object, a visible document, a key, exits, and an armed investigator."""
    with open("config/settings.json", encoding="utf-8") as f:
        cfg = json.load(f)
    cfg.setdefault("chronicle", {})["backend"] = "off"
    k = CoCKeeper(cfg, mock=True)
    k.load_scenario("data/scenarios/five-minute-house")
    # Suite hygiene: keep every save this fixture writes off the real
    # scenario id — a live campaign must survive a full test run.
    k.scenario_id = "rld-adjudicator"
    det = Character(
        id="det", name="Det", char_type="player",
        STR=60, CON=50, SIZ=50, DEX=60,
        skills={"Fighting_Brawl": 60, "Intimidate": 55, "Persuade": 50,
                "Firearms_Rifle_Shotgun": 70, "Spot_Hidden": 60,
                "Library_Use": 55, "Stealth": 40, "Listen": 50,
                "First_Aid": 45, "Locksmith": 50},
        location="house_hallway",
    )
    k.add_player(det)
    sg = items_mod.create_instance(k.item_templates["12_gauge_shotgun"],
                                   owner_id=det.id, registry=k.item_instances)
    det.inventory.append(sg.id)
    det.equipped_item_id = sg.id
    det.refresh_weapon_view()
    fl = items_mod.create_instance(k.item_templates["flashlight"],
                                   owner_id=det.id, registry=k.item_instances)
    det.inventory.append(fl.id)
    wh = items_mod.create_instance(k.item_templates["whiskey"],
                                   owner_id=det.id, registry=k.item_instances)
    det.inventory.append(wh.id)
    # everything the corpus needs lives in the hallway with the investigator
    hobbs = k.characters["mr_hobbs"]
    k.locations["house_study"].occupants.discard("mr_hobbs")
    hobbs.location = "house_hallway"
    k.locations["house_hallway"].occupants.add("mr_hobbs")
    items_mod.create_instance(k.item_templates["torn_letter"],
                              location_id="house_hallway",
                              registry=k.item_instances)
    return k, det


def resolve_target(k, frame):
    """Human-readable target name for corpus comparison."""
    if frame.target_type == "npc" and frame.target_id:
        c = k.characters.get(frame.target_id)
        return c.name.lower() if c else str(frame.target_id)
    if frame.target_type == "object" and frame.target_id:
        obj = k.world_objects.get(frame.target_id)
        return (obj.name.lower() if obj else str(frame.target_id))
    if frame.target_type in ("item", "document") and frame.target_id:
        inst = k.item_instances.get(frame.target_id)
        return inst.name.lower() if inst else str(frame.target_id)
    if frame.target_type == "exit" and frame.target_id:
        return str(frame.target_id).replace("_", " ")
    return ""


def evaluate_entry(k, det, entry):
    text = entry["text"]
    frames = k.adjudicator.adjudicate(k, det, text)
    if not frames:
        return [f"no frames produced for {text!r}"]
    primary = frames[0]
    errors = []

    if "frames" in entry and len(frames) != entry["frames"]:
        errors.append(f"frames: expected {entry['frames']}, got {len(frames)}")

    # expected decision applies to the FIRST roll-bearing frame when the
    # phrase is compound; otherwise the primary frame
    check_frame = primary
    if entry.get("frames", 1) > 1:
        for f in frames:
            if f.decision == "roll":
                check_frame = f
                break

    if "decision" in entry:
        exp = entry["decision"]
        if exp == "local":
            ok = all(f.decision in ("local", "passthrough") for f in frames) \
                and any(f.decision == "local" for f in frames)
            if not ok:
                errors.append(f"decision: expected local, got "
                              f"{[f.decision for f in frames]}")
        elif check_frame.decision != exp:
            errors.append(f"decision: expected {exp}, got {check_frame.decision}")
    if "skill" in entry and check_frame.skill != entry["skill"]:
        errors.append(f"skill: expected {entry['skill']}, got {check_frame.skill}")
    if "action_type" in entry and check_frame.action_type != entry["action_type"]:
        errors.append(f"action_type: expected {entry['action_type']}, "
                      f"got {check_frame.action_type}")
    if entry.get("nonlethal") and "nonlethal" not in check_frame.manner:
        errors.append("nonlethal: expected nonlethal manner")
    for key, want in (("target_npc", "npc"), ("target_object", "object"),
                      ("target_item", "item"), ("target_exit", "exit")):
        if key in entry:
            name = resolve_target(k, check_frame)
            if entry[key].lower() not in name:
                errors.append(f"{key}: expected {entry[key]!r} in target, "
                              f"got {name!r}")
    return errors


def main():
    k, det = make_fixture()

    entries = [json.loads(l) for l in open(CORPUS, encoding="utf-8") if l.strip()]
    for entry in entries:
        errs = evaluate_entry(k, det, entry)
        check(f"corpus: {entry['text']}", not errs, "; ".join(errs))

    print(f"== action_phrases.jsonl: {PASS}/{len(entries)} phrases as expected ==")

    # ---- required live cases, end-to-end through the resolver ----
    print("== live cases ==")

    def live(text, want_skill=None, want_nonlethal=False, want_decision=None,
             want_target=None):
        frames = k.adjudicator.adjudicate(k, det, text)
        f = frames[0] if frames else None
        ok = f is not None
        if ok and want_skill is not None:
            ok = f.skill == want_skill
        if ok and want_nonlethal:
            ok = "nonlethal" in f.manner
        if ok and want_decision is not None:
            ok = f.decision == want_decision
        if ok and want_target is not None:
            ok = want_target in resolve_target(k, f)
        check(f"live: {text}", ok,
              f.debug_line() if f else "no frames")
        return f

    live("I slam the buttstock into Hobbs and try to knock him out",
         want_skill="Fighting_Brawl", want_nonlethal=True, want_target="hobbs")
    live("roll intimidation to compell", want_skill="Intimidate",
         want_target="hobbs")
    live("roll strength for a round house kick", want_skill="Fighting_Brawl")
    live("I kick the study door down", want_skill="STR", want_target="door")
    live("I kick Hobbs in the ribs", want_skill="Fighting_Brawl",
         want_target="hobbs")
    live("I hit the road", want_decision="passthrough")
    live("I strike up a conversation", want_decision="passthrough")

    frames = k.adjudicator.adjudicate(k, det, "I blast the lock off, then kick it in")
    check("live: blast-then-kick is two frames",
          len(frames) == 2 and frames[0].action_type == "object_attack"
          and frames[1].action_type == "force_object",
          " | ".join(f.debug_line() for f in frames))
    frames = k.adjudicator.adjudicate(k, det, "I grab the letter and read it")
    check("live: grab-and-read is two local frames",
          len(frames) == 2 and all(f.decision == "local" for f in frames),
          " | ".join(f.debug_line() for f in frames))

    # resolver executes the compound without the LLM
    import contextlib
    import io as _io
    calls0 = k.gemini.calls
    buf = _io.StringIO()
    with contextlib.redirect_stdout(buf):
        k.take_turn({"det": "I grab the letter and read it"})
    check("resolver: grab-and-read calls no LLM and consumes no turn",
          k.gemini.calls == calls0 and k.turn == 0,
          f"calls={k.gemini.calls} turn={k.turn}")
    check("resolver: the letter was taken and read locally",
          "takes the" in buf.getvalue() and "reads the" in buf.getvalue(),
          buf.getvalue()[:200])

    # ==================== v2.8.1.3 adversarial ====================
    print("== v2.8.1.3 adversarial ==")

    class _SureDice:
        def skill_check(self, target, bonus=0, penalty=0):
            return 1, "Extreme"
        def d(self, sides, count=1):
            return sides * count
        def d100(self):
            return 1

    class _FailDice:
        def skill_check(self, target, bonus=0, penalty=0):
            return 99, "Failure"
        def d(self, sides, count=1):
            return 1
        def d100(self):
            return 99

    def fresh():
        kx, pc = make_fixture()
        kx.dice = _SureDice()
        kx.combat = type(kx.combat)(kx.spatial, kx.dice)
        return kx, pc

    # 1. conditional threat -> Intimidate, no ammo spent
    kx, pc = fresh()
    ammo0 = pc.weapon.ammo
    frames = kx.adjudicator.adjudicate(kx, pc, "Step away with your hands up or I will shoot you")
    f0 = frames[0]
    check("conditional threat is Intimidate, not a firearm attack",
          f0.skill == "Intimidate" and f0.action_type == "coercion",
          f0.debug_line())
    with contextlib.redirect_stdout(_io.StringIO()):
        kx.action_resolver.resolve(kx, pc, frames)
    check("conditional threat spends no ammunition",
          pc.weapon.ammo == ammo0)

    # 2. implied coercion
    kx, pc = fresh()
    frames = kx.adjudicator.adjudicate(
        kx, pc, "I want you to make them come here, or else I'll kill you in this room")
    check("implied coercion rolls Intimidate",
          frames[0].skill == "Intimidate" and frames[0].decision == "roll",
          frames[0].debug_line())

    # 3. grab/drag an NPC is handling, never a pickup
    kx, pc = fresh()
    frames = kx.adjudicator.adjudicate(kx, pc, "grab Hobbs and drag him to the hallway")
    check("grab NPC is npc_handling, not take_item",
          frames[0].action_type == "npc_handling",
          frames[0].debug_line())
    check("drag NPC binds the destination",
          frames[1].dest_id == "house_hallway",
          frames[1].debug_line())

    # 4. force-NPC is handling, not force_object
    kx, pc = fresh()
    frames = kx.adjudicator.adjudicate(kx, pc, "force Hobbs into the hallway and shut the door")
    check("force NPC is npc_handling, not force_object",
          frames[0].action_type == "npc_handling"
          and frames[0].skill in ("Fighting_Brawl", "STR", "Intimidate"),
          frames[0].debug_line())

    # 5. clarification blocks the WHOLE compound
    kx, pc = fresh()
    calls0 = kx.gemini.calls
    buf = _io.StringIO()
    with contextlib.redirect_stdout(buf):
        kx.take_turn({"det": "kick and demand answers"})
    check("a clarification anywhere blocks the whole declaration",
          kx.gemini.calls == calls0 and "Keeper" in buf.getvalue(),
          buf.getvalue()[:200])

    # 6. successful forced movement updates locations + door state
    kx, pc = fresh()
    hobbs = kx.characters["mr_hobbs"]
    for _who in (pc, hobbs):   # both start in the Study for this case
        kx.locations[_who.location].occupants.discard(_who.id)
        _who.location = "house_study"
        kx.locations["house_study"].occupants.add(_who.id)
    frames = kx.adjudicator.adjudicate(kx, pc, "force Hobbs into the hallway and shut the door")
    # realistic sequence: the door was opened first, so 'shut' means closed
    from src import room_view as _rv
    _rv._unlock_exit(kx, kx.locations["house_hallway"], "house_study")
    with contextlib.redirect_stdout(_io.StringIO()):
        kx.action_resolver.resolve(kx, pc, frames)
    check("forced move: NPC, player, scene, and door all update",
          hobbs.location == "house_hallway"
          and pc.location == "house_hallway"
          and kx.current_scene == "house_hallway"
          and kx.world_objects["study_door"].state == "closed",
          f"hobbs={hobbs.location} pc={pc.location} scene={kx.current_scene} "
          f"door={kx.world_objects['study_door'].state}")
    check("forced move: exit state follows the closed door",
          _rv.connection_state(kx.locations["house_hallway"], "house_study",
                               kx.world_objects) == "closed")

    # 7. failed forced movement cannot be narrated as successful
    kx, pc = make_fixture()
    kx.dice = _FailDice()
    kx.combat = type(kx.combat)(kx.spatial, kx.dice)
    hobbs = kx.characters["mr_hobbs"]
    for _who in (pc, hobbs):
        kx.locations[_who.location].occupants.discard(_who.id)
        _who.location = "house_study"
        kx.locations["house_study"].occupants.add(_who.id)
    frames = kx.adjudicator.adjudicate(kx, pc, "force Hobbs into the hallway and shut the door")
    with contextlib.redirect_stdout(_io.StringIO()):
        outcome = kx.action_resolver.resolve(kx, pc, frames)
    roll = outcome["rolls"][0]
    check("failed forced move: nobody moves, packet says so",
          hobbs.location == "house_study"
          and pc.location == "house_study"
          and roll.get("forced_move_failed") is True,
          f"hobbs={hobbs.location} roll={roll.get('level')}")

    # 8. fire without ignition fails locally
    kx, pc = fresh()
    for iid in list(pc.inventory):
        inst = kx.item_instances.get(iid)
        if inst is not None and inst.item_type == "light_source":
            pc.inventory.remove(iid)
    calls0 = kx.gemini.calls
    buf = _io.StringIO()
    with contextlib.redirect_stdout(buf):
        kx.take_turn({"det": "set fire to the study"})
    check("fire without an ignition source fails locally",
          "anything that can start a fire" in buf.getvalue()
          and kx.gemini.calls == calls0,
          buf.getvalue()[:200])

    # 9. ordinary entry never grants a passive inspection roll
    kx, pc = fresh()
    kx.locations["house_study"].entry_check = {}
    pkt = kx._movement_packet(pc, {"dest": "house_study", "origin": "house_hallway",
                                   "first": True, "triggers": []})
    check("no authored entry_check -> no passive roll in the packet",
          "entry_check" not in pkt)
    kx.locations["house_study"].entry_check = {"skill": "Spot_Hidden"}
    pkt = kx._movement_packet(pc, {"dest": "house_study", "origin": "house_hallway",
                                   "first": True, "triggers": []})
    check("authored entry_check -> the packet carries the roll",
          pkt.get("entry_check", {}).get("skill") == "Spot_Hidden")

    # 10. quoted threats spend no ammunition
    kx, pc = fresh()
    ammo0 = pc.weapon.ammo
    frames = kx.adjudicator.adjudicate(kx, pc, 'tell him "hands up or I shoot"')
    f0 = frames[0]
    check("quoted threat is coercion",
          f0.action_type == "coercion" and f0.skill == "Intimidate",
          f0.debug_line())
    with contextlib.redirect_stdout(_io.StringIO()):
        kx.action_resolver.resolve(kx, pc, frames)
    check("quoted threat spends no ammunition", pc.weapon.ammo == ammo0)

    # 11. explicit commitment still fires
    kx, pc = fresh()
    frames = kx.adjudicator.adjudicate(kx, pc, "I shoot him")
    check("explicit 'I shoot him' is a firearm attack",
          frames[0].action_type == "ranged_attack",
          frames[0].debug_line())

    # ==================== v2.8.1.7 P0-4 field phrases ====================
    print("== v2.8.1.7 field phrases ==")

    # 12. the grapple is conditional on the sweep — a failed sweep skips it
    kx, pc = make_fixture()
    kx.dice = _FailDice()
    kx.combat = type(kx.combat)(kx.spatial, kx.dice)
    hobbs = kx.characters["mr_hobbs"]
    hp0 = hobbs.hp
    frames = kx.adjudicator.adjudicate(
        kx, pc, "sweep kick Hobbs off his feet and grapple on top of him")
    check("sweep+grapple splits into two frames, grapple conditional",
          len(frames) == 2 and frames[1].conditional_on == 0
          and frames[0].skill == "Fighting_Brawl"
          and frames[1].skill == "Fighting_Brawl",
          " | ".join(f.debug_line() for f in frames))
    with contextlib.redirect_stdout(_io.StringIO()):
        outcome = kx.action_resolver.resolve(kx, pc, frames)
    check("failed sweep skips the grapple (no prone AND pinned from one frame)",
          any(c.get("skipped") for c in outcome["components"])
          and len(outcome["rolls"]) == 1 and hobbs.hp == hp0,
          f"components={outcome['components']}")

    # 13. shotgun threat: Intimidate, no shell, no firearm attack
    kx, pc = fresh()
    ammo0 = pc.weapon.ammo
    frames = kx.adjudicator.adjudicate(
        kx, pc, "train the shotgun on Hobbs' head as a threat")
    check("shotgun threat rolls Intimidate with the shotgun as instrument",
          frames[0].action_type == "coercion"
          and frames[0].skill == "Intimidate"
          and frames[0].instrument_id is not None,
          frames[0].debug_line())
    with contextlib.redirect_stdout(_io.StringIO()):
        kx.action_resolver.resolve(kx, pc, frames)
    check("shotgun threat spends no ammunition", pc.weapon.ammo == ammo0)

    # 14. quoted threat + parenthetical slam: both survive, both roll
    kx, pc = fresh()
    frames = kx.adjudicator.adjudicate(
        kx, pc, '"Stop the counting or I will kill you" '
                '(grabs Hobbs and judo slams him)')
    check("parenthetical action is not discarded (two frames)",
          len(frames) == 2,
          " | ".join(f.debug_line() for f in frames))
    check("threat frame is Intimidate; the judo slam must roll",
          frames[0].action_type == "coercion"
          and frames[0].skill == "Intimidate"
          and frames[1].decision == "roll"
          and frames[1].skill == "Fighting_Brawl",
          " | ".join(f.debug_line() for f in frames))

    # 15. question/burn/movement: targets stay on the right things
    kx, pc = fresh()
    frames = kx.adjudicator.adjudicate(
        kx, pc, "where are the papers Hobbs? we gotta burn them and go now!")
    check("question/fire/movement splits into three frames",
          len(frames) == 3,
          " | ".join(f.debug_line() for f in frames))
    check("the question is addressed to Hobbs and rolls nothing",
          frames[0].decision == "passthrough"
          and frames[0].target_type == "npc"
          and "hobbs" in resolve_target(kx, frames[0]),
          frames[0].debug_line())
    check("'burn them' targets the papers, never Hobbs",
          frames[1].action_type == "fire_setting"
          and frames[1].target_type == "document"
          and "letter" in resolve_target(kx, frames[1]),
          frames[1].debug_line())
    check("destination-less movement stays unresolved",
          frames[2].decision == "passthrough",
          frames[2].debug_line())

    if FAILURES:
        print(f"\n{len(FAILURES)} FAILURE(S):")
        for f_ in FAILURES[:40]:
            print("  FAIL:", f_)
        print(f"\nADJUDICATOR TESTS FAILED ({PASS} passed, {len(FAILURES)} failed)")
        sys.exit(1)
    print(f"\nALL ADJUDICATOR TESTS PASSED ({PASS} checks)")


if __name__ == "__main__":
    main()
