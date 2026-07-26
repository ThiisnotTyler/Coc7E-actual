"""CoC 7e investigator creation wizard — enforces the actual rulebook.

Verified against the 7th-edition Investigator Handbook (ch. 3-4):
- Characteristics: STR/CON/DEX/APP/POW/Luck = 3D6 x5; SIZ/INT/EDU = (2D6+6) x5.
- Quick-Fire: assign 40, 50, 50, 50, 60, 60, 70, 80 to the eight characteristics.
- Point-Buy (Option 4): 460 points, each stat 15-90, INT and SIZ min 40.
- Age modifiers (not cumulative): see AGE_TABLE. EDU improvement check =
  1D100 > current EDU -> +1D10 EDU (max 99). MOV penalty by decade.
- Skills: occupation points per occupation formula (added to base values);
  personal interests = INT x2 to any non-Mythos skill; Credit Rating must
  land inside the occupation's range. Own Language base = EDU, Dodge = DEX/2.
- Quick Skills (Quick-Fire method): assign 70, 60, 60, 50, 50, 50, 40, 40, 40
  to the 8 occupation skills + Credit Rating, ignoring base values; then pick
  4 non-occupation skills and add +20 to each one's base value.
- Starting-skill cap: the handbook makes this an OPTIONAL Keeper rule
  ("such as 75%") — default 75 here, override in settings.json
  (game.creation_skill_cap).
"""
import copy
import json
import os
import re

from src.character import Character, Weapon
from src.dice import DiceEngine

CHAR_STATS = ["STR", "CON", "SIZ", "DEX", "APP", "INT", "POW", "EDU"]
QUICKFIRE_ARRAY = [80, 70, 60, 60, 50, 50, 50, 40]
QUICK_SKILLS_ARRAY = [70, 60, 60, 50, 50, 50, 40, 40, 40]
POINT_BUY_TOTAL = 460
POINT_BUY_MIN, POINT_BUY_MAX = 15, 90
POINT_BUY_INT_SIZ_MIN = 40

# (max_age, edu_checks, phys_deduct, phys_stats, app_deduct, mov_penalty, teen)
AGE_TABLE = [
    (19, 0, 5, ["STR", "SIZ"], 0, 0, True),
    (39, 1, 0, [], 0, 0, False),
    (49, 2, 5, ["STR", "CON", "DEX"], 5, 1, False),
    (59, 3, 10, ["STR", "CON", "DEX"], 10, 2, False),
    (69, 4, 20, ["STR", "CON", "DEX"], 15, 3, False),
    (79, 4, 40, ["STR", "CON", "DEX"], 20, 4, False),
    (200, 4, 80, ["STR", "CON", "DEX"], 25, 5, False),
]

BASE_SKILLS = {
    "Accounting": 5, "Anthropology": 1, "Appraise": 5, "Archaeology": 1,
    "Art_Craft": 5, "Art_Craft_Photography": 5, "Art_Literature": 5,
    "Charm": 15, "Climb": 20, "Credit_Rating": 0, "Cthulhu_Mythos": 0,
    "Disguise": 5, "Drive_Auto": 20, "Electrical_Repair": 10,
    "Fast_Talk": 5, "Fighting_Brawl": 25, "Firearms_Handgun": 20,
    "Firearms_Rifle_Shotgun": 25, "First_Aid": 30, "History": 5,
    "Intimidate": 15, "Jump": 20, "Law": 5, "Library_Use": 20,
    "Listen": 20, "Locksmith": 1, "Mechanical_Repair": 10, "Medicine": 1,
    "Natural_World": 10, "Navigate": 10, "Occult": 5, "Other_Language": 1,
    "Persuade": 10, "Pilot": 1, "Psychoanalysis": 1, "Psychology": 10,
    "Ride": 5, "Science": 1, "Science_Biology": 1, "Science_Chemistry": 1,
    "Sleight_of_Hand": 10, "Spot_Hidden": 25, "Stealth": 20, "Survival": 10,
    "Swim": 20, "Throw": 20, "Track": 10,
}

