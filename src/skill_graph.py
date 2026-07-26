"""Skill graph for the adjudication layer (v2.8.1.2).

One place that knows what the skills are called at the table. Players say
"intimidation", "brawn", "library", "a spot check" — the engine needs the
canonical 7e skill ids. Aliases map natural language to canonical skills;
`canon_skill_name` resolves a free-text phrase (longest alias wins).
"""
from typing import Optional

# Natural alias -> canonical skill. Multi-word aliases first via longest-match
# lookup in canon_skill_name; keep everything lowercase.
SKILL_ALIASES = {
    # combat
    "fighting brawl": "Fighting_Brawl",
    "brawl": "Fighting_Brawl",
    "brawling": "Fighting_Brawl",
    "fisticuffs": "Fighting_Brawl",
    "fighting": "Fighting_Brawl",
    "firearms rifle shotgun": "Firearms_Rifle_Shotgun",
    "rifle": "Firearms_Rifle_Shotgun",
    "shotgun": "Firearms_Rifle_Shotgun",
    "firearms handgun": "Firearms_Handgun",
    "handgun": "Firearms_Handgun",
    "pistol": "Firearms_Handgun",
    "firearms": "Firearms_Handgun",
    "shooting": "Firearms_Handgun",
    "strength": "STR",
    "str": "STR",
    "brawn": "STR",
    "muscle": "STR",
    "dexterity": "DEX",
    "dex": "DEX",
    "intelligence": "INT",
    "int": "INT",
    "idea": "INT",
    # coercion / social
    "intimidate": "Intimidate",
    "intimidation": "Intimidate",
    "threaten": "Intimidate",
    "menace": "Intimidate",
    "persuade": "Persuade",
    "persuasion": "Persuade",
    "charm": "Charm",
    "fast talk": "Fast_Talk",
    "deception": "Fast_Talk",
    "deceive": "Fast_Talk",
    "bluff": "Fast_Talk",
    "psychology": "Psychology",
    # perception / investigation
    "spot hidden": "Spot_Hidden",
    "spot": "Spot_Hidden",
    "search": "Spot_Hidden",
    "listen": "Listen",
    "listening": "Listen",
    "library use": "Library_Use",
    "library": "Library_Use",
    "research": "Library_Use",
    "occult": "Occult",
    "history": "History",
    "track": "Track",
    "tracking": "Track",
    # physical
    "stealth": "Stealth",
    "sneak": "Stealth",
    "hide": "Stealth",
    "climb": "Climb",
    "climbing": "Climb",
    "jump": "Jump",
    "swim": "Swim",
    "throw": "Throw",
    "dodge": "Dodge",
    "drive": "Drive_Auto",
    "driving": "Drive_Auto",
    "drive auto": "Drive_Auto",
    # technical / medical
    "locksmith": "Locksmith",
    "lockpicking": "Locksmith",
    "lock picking": "Locksmith",
    "pick locks": "Locksmith",
    "first aid": "First_Aid",
    "medicine": "Medicine",
    "sleight of hand": "Sleight_of_Hand",
    "disguise": "Disguise",
}

# Canonical skills that are characteristics, not skills, when spoken.
CHARACTERISTICS = {"STR", "DEX", "INT", "CON", "POW", "APP", "EDU", "SIZ"}


def canon_skill_name(text: str) -> Optional[str]:
    """Resolve a free-text skill mention to a canonical skill id.

    Longest alias wins ('library use' beats 'library'). Returns None when
    nothing matches — callers treat that as 'no explicit skill'."""
    t = " ".join((text or "").lower().replace("_", " ").replace("-", " ").split())
    if not t:
        return None
    best = None
    for alias, skill in SKILL_ALIASES.items():
        if t == alias or t.startswith(alias + " ") or t.endswith(" " + alias) \
                or f" {alias} " in f" {t} ":
            if best is None or len(alias) > len(best[0]):
                best = (alias, skill)
    if best:
        return best[1]
    # bare canonical id ('Spot_Hidden', 'Fighting_Brawl')
    canon = text.strip().replace(" ", "_")
    if canon and canon[0].isupper():
        return canon
    return None


def base_value(skill: str, char_stats: dict) -> int:
    """7e base for a skill the character lacks, via charcreate's table."""
    from src.charcreate import skill_base
    return skill_base(skill, {"DEX": char_stats.get("DEX", 50),
                              "EDU": char_stats.get("EDU", 50)})


def skill_target(char, skill: str) -> int:
    """The character's skill value, else the 7e base."""
    if skill in getattr(char, "skills", {}):
        return char.skills[skill]
    return base_value(skill, {"DEX": char.DEX, "EDU": char.EDU})
