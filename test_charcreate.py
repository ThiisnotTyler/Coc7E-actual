"""CoC 7e character-creation rules tests — offline, no API.

Run from the project root:  python test_charcreate.py
Verifies the rulebook math (age table, EDU checks, formulas, credit rating,
skill cap, allocation rules) and a fully scripted end-to-end wizard run.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.charcreate import (
    QUICKFIRE_ARRAY, QUICK_SKILLS_ARRAY, POINT_BUY_TOTAL,
    roll_characteristics, roll_luck, validate_point_buy, age_bracket,
    apply_age, parse_formula, occupation_skill_points, resolve_occupation_skills,
    validate_credit_rating, canon_skill, _allocate_loop, create_character_interactive,
    load_roster, ScriptedIO,
)

PASS = 0


def check(name, cond):
    global PASS
    assert cond, f"FAIL: {name}"
    PASS += 1
    print(f"  ok  {name}")


class FakeDice:
    """Deterministic dice: d100 -> 1 (EDU checks always fail), d(n,count) -> count."""
    def d100(self):
        return 1

    def d(self, sides, count=1):
        return count


class MidDice:
    def d100(self):
        return 50

    def d(self, sides, count=1):
        return count * (sides // 2 + 1)


print("== characteristics generation ==")
check("quick-fire array matches handbook", sorted(QUICKFIRE_ARRAY) == [40, 50, 50, 50, 60, 60, 70, 80])
check("quick-skills array (8 occ skills + CR)", sorted(QUICK_SKILLS_ARRAY) == [40, 40, 40, 50, 50, 50, 60, 60, 70])
stats = roll_characteristics(FakeDice())
check("min-roll stats: 3D6x5=15, (2D6+6)x5=40",
      stats["STR"] == 15 and stats["SIZ"] == 40 and stats["EDU"] == 40)
stats2 = roll_characteristics(MidDice())
check("rolled stats in legal range", all(15 <= v <= 90 for v in stats2.values()))
check("teen luck is best of two", roll_luck(MidDice(), best_of_two=True) >= roll_luck(FakeDice()))

print("== point buy ==")
good = {"STR": 50, "CON": 50, "SIZ": 60, "DEX": 60, "APP": 50, "INT": 70, "POW": 60, "EDU": 60}
check("valid 460 spread passes", validate_point_buy(good) == [])
bad_total = dict(good, STR=49)
check("non-460 total rejected", any("460" in e for e in validate_point_buy(bad_total)))
bad_int = dict(good, INT=30, EDU=100 - 40)  # keep sum 460
check("INT below 40 rejected", any("INT" in e for e in validate_point_buy(bad_int)))
bad_max = dict(good, STR=95, CON=45)
check("stat above 90 rejected", any("90" in e for e in validate_point_buy(bad_max)))

print("== age modifiers (Investigator Handbook table) ==")
s = {k: 50 for k in ("STR", "CON", "SIZ", "DEX", "APP", "INT", "POW", "EDU")}
rep = apply_age(s, 17, {"STR": 5}, FakeDice())
check("teen: -5 STR and -5 EDU", s["STR"] == 45 and s["EDU"] == 45)
check("teen: no EDU checks, no MOV penalty", rep["edu_checks"] == [] and rep["mov_penalty"] == 0)

s = {k: 50 for k in ("STR", "CON", "SIZ", "DEX", "APP", "INT", "POW", "EDU")}
rep = apply_age(s, 45, {"STR": 3, "CON": 2, "APP": 5}, FakeDice())
check("40s: -5 among STR/CON/DEX, APP -5", s["STR"] == 47 and s["CON"] == 48 and s["APP"] == 45)
check("40s: 2 EDU checks, MOV -1", len(rep["edu_checks"]) == 2 and rep["mov_penalty"] == 1)

s = {k: 90 for k in ("STR", "CON", "SIZ", "DEX", "APP", "INT", "POW", "EDU")}
rep = apply_age(s, 75, {"STR": 40, "APP": 20}, MidDice())
check("70s: -40 phys, APP -20, 4 checks, MOV -4",
      s["STR"] == 50 and s["APP"] == 70 and len(rep["edu_checks"]) == 4 and rep["mov_penalty"] == 4)
check("70s EDU check can only raise EDU", s["EDU"] >= 90)
try:
    apply_age({k: 50 for k in good}, 45, {"STR": 2, "APP": 5}, FakeDice())
    raise SystemExit("FAIL: expected deduction mismatch error")
except ValueError:
    check("wrong physical deduction total rejected", True)

print("== occupation formulas & skills ==")
check("formula parse", parse_formula("EDU*2+DEX*2|STR*2") == [[("EDU", 2)], [("DEX", 2), ("STR", 2)]])
pi = {"name": "Private Investigator", "skill_points": "EDU*2+DEX*2|STR*2",
      "credit_rating": [9, 30], "skills": []}
st = {"STR": 50, "DEX": 60, "EDU": 60}
check("EDU*2+DEX*2 = 240", occupation_skill_points(pi, st, {"DEX|STR": "DEX"}) == 240)
check("EDU*2+STR*2 = 220", occupation_skill_points(pi, st, {"DEX|STR": "STR"}) == 220)
prof = {"name": "Professor", "skill_points": "EDU*4",
        "skills": ["Library Use", "Other Language", "Own Language", "Psychology",
                   {"choose": 2, "from": "any"}]}
picks = iter(["Occult", "Occult"])   # duplicate 'any' choice must dedup
skills = resolve_occupation_skills(prof, lambda p, o: next(picks))
check("fixed skills canonicalized", "Library_Use" in skills and "Language_Own" in skills)
check("duplicate choices deduped", skills.count("Occult") == 1 and len(skills) == 5)
check("canon: firearms parenthetical", canon_skill("Firearms (Rifle/Shotgun)") == "Firearms_Rifle_Shotgun")

print("== choice groups with multi-word skills (v2.4.2 field bug) ==")
# Field report: Soldier's "choose 2 from [First Aid, Mechanical Repair,
# Other Language]" crashed with ValueError: list.remove(x): x not in list —
# the interactive chooser returned canonical "First_Aid" while the pool held
# raw "First Aid". 10 of 15 shipped occupations contain such options, and
# every pick of one crashed. The resolver must consume the pool whether the
# chooser returns raw or canonicalized names.
sold = {"name": "Soldier", "skill_points": "EDU*2+DEX*2|STR*2",
        "credit_rating": [9, 30],
        "skills": ["Dodge", {"choose": 1, "from": ["Climb", "Swim"]},
                   {"choose": 2, "from": ["First Aid", "Mechanical Repair", "Other Language"]}]}
picks = iter(["Climb", "First Aid", "Mechanical Repair"])     # raw chooser results
sk = resolve_occupation_skills(sold, lambda p, o: next(picks))
check("raw multi-word picks resolve",
      sk == ["Dodge", "Climb", "First_Aid", "Mechanical_Repair"])
picks = iter(["Swim", "First_Aid", "Other_Language"])         # pre-canonicalized results
sk = resolve_occupation_skills(sold, lambda p, o: next(picks))
check("canonicalized picks tolerated, pool still consumed",
      sk == ["Dodge", "Swim", "First_Aid", "Other_Language"])

print("== credit rating ==")
check("CR inside range ok", validate_credit_rating(30, [9, 30]) == [])
check("CR below min rejected", validate_credit_rating(8, [9, 30]) != [])
check("CR above max rejected", validate_credit_rating(31, [9, 30]) != [])

print("== allocation rules ==")
io = ScriptedIO(["Cthulhu Mythos 5", "Stealth 5", "Spot Hidden 20", "Spot Hidden 10"])
vals = _allocate_loop(io, {}, 10, {"Spot_Hidden"}, 75, {"DEX": 50, "EDU": 50}, "occupation")
check("mythos/non-occ/over-budget rejected, then accepted",
      vals["Spot_Hidden"] == 35 and "Stealth" not in vals)
io2 = ScriptedIO(["Spot Hidden 60", "Spot Hidden 50", "Stealth 50"])
vals2 = _allocate_loop(io2, {}, 100, None, 75, {"DEX": 50, "EDU": 50}, "interest")
check("cap 75 enforced (25+60=85 rejected), 75 accepted",
      vals2["Spot_Hidden"] == 75 and vals2["Stealth"] == 70)

print("== full wizard (scripted, deterministic) ==")
answers = [
    "Test Person", "player1", "30",          # name, owner, age
    "3",                                      # point-buy
    "50", "50", "60", "60", "50", "70", "60", "60",   # stats (=460)
    "1",                                      # occupation: Investigative Journalist
    "4",                                      # interpersonal: Persuade
    "Spot Hidden", "Stealth",                 # 2 'any' occupation picks
    "1",                                      # standard allocation
    "Library Use 55", "Psychology 40", "Spot Hidden 50",
    "Credit Rating 30", "History 45", "Persuade 20",  # 240 occupation points
    "Stealth 55", "First Aid 45", "Listen 40",        # 140 personal (INT 70 x2)
    "1",                                      # weapon: none
]
import tempfile as _tempfile
_TMP_ROSTER_DIR = _tempfile.mkdtemp(prefix="coc7-charcreate-")
roster = os.path.join(_TMP_ROSTER_DIR, "investigators.json")
char = create_character_interactive(config={}, io=ScriptedIO(answers),
                                    dice=FakeDice(), roster_path=roster)
check("wizard produced a Character", char.name == "Test Person" and char.char_type == "player")
check("Library Use 20+55=75", char.skills["Library_Use"] == 75)
check("Credit Rating 30", char.skills["Credit_Rating"] == 30)
check("First Aid 30+45=75", char.skills["First_Aid"] == 75)
check("SAN = POW", char.san == 60)
check("HP = (CON+SIZ)/10", char.max_hp == 11)
check("MOV 8 (DEX=SIZ)", char.move == 8)
check("occupation recorded", char.extra.get("occupation") == "Investigative Journalist")
loaded = load_roster(roster)
check("roster round-trip", len(loaded) == 1 and loaded[0].skills["Library_Use"] == 75)
char2 = create_character_interactive(config={}, io=ScriptedIO(list(answers)),
                                     dice=FakeDice(), roster_path=roster)
check("re-saving same name replaces, not duplicates", len(load_roster(roster)) == 1)

print("== full wizard: quick-fire + quick-skills path ==")
q_answers = [
    "Quick Tester", "player2", "25",                    # name, owner, age
    "2",                                                # quick-fire characteristics
    "3", "3", "3", "3", "3", "2", "2", "1",             # STR60 CON50 SIZ50 DEX50 APP50 INT70 POW40 EDU80
    "1",                                                # occupation: Investigative Journalist
    "4",                                                # interpersonal: Persuade
    "Spot Hidden", "Stealth",                           # 2 'any' picks
    "2",                                                # quick skills
    "Library Use", "Psychology", "Spot Hidden", "History", "Persuade",
    "Credit Rating", "Art Craft Photography", "Language Own", "Stealth",
    "Occult", "Drive Auto", "First Aid", "Listen",      # 4 interests (+20 base)
    "30",                                               # CR 50 out of range -> re-set to 30
    "1",                                                # weapon: none
]
qchar = create_character_interactive(config={}, io=ScriptedIO(q_answers),
                                     dice=FakeDice(), roster_path=os.path.join(_TMP_ROSTER_DIR, "q.json"))
check("quick-fire stats assigned", qchar.STR == 60 and qchar.EDU == 80 and qchar.POW == 40)
check("quick skills ignore base (Library Use = 70 flat)", qchar.skills["Library_Use"] == 70)
check("quick skills: Psychology 60", qchar.skills["Psychology"] == 60)
check("CR re-validated into range", qchar.skills["Credit_Rating"] == 30)
check("interest +20 on base (Occult 5+20)", qchar.skills["Occult"] == 25)
check("interest +20 on base (First Aid 30+20)", qchar.skills["First_Aid"] == 50)
check("SAN = POW 40", qchar.san == 40)
check("HP = (CON 60 + SIZ 50)/10", qchar.max_hp == 11)

print("== full wizard: Soldier path (v2.4.2 field crash, end-to-end) ==")
# Mirrors the reported session: quick-fire spread, Soldier occupation,
# choose-from-list groups with multi-word skills, standard allocation.
s_answers = [
    "Soldier Tester", "player1", "23",                    # name, owner, age
    "2",                                                  # quick-fire characteristics
    "6", "6", "2", "1", "1", "1", "1", "1",               # STR50 CON50 SIZ70 DEX80 APP60 INT60 POW50 EDU40
    "10",                                                 # occupation: Soldier
    "1",                                                  # formula: DEX
    "1",                                                  # Climb
    "1", "1",                                             # First Aid, then Mechanical Repair (choose 2)
    "1",                                                  # standard allocation
    "Firearms Rifle Shotgun 50", "Fighting Brawl 50", "First Aid 45",
    "Dodge 35", "Credit Rating 30", "Survival 30",        # 240 occupation points
    "Stealth 55", "Listen 55", "Climb 10",                # 120 personal (INT 60 x2)
    "1",                                                  # weapon: none
]
schar = create_character_interactive(config={}, io=ScriptedIO(s_answers),
                                     dice=FakeDice(), roster_path=os.path.join(_TMP_ROSTER_DIR, "s.json"))
check("soldier wizard completes (no ValueError)", schar.name == "Soldier Tester")
check("occ points EDU*2+DEX*2 = 240 spent to cap",
      schar.skills["Firearms_Rifle_Shotgun"] == 75 and schar.skills["Fighting_Brawl"] == 75)
check("multi-word choice-group skill allocated", schar.skills["First_Aid"] == 75)
check("unpicked choice-group skill stays at base", schar.skills["Mechanical_Repair"] == 10)
check("Credit Rating 30 at occupation max", schar.skills["Credit_Rating"] == 30)
check("personal interests applied", schar.skills["Stealth"] == 75 and schar.skills["Listen"] == 75)
check("SAN = POW 50", schar.san == 50)
check("HP = (CON 50 + SIZ 70)/10", schar.max_hp == 12)
check("MOV 8 (DEX>SIZ, STR<SIZ)", schar.move == 8)
check("occupation recorded", schar.extra.get("occupation") == "Soldier")

print("== interest picker: blanks/garbage rejected (v2.4.3 field bug) ==")
# Field report: the quick-skills interest phase shows a bare free-text prompt
# (the wizard's only unguided one). The user hit Enter four times; each blank
# canonicalized to "" and stacked +20 into a PHANTOM empty-named skill that
# finished at 75% and saved to the roster ("Top skills:  75%, ..."). Mythos
# rejections also silently burned one of the four slots. Now: blank/'list'/
# Mythos/unknown all re-prompt without consuming a slot.
i_answers = [
    "Interest Tester", "player1", "19",                   # name, owner, age (teen)
    "2",                                                  # quick-fire characteristics
    "3", "3", "1", "1", "4", "2", "1", "1",               # STR60 CON60 SIZ80 DEX70 APP40 INT50 POW50 EDU50
    "5",                                                  # teen: deduct 5 from STR
    "10",                                                 # occupation: Soldier
    "1",                                                  # formula: DEX
    "1",                                                  # Climb
    "1", "1",                                             # First Aid, then Mechanical Repair
    "2",                                                  # quick skills
    "3", "1", "1", "1", "3", "1", "1", "1", "1",          # 70/60/60/50/50/50/40/40/40 spread
    "", "list", "Cthulhu Mythos", "Spott Hidden",         # interest junk: blank, list, Mythos, typo
    "Occult", "Drive Auto", "First Aid", "Listen",        # the 4 real interests
    "30",                                                 # CR 40 out of range -> re-set to 30
    "5",                                                  # weapon: 12-gauge shotgun
]
ichar = create_character_interactive(config={}, io=ScriptedIO(i_answers),
                                     dice=FakeDice(), roster_path=os.path.join(_TMP_ROSTER_DIR, "i.json"))
check("no phantom empty-named skill", "" not in ichar.skills)
check("Mythos not learned at creation", "Cthulhu_Mythos" not in ichar.skills)
check("typo skill not learned", "Spott_Hidden" not in ichar.skills)
check("junk answers cost no slots: 4 real interests applied",
      ichar.skills["Occult"] == 25 and ichar.skills["Drive_Auto"] == 40
      and ichar.skills["First_Aid"] == 70 and ichar.skills["Listen"] == 40)
check("CR re-validated into range", ichar.skills["Credit_Rating"] == 30)
check("teen build: HP 14, SAN 50, MOV 7",
      ichar.max_hp == 14 and ichar.san == 50 and ichar.move == 7)
check("weapon saved", ichar.weapon is not None)

print("== wizard input validation: reserved commands are never skills ==")
# Field repro: Quick-Fire + Boxer/Wrestler, 'list' typed at both 'pick any
# skill' prompts — it was stored as an occupation skill twice and saved.
from src.charcreate import (RESERVED_COMMANDS, resolve_skill_name,
                            skill_suggestion, _ask_skill, valid_skill_names)

check("reserved words never resolve to skills",
      all(resolve_skill_name(w) is None for w in RESERVED_COMMANDS))
check("exact registry names resolve", resolve_skill_name("Spot Hidden") == "Spot_Hidden")
check("aliases resolve (brawl, library)",
      resolve_skill_name("brawl") == "Fighting_Brawl"
      and resolve_skill_name("library") == "Library_Use")
check("unknown names rejected by default", resolve_skill_name("Mycology") is None)
check("a typo only suggests, never applies silently",
      skill_suggestion("Spott Hidden") == "Spot_Hidden")

io = ScriptedIO(["list", "Spot Hidden"])
s = _ask_skill(io, "  pick any skill (1 of 2) — skill name > ")
check("'list' shows skills and reprompts without consuming the pick",
      s == "Spot_Hidden" and any("Spot Hidden" in m for m in io.log))
io = ScriptedIO(["help", "back", "cancel", "quit", "exit", "save", "Intimidate"])
s = _ask_skill(io, "  pick any skill (1 of 2) — skill name > ")
check("every reserved command reprompts, none becomes a skill", s == "Intimidate")
io = ScriptedIO(["Mycology", "Fungus Studies", "Listen"])
s = _ask_skill(io, "  pick any skill (1 of 2) — skill name > ")
check("unknown skills are rejected and reprompted", s == "Listen")
io = ScriptedIO(["Spott Hidden", "n", "Listen"])
s = _ask_skill(io, "  pick > ")
check("'did you mean' + 'n' rejects the typo", s == "Listen")
io = ScriptedIO(["Spott Hidden", "y"])
s = _ask_skill(io, "  pick > ")
check("'did you mean' + 'y' accepts the suggestion", s == "Spot_Hidden")
io = ScriptedIO(["Mycology", "n", "Mycology", "y"])
s = _ask_skill(io, "  pick > ", allow_custom=True)
check("custom skills require an explicit y/n confirmation",
      s == "Mycology" and any("custom skill" in m for m in io.log))

# Full-wizard repro: Boxer/Wrestler, 'list' at BOTH pick-any prompts.
boxer = next(o for o in json.load(open("data/occupations.json", encoding="utf-8"))["occupations"]
             if o["name"] == "Boxer/Wrestler")
check("Boxer/Wrestler occupation points stay EDU*2+STR*2 = 260 (EDU 50, STR 80)",
      occupation_skill_points(boxer, {"EDU": 50, "STR": 80}) == 260)

b_answers = [
    "Boxer Tester", "player1", "25",                 # name, owner, age
    "2",                                             # Quick-Fire characteristics
    "1", "2", "3", "2", "3", "1", "1", "1",          # STR80 CON60 SIZ50 DEX60 APP40 INT70 POW50 EDU50
    "13",                                            # occupation: Boxer/Wrestler
    "list", "Track",                                 # pick 1: 'list' (reprompt), then Track
    "list", "Climb",                                 # pick 2: 'list' again, then Climb
    "2",                                             # Quick skills
    "1", "1", "1", "1", "1", "1", "1", "1", "1",     # assign the 9 quick values
    "Occult", "Drive Auto", "First Aid", "Listen",   # 4 personal interests
    "1",                                             # weapon: none
]
bchar = create_character_interactive(config={}, io=ScriptedIO(b_answers),
                                     dice=FakeDice(), roster_path=os.path.join(_TMP_ROSTER_DIR, "b.json"))
_expected_boxer = {"Dodge", "Fighting_Brawl", "Intimidate", "Jump",
                   "Psychology", "Spot_Hidden", "Track", "Climb"}
check("Boxer/Wrestler receives its authored occupation skills plus both picks",
      _expected_boxer <= set(bchar.skills))
check("two 'list' answers consumed neither pick",
      "Track" in bchar.skills and "Climb" in bchar.skills)
check("'list' never became an occupation skill",
      "list" not in bchar.skills)
check("no reserved word can reach the roster",
      not (set(bchar.skills) & RESERVED_COMMANDS))
_roster_check = load_roster(os.path.join(_TMP_ROSTER_DIR, "b.json"))
check("data/investigators.json equivalent is clean of reserved words",
      _roster_check and not (set(_roster_check[-1].skills) & RESERVED_COMMANDS))

print(f"\nALL CREATION TESTS PASSED ({PASS} checks)")