WEAPONS = {
    "none": None,
    "knife": Weapon(name="Knife", damage="1D4", base_range=0),
    ".32 revolver": Weapon(name=".32 Revolver", damage="1D8", base_range=15, is_short_barrel=True),
    ".38 revolver": Weapon(name=".38 Revolver", damage="1D10", base_range=15, is_short_barrel=True),
    "12-gauge shotgun": Weapon(name="12-gauge Shotgun", damage="2D6", base_range=50, rof=1, ammo=5, is_shotgun=True),
    "club": Weapon(name="Club", damage="1D8", base_range=0),
}

ROSTER_PATH = "data/investigators.json"


def canon_skill(name: str) -> str:
    """'Firearms (Rifle/Shotgun)' -> Firearms_Rifle_Shotgun; 'Library Use' -> Library_Use."""
    name = name.strip()
    if name.lower() in ("own language", "language (own)", "language_own"):
        return "Language_Own"
    name = re.sub(r"[()/]", " ", name)
    name = re.sub(r"\s+", "_", name).strip("_")
    return name


def pretty(skill: str) -> str:
    return skill.replace("_", " ")


# v2.8.1.3-wizard: creation-prompt input validation. These are COMMANDS at a
# prompt — they must never be stored as skills (field bug: 'list' became a
# Boxer/Wrestler occupation skill twice and saved into the roster).
RESERVED_COMMANDS = {"list", "help", "back", "cancel", "quit", "exit", "save"}

# Common table names -> canonical 7e skill (exact aliases only; close-typo
# guesses go through an explicit 'did you mean' confirmation in _ask_skill).
SKILL_ALIASES = {
    "spot": "Spot_Hidden", "spot hidden": "Spot_Hidden",
    "library": "Library_Use", "library use": "Library_Use", "research": "Library_Use",
    "brawl": "Fighting_Brawl", "brawling": "Fighting_Brawl",
    "fighting": "Fighting_Brawl", "fighting brawl": "Fighting_Brawl",
    "fisticuffs": "Fighting_Brawl",
    "handgun": "Firearms_Handgun", "pistol": "Firearms_Handgun",
    "revolver": "Firearms_Handgun",
    "rifle": "Firearms_Rifle_Shotgun", "shotgun": "Firearms_Rifle_Shotgun",
    "drive": "Drive_Auto", "driving": "Drive_Auto",
    "first aid": "First_Aid", "medicine": "Medicine",
    "fast talk": "Fast_Talk", "bluff": "Fast_Talk", "deception": "Fast_Talk",
    "lockpicking": "Locksmith", "lock picking": "Locksmith", "lockpick": "Locksmith",
    "sneak": "Stealth", "hide": "Stealth",
    "intimidation": "Intimidate", "threaten": "Intimidate",
    "persuasion": "Persuade", "charm": "Charm",
    "listen": "Listen", "listening": "Listen",
    "dodge": "Dodge", "climb": "Climb", "climbing": "Climb",
    "jump": "Jump", "swim": "Swim", "throw": "Throw",
    "track": "Track", "tracking": "Track",
    "psychology": "Psychology", "occult": "Occult", "history": "History",
}


def valid_skill_names() -> set:
    """The canonical skill registry (7e base table + derived specials)."""
    return set(BASE_SKILLS) | {"Dodge", "Language_Own"}


def resolve_skill_name(raw: str):
    """Free text -> canonical 7e skill, or None.

    Reserved commands are never skills. Resolution order: exact registry
    hit, alias table, then a single close match returned as a SUGGESTION
    (callers confirm it — fuzzy guesses never apply silently)."""
    name = " ".join((raw or "").strip().split())
    if not name or name.lower() in RESERVED_COMMANDS:
        return None
    s = canon_skill(name)
    if s in valid_skill_names():
        return s
    alias = SKILL_ALIASES.get(name.lower())
    if alias:
        return alias
    return None


def skill_suggestion(raw: str):
    """A 'did you mean' candidate for a rejected name, or None."""
    import difflib
    name = " ".join((raw or "").strip().lower().split())
    if not name or name in RESERVED_COMMANDS:
        return None
    pretty_map = {pretty(k).lower(): k for k in valid_skill_names()}
    close = difflib.get_close_matches(name, list(pretty_map), n=1, cutoff=0.8)
    return pretty_map[close[0]] if close else None


def skill_base(skill: str, stats: dict) -> int:
    if skill == "Dodge":
        return stats["DEX"] // 2
    if skill == "Language_Own":
        return stats["EDU"]
    return BASE_SKILLS.get(skill, 1)


# ------------------------------------------------------------------ rules
def roll_characteristics(dice: DiceEngine) -> dict:
    stats = {}
    for s in ("STR", "CON", "DEX", "APP", "POW"):
        stats[s] = dice.d(6, 3) * 5
    for s in ("SIZ", "INT", "EDU"):
        stats[s] = (dice.d(6, 2) + 6) * 5
    return stats


def roll_luck(dice: DiceEngine, best_of_two: bool = False) -> int:
    a = dice.d(6, 3) * 5
    if best_of_two:
        b = dice.d(6, 3) * 5
        return max(a, b)
    return a


def validate_point_buy(stats: dict) -> list:
    errs = []
    if sum(stats.values()) != POINT_BUY_TOTAL:
        errs.append(f"point-buy must total {POINT_BUY_TOTAL} (got {sum(stats.values())})")
    for s, v in stats.items():
        if not (POINT_BUY_MIN <= v <= POINT_BUY_MAX):
            errs.append(f"{s} must be {POINT_BUY_MIN}-{POINT_BUY_MAX} (got {v})")
        if s in ("INT", "SIZ") and v < POINT_BUY_INT_SIZ_MIN:
            errs.append(f"{s} minimum is {POINT_BUY_INT_SIZ_MIN} (got {v})")
    return errs


def age_bracket(age: int):
    for row in AGE_TABLE:
        if age <= row[0]:
            return row
    return AGE_TABLE[-1]


def edu_improvement_check(stats: dict, dice: DiceEngine) -> tuple:
    roll = dice.d100()
    gained = 0
    if roll > stats["EDU"]:
        gained = dice.d(10)
        stats["EDU"] = min(99, stats["EDU"] + gained)
    return roll, gained


def apply_age(stats: dict, age: int, deduction_choices: dict, dice: DiceEngine) -> dict:
    """Apply the (non-cumulative) age modifiers. deduction_choices maps
    stat -> points to deduct, covering the physical and APP deductions.
    Teen EDU -5 is automatic; EDU checks are rolled here. Returns a report."""
    max_age, edu_checks, phys_deduct, phys_stats, app_deduct, mov_pen, teen = age_bracket(age)
    report = {"bracket": max_age, "edu_checks": [], "notes": []}

    if teen:
        stats["EDU"] = max(15, stats["EDU"] - 5)
        report["notes"].append("Teen: -5 EDU (and Luck will be best of two rolls)")

    applied_phys = {s: v for s, v in deduction_choices.items() if s in phys_stats}
    if sum(applied_phys.values()) != phys_deduct and phys_deduct > 0:
        raise ValueError(
            f"Age {age}: must deduct exactly {phys_deduct} points among "
            f"{'/'.join(phys_stats)} (got {sum(applied_phys.values())})")
    for s, v in applied_phys.items():
        stats[s] = max(15, stats[s] - v)

    if app_deduct:
        given = deduction_choices.get("APP", 0)
        if given != app_deduct:
            raise ValueError(f"Age {age}: APP must be reduced by exactly {app_deduct}")
        stats["APP"] = max(15, stats["APP"] - app_deduct)

    for _ in range(edu_checks):
        roll, gained = edu_improvement_check(stats, dice)
        report["edu_checks"].append({"roll": roll, "gained": gained})
    report["mov_penalty"] = mov_pen
    return report


def parse_formula(formula: str) -> list:
    """'EDU*2+DEX*2|STR*2' -> [[('EDU', 2)], [('DEX', 2), ('STR', 2)]]
    Each top-level term is a list of (stat, multiplier) alternatives."""
    terms = []
    for part in formula.split("+"):
        options = []
        for alt in part.split("|"):
            stat, mult = alt.rsplit("*", 1)
            options.append((stat, int(mult)))
        terms.append(options)
    return terms


def occupation_skill_points(occupation: dict, stats: dict, choices: dict = None) -> int:
    """choices: {'DEX|STR': 'DEX'} for piped formula terms."""
    total = 0
    for term in parse_formula(occupation["skill_points"]):
        opts = [s for s, _ in term]
        key = "|".join(opts)
        pick = (choices or {}).get(key, opts[0])
        if pick not in opts:
            raise ValueError(f"formula choice {pick!r} not one of {opts}")
        mult = next(m for s, m in term if s == pick)
        total += stats[pick] * mult
    return total


def resolve_occupation_skills(occupation: dict, chooser) -> list:
    """Expand fixed skills + choice groups into a concrete skill list.
    chooser(prompt_text, options) -> chosen option."""
    skills = []
    for item in occupation["skills"]:
        if isinstance(item, str):
            skills.append(canon_skill(item))
        else:
            n, src = item["choose"], item["from"]
            if src == "any":
                for i in range(n):
                    skills.append(canon_skill(chooser(
                        f"pick any skill ({i + 1} of {n})", None)))
            else:
                pool = list(src)
                for i in range(n):
                    pick = chooser(f"choose {i + 1} of {n}", pool)
                    cpick = canon_skill(pick)
                    skills.append(cpick)
                    # Consume the pool entry whether the chooser returned the
                    # option raw ("First Aid") or canonicalized ("First_Aid").
                    # (v2.4.2 field bug: a pre-canonicalized pick crashed here
                    # with ValueError: list.remove(x): x not in list)
                    pool[:] = [o for o in pool if canon_skill(o) != cpick]
    seen = set()
    out = []
    for s in skills:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def validate_credit_rating(value: int, cr_range: list) -> list:
    lo, hi = cr_range
    if value < lo:
        return [f"Credit Rating {value} is below the occupation minimum ({lo})"]
    if value > hi:
        return [f"Credit Rating {value} exceeds the occupation maximum ({hi})"]
    return []


# ------------------------------------------------------------------ roster
def save_to_roster(char: Character, occupation: str, age: int, path: str = ROSTER_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = {"investigators": []}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    d = char.to_dict()
    d["extra"] = {**d.get("extra", {}), "occupation": occupation, "age": age}
    data["investigators"] = [c for c in data["investigators"] if c["id"] != char.id]
    data["investigators"].append(d)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return path


def load_roster(path: str = ROSTER_PATH) -> list:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return [Character.from_dict(d) for d in data.get("investigators", [])]


# ------------------------------------------------------------------ IO
class ConsoleIO:
    def ask(self, prompt: str = "") -> str:
        return input(prompt)

    def say(self, msg: str = ""):
        print(msg)


class ScriptedIO:
    """Deterministic IO for tests/replays."""
    def __init__(self, answers: list):
        self.answers = list(answers)
        self.log = []

    def ask(self, prompt: str = "") -> str:
        self.log.append(prompt)
        if not self.answers:
            raise AssertionError(f"ScriptedIO ran out of answers at prompt: {prompt!r}")
        return str(self.answers.pop(0))

    def say(self, msg: str = ""):
        self.log.append(str(msg))


def build_character(name: str, age: int, stats: dict, luck: int, occupation: dict,
                    occ_skills: list, skill_values: dict, mov_penalty: int,
                    weapon_key: str = "none", owner: str = "player",
                    location: str = "unknown") -> Character:
    char_id = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    # Roster guard: reserved commands and empty names can never be skills,
    # no matter which prompt path produced them (v2.8.1.3-wizard).
    skill_values = {k: v for k, v in skill_values.items()
                    if k and k.strip() and k.lower() not in RESERVED_COMMANDS}
    char = Character(
        id=char_id, name=name, char_type="player", owner=owner,
        STR=stats["STR"], CON=stats["CON"], SIZ=stats["SIZ"], DEX=stats["DEX"],
        APP=stats["APP"], INT=stats["INT"], POW=stats["POW"], EDU=stats["EDU"],
        # copy.copy: every investigator gets their OWN weapon instance —
        # WEAPONS holds catalog templates; ammo must never be shared (v2.7.3)
        luck=luck, skills=dict(skill_values),
        weapon=copy.copy(WEAPONS.get(weapon_key)),
        location=location,
    )
    if mov_penalty:
        char.move = max(1, char.move - mov_penalty)
    char.extra.update({"occupation": occupation["name"], "age": age,
                       "credit_rating": skill_values.get("Credit_Rating", 0)})
    return char


# ------------------------------------------------------------------ wizard
def _ask_int(io, prompt, lo=None, hi=None):
    while True:
        raw = io.ask(prompt).strip()
        if raw.lstrip("-").isdigit():
            v = int(raw)
            if (lo is None or v >= lo) and (hi is None or v <= hi):
                return v
        io.say(f"  enter a number{f' {lo}-{hi}' if lo is not None else ''}.")


def _ask_choice(io, prompt, options):
    io.say(prompt)
    for i, o in enumerate(options, 1):
        io.say(f"  {i}. {o}")
    while True:
        raw = io.ask("> ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1]
        for o in options:
            if o.lower() == raw.lower():
                return o
        io.say("  pick a number from the list.")


def _ask_skill(io, prompt, allow_custom=False):
    """A validated free-text skill prompt (the 'pick any skill' path).

    - 'list' shows the valid skills and re-asks WITHOUT consuming the pick;
    - reserved words ('help', 'back', 'cancel', 'quit', 'exit', 'save') are
      rejected as commands, never stored;
    - unknown names are rejected by default, with a 'did you mean' offer when
      a close 7e skill exists;
    - custom (non-7e) skills only ever enter via an explicit y/n confirm."""
    while True:
        raw = io.ask(prompt).strip()
        if not raw:
            io.say("  blank isn't a skill — type a name, or 'list'.")
            continue
        low = raw.lower()
        if low == "list":
            io.say("  " + ", ".join(pretty(s) for s in sorted(BASE_SKILLS)))
            continue
        if low in RESERVED_COMMANDS:
            io.say(f"  '{raw}' is a command, not a skill — type a skill name, or 'list'.")
            continue
        s = resolve_skill_name(raw)
        if s is not None:
            return s
        guess = skill_suggestion(raw)
        if guess is not None:
            ans = io.ask(f"  did you mean {pretty(guess)}? y/n > ").strip().lower()
            if ans in ("y", "yes"):
                return guess
            continue
        if allow_custom:
            ans = io.ask(f"  Add '{raw}' as a custom skill? y/n > ").strip().lower()
            if ans in ("y", "yes"):
                return canon_skill(raw)
            io.say("  not added.")
            continue
        io.say(f"  '{raw}' isn't a 7e skill — check spelling, or type 'list'.")


def _show_skills(io, values, budget, allowed=None):
    io.say(f"\n  {'skill':<28}{'value':>6}")
    io.say("  " + "-" * 36)
    for s in sorted(values):
        if allowed is None or s in allowed:
            io.say(f"  {pretty(s):<28}{values[s]:>5}%")
    io.say(f"\n  points remaining: {budget}")


def _allocate_loop(io, values, budget, allowed, cap, stats, label):
    """Standard point allocation. allowed=None means any non-Mythos skill."""
    while budget > 0:
        _show_skills(io, values, budget, allowed)
        raw = io.ask(f"[{label}] <skill> <points> > ").strip()
        if not raw:
            continue
        m = re.match(r"^(.*?)\s+(\d+)$", raw)
        if not m:
            io.say("  format: skill name, space, points. e.g. 'Library Use 40'")
            continue
        raw_skill = m.group(1).strip()
        if raw_skill.lower() in RESERVED_COMMANDS:
            io.say(f"  '{raw_skill}' is a command, not a skill — type a skill name.")
            continue
        skill = resolve_skill_name(raw_skill)
        if skill is None:
            io.say(f"  '{raw_skill}' isn't a 7e skill — check spelling, or type 'list'.")
            continue
        points = int(m.group(2))
        if skill == "Cthulhu_Mythos":
            io.say("  no points into Cthulhu Mythos at creation (rulebook).")
            continue
        if allowed is not None and skill not in allowed:
            io.say("  occupation points go to occupation skills (and Credit Rating) only.")
            continue
        if points > budget:
            io.say(f"  only {budget} points left.")
            continue
        current = values.get(skill, skill_base(skill, stats))
        if current + points > cap:
            io.say(f"  starting-skill cap is {cap}% (optional rule; change in settings.json).")
            continue
        values[skill] = current + points
        budget -= points
    return values


def _deduction_plan(io, phys_deduct, phys_stats, app_deduct, stats):
    choices = {}
    remaining = phys_deduct
    if phys_deduct:
        io.say(f"\nAge deducts {phys_deduct} points among {'/'.join(phys_stats)}.")
        for s in phys_stats:
            if remaining <= 0:
                break
            v = _ask_int(io, f"  deduct from {s} (current {stats[s]}, {remaining} left to assign) > ", 0, remaining)
            if v:
                choices[s] = v
                remaining -= v
        if remaining:
            io.say(f"  still {remaining} unassigned — taking the rest from {phys_stats[0]}.")
            choices[phys_stats[0]] = choices.get(phys_stats[0], 0) + remaining
    if app_deduct:
        io.say(f"Age also reduces APP by {app_deduct} (automatic).")
        choices["APP"] = app_deduct
    return choices


def create_character_interactive(config=None, io=None, dice=None, roster_path=ROSTER_PATH):
    io = io or ConsoleIO()
    dice = dice or DiceEngine()
    cfg = config or {}
    cap = int(cfg.get("game", {}).get("creation_skill_cap", 75))
    with open("data/occupations.json", encoding="utf-8") as f:
        occupations = json.load(f)["occupations"]

    io.say("=" * 60)
    io.say(" INVESTIGATOR CREATION — Call of Cthulhu 7th Edition")
    io.say("=" * 60)
    name = io.ask("Investigator's full name: ").strip() or "Unnamed Investigator"
    owner = io.ask("Player handle (e.g. player1): ").strip() or "player"
    age = _ask_int(io, "Age (15-90): ", 15, 90)

    # ---- characteristics ----
    method = _ask_choice(io, "\nCharacteristic generation method:",
                         ["Roll dice (3D6x5 / 2D6+6x5)",
                          "Quick-Fire array (40,50,50,50,60,60,70,80)",
                          "Point-Buy (460 points, 15-90, INT/SIZ min 40)"])
    if method.startswith("Roll"):
        stats = roll_characteristics(dice)
        io.say("Rolled: " + ", ".join(f"{k} {v}" for k, v in stats.items()))
    elif method.startswith("Quick-Fire"):
        stats = {}
        pool = sorted(QUICKFIRE_ARRAY, reverse=True)
        for stat in CHAR_STATS:
            io.say(f"Remaining values: {pool}")
            pick = _ask_choice(io, f"Assign a value to {stat}:", [str(v) for v in pool])
            stats[stat] = int(pick)
            pool.remove(int(pick))
    else:
        stats = {}
        io.say(f"Distribute {POINT_BUY_TOTAL} points (each stat 15-90, INT/SIZ min 40).")
        for stat in CHAR_STATS:
            stats[stat] = _ask_int(io, f"  {stat} > ", POINT_BUY_MIN, POINT_BUY_MAX)
        errs = validate_point_buy(stats)
        while errs:
            for e in errs:
                io.say(f"  ! {e}")
            fix = io.ask("  re-enter as 'STAT value' (e.g. 'INT 60') > ").strip()
            m = re.match(r"^([A-Za-z]+)\s+(\d+)$", fix)
            if m and m.group(1).upper() in CHAR_STATS:
                stats[m.group(1).upper()] = int(m.group(2))
            errs = validate_point_buy(stats)

    # ---- luck ----
    teen = age <= 19
    luck = roll_luck(dice, best_of_two=teen)
    io.say(f"Luck: {luck}" + (" (best of two rolls — teen)" if teen else ""))

    # ---- age modifiers ----
    max_age, edu_checks, phys_deduct, phys_stats, app_deduct, mov_pen, _ = age_bracket(age)
    choices = _deduction_plan(io, phys_deduct, phys_stats, app_deduct, stats)
    report = apply_age(stats, age, choices, dice)
    for chk in report["edu_checks"]:
        io.say(f"EDU improvement check: rolled {chk['roll']} "
               + (f"-> +{chk['gained']} EDU" if chk["gained"] else "-> no gain"))
    if mov_pen:
        io.say(f"MOV will be reduced by {mov_pen} (age).")
    io.say("Final characteristics: " + ", ".join(f"{k} {v}" for k, v in stats.items()))

    # ---- occupation ----
    occ = _ask_choice(io, "\nChoose occupation:", [o["name"] for o in occupations])
    occupation = next(o for o in occupations if o["name"] == occ)
    formula_choices = {}
    for term in parse_formula(occupation["skill_points"]):
        opts = [s for s, _ in term]
        if len(opts) > 1:
            pick = _ask_choice(io, f"Skill-point formula uses {' or '.join(opts)} — pick one:", opts)
            formula_choices["|".join(opts)] = pick
    occ_points = occupation_skill_points(occupation, stats, formula_choices)
    io.say(f"Occupation skill points: {occ_points}")

    # Chooser returns the raw option/free text; resolve_occupation_skills owns
    # canonicalization (double-canon here caused the v2.4.2 pool.remove crash).
    # 'pick any skill' goes through _ask_skill: reserved words and unknown
    # names can no longer become occupation skills (the 'list' field bug).
    allow_custom = bool(cfg.get("game", {}).get("creation_custom_skills", False))
    occ_skills = resolve_occupation_skills(
        occupation,
        lambda prompt, options: (_ask_skill(io, f"  {prompt} — skill name > ",
                                            allow_custom=allow_custom)
                                 if options is None else
                                 _ask_choice(io, f"  {prompt}:", options)))
    io.say("Occupation skills: " + ", ".join(pretty(s) for s in occ_skills))

    # ---- skills ----
    skill_method = _ask_choice(io, "\nSkill allocation method:",
                               ["Standard (spend occupation points + INT x2 interests)",
                                "Quick skills (70/60/60/50/50/50/40/40/40, then 4 interests +20)"])
    values = {s: skill_base(s, stats) for s in occ_skills}
    values.setdefault("Credit_Rating", 0)

    if skill_method.startswith("Standard"):
        allowed = set(occ_skills) | {"Credit_Rating"}
        io.say("\nAllocate OCCUPATION points (occupation skills + Credit Rating only).")
        values = _allocate_loop(io, values, occ_points, allowed, cap, stats, "occupation")
        personal = stats["INT"] * 2
        io.say(f"\nAllocate PERSONAL INTEREST points: {personal} (INT x2, any non-Mythos skill).")
        values = _allocate_loop(io, values, personal, None, cap, stats, "interest")
    else:
        pool = sorted(QUICK_SKILLS_ARRAY, reverse=True)
        targets = list(occ_skills) + ["Credit_Rating"]
        while len(targets) > 9:
            drop = _ask_choice(io, "Quick skills supports 8 occupation skills + Credit Rating; drop one:",
                               [pretty(t) for t in targets[:-1]])
            targets.remove(next(t for t in targets if pretty(t) == drop))
        quick_values = {}
        for v in pool:
            pick = _ask_choice(io, f"Assign {v}% to:", [pretty(t) for t in targets])
            target = next(t for t in targets if pretty(t) == pick)
            quick_values[target] = v
            targets.remove(target)
        values.update(quick_values)
        # v2.4.3 field bug: this was the wizard's only unguided free-text
        # prompt. Four Enters stacked +20 each into a phantom empty-named
        # skill ("" -> 75% in the roster), and a Mythos answer silently
        # burned a slot. Now every invalid answer re-prompts, no slot lost.
        io.say("\nPick 4 personal-interest skills (+20% on base value each).")
        io.say("  any non-Mythos skill, e.g. Spot Hidden, Listen, Occult, Drive Auto.")
        io.say("  type 'list' to see every skill.")
        picked = 0
        while picked < 4:
            raw = io.ask(f"  interest skill {picked + 1} of 4 > ").strip()
            if not raw:
                io.say("  blank isn't a skill — type a name, or 'list'.")
                continue
            if raw.lower() == "list":
                io.say("  " + ", ".join(pretty(s) for s in sorted(BASE_SKILLS)))
                continue
            s = canon_skill(raw)
            if s == "Cthulhu_Mythos":
                io.say("  no Cthulhu Mythos at creation (rulebook).")
                continue
            if s not in BASE_SKILLS and s not in ("Dodge", "Language_Own"):
                io.say(f"  '{raw}' isn't a 7e skill — check spelling, or type 'list'.")
                continue
            base = values.get(s, skill_base(s, stats))
            values[s] = min(cap, base + 20)
            io.say(f"  -> {pretty(s)} now {values[s]}%")
            picked += 1

    # ---- credit rating enforcement ----
    cr_range = occupation["credit_rating"]
    errs = validate_credit_rating(values.get("Credit_Rating", 0), cr_range)
    while errs:
        for e in errs:
            io.say(f"  ! {e}")
        values["Credit_Rating"] = _ask_int(
            io, f"  set Credit Rating within {cr_range[0]}-{cr_range[1]} > ", cr_range[0], cr_range[1])
        errs = validate_credit_rating(values["Credit_Rating"], cr_range)

    # ---- weapon ----
    wkey = _ask_choice(io, "\nStarting weapon:", list(WEAPONS.keys()))

    char = build_character(name, age, stats, luck, occupation, occ_skills,
                           values, mov_pen, weapon_key=wkey, owner=owner)
    path = save_to_roster(char, occupation["name"], age, path=roster_path)

    io.say("\n" + "=" * 60)
    io.say(f" {char.name} — {occupation['name']}, age {age}")
    io.say(f" HP {char.hp}/{char.max_hp} | SAN {char.san} | MP {char.mp} | Luck {char.luck} | MOV {char.move}")
    io.say(f" Build {char.build} | Damage bonus {char.damage_bonus}")
    top = sorted(values.items(), key=lambda kv: -kv[1])[:6]
    io.say(" Top skills: " + ", ".join(f"{pretty(s)} {v}%" for s, v in top))
    io.say(f" Saved to {path}")
    io.say("=" * 60)
    return char
