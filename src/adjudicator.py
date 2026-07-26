"""Deterministic local adjudication (v2.8.1.2).

raw declaration
  -> normalization
  -> compound-action splitting
  -> intent frames
  -> scene-context target/tool binding
  -> skill candidate scoring
  -> roll / no_roll / local / clarify / impossible / passthrough decision

Data-driven: verb phrases, targets, and weighted skills live in
data/action_prototypes.json — not in an ever-growing regex list. Anything
the pipeline cannot confidently frame is PASSED THROUGH to the normal
Keeper path untouched; the adjudicator only eats what it understands.
"""
import json
import os
import re
from typing import Dict, List, Optional

from src.action_intent import IntentFrame, normalize, strip_article
from src import skill_graph

# Idioms that must never become rolls ('hit the road', 'strike up a chat').
IDIOM_PHRASES = (
    "hit the road", "hit the hay", "hit the sack", "hit the books",
    "hit it off", "knock on wood", "hit the nail", "strike up",
    "strike a match", "strike a chord", "break the news", "break a leg",
    "kick the bucket", "break the ice",
)

_EXPLICIT_RE = re.compile(
    r"^(?:i(?:'ll| will)?\s+)?(?:roll|try|attempt)\s+"
    r"([a-z_ ]+?)(?:\s+(?:to|for|on|at|around|against|with)\s+(.*))?$")

# v2.8.1.3: conditional threats are coercion, never committed fire.
# 'Step away with your hands up or I will shoot you' rolls Intimidate — the
# shotgun stays unfired until noncompliance becomes a declared attack.
CONDITIONAL_RE = re.compile(
    r"\b(or\s+i'?ll|or\s+i\s+will|or\s+else|if\s+you\s+don'?t|"
    r"if\s+you\s+do\s+not|unless\s+you|or\s+i\s+swear|"
    r"do\s+as\s+i\s+say\s+or|do\s+what\s+i\s+say\s+or)\b")
THREAT_WORD_RE = re.compile(
    r"\b(shoot|kill|blast|plug|hurt|waste|murder|end\s+you|"
    r"blow\s+\w+\s+(?:head|brains)|put\s+a\s+(?:bullet|shell))\b")
# Violence only counts when the PLAYER commits outside quotation marks.
COMMIT_VIOLENCE_RE = re.compile(
    r"\bi(?:\s+|')(?:shoot|fire|blast|pull\s+the\s+trigger|kill|waste)\b")
_QUOTED_RE = re.compile(r"[\"'][^\"']*[\"']")

# v2.8.1.7 P0-4: aiming a firearm AS A THREAT is coercion, not an attack —
# 'train the shotgun on Hobbs' head as a threat' rolls Intimidate, spends no
# shell, and fires nothing. Requires both the aim and the threat marker so
# a bare 'aim at Hobbs' stays a ranged attack.
ARMED_AIM_RE = re.compile(
    r"\b(train|point|aim|level|stick|shove|press|hold)\b.{0,40}"
    r"\b(shotgun|revolver|pistol|rifle|gun|barrel)\b")
THREAT_MARK_RE = re.compile(
    r"\b(threat|threaten|warn|warning|back\s+off|or\s+else)\b")

# 'throw a flying knee' is a strike, never the Throw skill — the word
# 'throw' does not own body weapons.
_THROW_STRIKE_RE = re.compile(
    r"\b(?:throw|throws|thrown|hurl|toss)\s+(?:a\s+|my\s+)?(?:flying\s+)?"
    r"(?:knee|kick|punch|elbow|headbutt)\b")

_QUESTION_START_RE = re.compile(
    r"^(?:where|what|who|whom|whose|why|how|when|which)\b")

_COMPOUND_HARD = re.compile(r"\s*(?:,?\s+and\s+then\s+|,?\s+then\s+|;\s*)\s*")
# v2.8.1.7 P0-4: sentence-final ?/! ends a clause ('where are the papers
# Hobbs? we gotta burn them...' — the question is its own segment).
_SENTENCE_END_RE = re.compile(r"(?<=[?!])\s+")
# Stage directions in parentheses are action clauses, not commentary to
# discard: '"Stop or I'll kill you" (grabs Hobbs and judo slams him)'.
_PAREN_RE = re.compile(r"\(([^)]+)\)")

# Words that start an action clause: '..., and demand answers' splits,
# 'the lock and key' does not.
_SPLIT_VERBS = {
    "take", "grab", "collect", "pocket", "snatch", "read", "skim",
    "shoot", "fire", "blast", "plug", "aim", "hit", "strike", "punch",
    "swing", "stab", "attack", "smash", "slam", "tackle", "kick", "break",
    "force", "shoulder", "ram", "barge", "burst", "pry", "jimmy", "knock",
    "intimidate", "threaten", "menace", "demand", "order", "command",
    "warn", "compel", "bully", "stick", "persuade", "convince", "appeal",
    "reason", "plead", "beg", "negotiate", "charm", "calm", "lie",
    "bluff", "deceive", "con", "trick", "sneak", "hide", "creep",
    "grab", "drag", "shove", "march", "restrain", "haul", "seize",
    "search", "look", "spot", "rifle", "rummage", "comb", "sweep", "poke",
    "examine", "inspect", "study", "listen", "interpret", "decipher",
    "decode", "translate", "bandage", "patch", "treat", "dodge", "duck",
    "evade", "dive", "flee", "run", "climb", "scale", "jump", "leap",
    "vault", "swim", "throw", "hurl", "toss", "lockpick", "pick", "use",
    "light", "switch", "drink", "eat", "go", "enter", "walk", "head",
    "step", "move", "cross", "return", "leave", "sprint", "unlock",
    "grapple",
}

# Confidence thresholds (documented in HANDOFF): act >= 0.65,
# clarify 0.45-0.65 (only with real options), passthrough below.
ACT_THRESHOLD = 0.65
CLARIFY_THRESHOLD = 0.45

_PROTO_CACHE: Optional[list] = None


class Adjudicator:
    def __init__(self, prototypes: list):
        self.prototypes = prototypes
        # (phrase, prototype) sorted longest-phrase-first for matching.
        self._verb_index = []
        for proto in prototypes:
            for verb in proto.get("verbs", []):
                self._verb_index.append((verb, proto))
        self._verb_index.sort(key=lambda vp: -len(vp[0]))

    @classmethod
    def load(cls, path: str = "data/action_prototypes.json") -> "Adjudicator":
        global _PROTO_CACHE
        if _PROTO_CACHE is None:
            with open(path, encoding="utf-8") as f:
                _PROTO_CACHE = json.load(f).get("prototypes", [])
        return cls(_PROTO_CACHE)

    # ------------------------------------------------------------- pipeline
    def adjudicate(self, keeper, char, text: str) -> List[IntentFrame]:
        segments = self.split_compound(text)
        frames = []
        for i, seg in enumerate(segments):
            frames.append(self._frame_for(keeper, char, seg, i, frames))
        return frames

    def split_compound(self, text: str) -> List[str]:
        """'blast the lock off, then kick it in' -> two segments.
        'the lock and key' stays one — a clause only splits before a verb.
        v2.8.1.7: parenthetical stage directions become their own clause,
        and sentence-final ?/! ends a segment."""
        text = (text or "").strip()
        if not text:
            return []
        text = _PAREN_RE.sub(r" then \1", text)
        hard = []
        for piece in _SENTENCE_END_RE.split(text):
            hard.extend(_COMPOUND_HARD.split(piece))
        segments = []
        for piece in hard:
            parts = re.split(r",\s+|\s+and\s+", piece)
            buf = parts[0]
            for part in parts[1:]:
                p = re.sub(r"^and\s+", "", part.strip())
                first = p.split(" ", 1)[0].lower() if p else ""
                if first in _SPLIT_VERBS:
                    if buf.strip():
                        segments.append(buf.strip())
                    buf = p
                else:
                    buf = f"{buf} and {part}" if " and " in piece else f"{buf}, {part}"
            if buf.strip():
                segments.append(buf.strip())
        return [s for s in segments if s]

    # ------------------------------------------------------------- framing
    def _frame_for(self, keeper, char, seg: str, index: int,
                   prior: List[IntentFrame]) -> IntentFrame:
        norm = normalize(seg)
        frame = IntentFrame(raw=seg)

        # 1. idioms never become rolls
        if any(p in norm for p in IDIOM_PHRASES):
            frame.decision = "passthrough"
            frame.reason = "idiom or flavor, not a mechanical action"
            frame.confidence = 0.9
            return frame

        # 1b. v2.8.1.3: conditional/implied threats are COERCION, not combat.
        # Violence counts only when committed outside quotation marks —
        # 'hands up or I will shoot you' is Intimidate; 'I shoot him' is not.
        # 1a. v2.8.1.7 P0-4: a question is table talk, not a mechanic. Bind
        # who was asked so the Keeper knows who was addressed; nothing rolls.
        if seg.rstrip().endswith("?") or _QUESTION_START_RE.match(norm):
            frame.decision = "passthrough"
            frame.reason = "question - answered in play, not a mechanic"
            frame.confidence = 0.8
            self._bind_target(keeper, char, norm, frame,
                              {"targets": ["npc"]}, prior)
            return frame

        unquoted = _QUOTED_RE.sub(" ", norm)
        committed = bool(COMMIT_VIOLENCE_RE.search(unquoted))
        # v2.8.1.7 P0-4: aiming a firearm AS A THREAT joins this branch -
        # 'train the shotgun on Hobbs' head as a threat' rolls Intimidate,
        # spends no shell, and fires nothing.
        conditional_threat = bool((CONDITIONAL_RE.search(norm)
                                   or _QUOTED_RE.search(seg))
                                  and THREAT_WORD_RE.search(norm))
        armed_threat = bool(ARMED_AIM_RE.search(norm)
                            and THREAT_MARK_RE.search(norm))
        if not committed and (conditional_threat or armed_threat):
            frame.action_type = "coercion"
            frame.verb = "threaten"
            frame.manner.append("armed_threat")
            frame.skill = "Intimidate"
            frame.confidence = 0.85
            self._bind_target(keeper, char, norm, frame,
                              {"targets": ["npc"]}, prior)
            if not frame.target_id:
                npc = self._nearest_npc(keeper, char)
                if npc is not None:
                    frame.target_id, frame.target_type = npc.id, "npc"
            self._bind_instrument(keeper, char, norm, frame)
            frame.decision = "roll" if frame.target_id else "passthrough"
            frame.needs_roll = frame.decision == "roll"
            frame.reason = ("conditional armed threat — coercive; "
                            "no shot is fired unless the player commits")
            return frame

        # 2. explicit roll override: 'roll intimidation to compell'
        explicit_skill = None
        rest = norm
        m = _EXPLICIT_RE.match(norm)
        if m:
            cand = skill_graph.canon_skill_name(m.group(1))
            if cand:
                explicit_skill = cand
                rest = (m.group(2) or "").strip()
                frame.explicit_skill = cand

        # 3. prototype match: all verb matches, longest phrase first; target
        # words in the text disambiguate ('kick Hobbs' vs 'kick the door').
        matches = self._match_prototypes(rest)
        proto, verb, alts = self._disambiguate(keeper, char, rest, matches, prior)
        if proto is not None:
            frame.action_type = proto["action_type"]
            frame.verb = verb
            frame.goal = proto.get("examples", [""])[0]

        # 4. bind target and instrument against the live scene
        self._bind_target(keeper, char, rest, frame, proto, prior)
        self._bind_instrument(keeper, char, rest, frame)
        if "nonlethal" in (proto or {}).get("manner", []) or re.search(
                r"\b(knock\s+\w+\s+out|knock\s+out|nonlethal|just\s+knock)\b", rest):
            frame.manner.append("nonlethal")
        # v2.8.1.3 hard rules: people are not items, items are not people.
        if frame.target_type == "npc" and frame.action_type in (
                "take_item", "force_object"):
            frame.action_type = "npc_handling"
            proto = next((p for p in self.prototypes
                          if p["action_type"] == "npc_handling"), proto)
        elif frame.action_type == "npc_handling" and frame.target_type in (
                "item", "document"):
            frame.action_type = "take_item"
            proto = next((p for p in self.prototypes
                          if p["action_type"] == "take_item"), proto)

        # v2.8.1.3: forced-movement destination and door clause (after the
        # hard rules, so rewritten npc_handling frames bind too).
        if frame.action_type in ("npc_handling", "movement"):
            mdest = re.search(
                r"\b(?:to|into|toward|towards|through)\s+(?:the\s+)?"
                r"([a-z][a-z ]+?)(?:\s+and\b|\s+while\b|\s+then\b|$)", rest)
            if mdest:
                want = mdest.group(1).strip()
                for lid, loc in keeper.locations.items():
                    if want in (loc.name.lower(), lid.replace("_", " ")):
                        frame.dest_id = lid
                        break
        if re.search(r"\b(shut|close|slam)\s+(?:the\s+)?door\b", rest):
            frame.manner.append("shut_door")

        # 5. choose the skill
        frame.skill = self._choose_skill(char, frame, proto, explicit_skill)

        # 6. score confidence
        frame.confidence = self._score(frame, proto, explicit_skill)

        # 7. decide
        self._decide(keeper, char, frame, proto, explicit_skill, alts)
        # v2.8.1.3: later frames are conditional on earlier outcomes by
        # default ('force him out, then grab a lantern and throw it').
        if index > 0:
            frame.conditional_on = index - 1
        return frame

    def _match_prototypes(self, norm: str):
        out = []
        for phrase, proto in self._verb_index:
            # v2.8.1.7 P0-4: third-person stage directions match too —
            # '(grabs Hobbs and judo slams him)' is still a declaration.
            if not re.search(rf"\b{re.escape(phrase)}s?\b", norm):
                continue
            # 'open'/'unlock' only count when they lead the clause —
            # 'pry the door open' is a pry, 'work the latch open' is not
            # an open command at all.
            if proto.get("action_type") == "open_object" \
                    and not norm.startswith(phrase):
                continue
            out.append((phrase, proto))
        # split-verb force: 'kick the study door down' — the object sits
        # between the verb and its particle.
        if not any(p.get("action_type") == "force_object" for _, p in out):
            if re.search(r"\b(kick|break|force|shoulder|ram|smash|knock)\b"
                         r".{1,40}\b(down|in|open)\b", norm) and re.search(
                    r"\b(door|gate|hatch|window|padlock|barricade)\b", norm):
                for proto in self.prototypes:
                    if proto.get("action_type") == "force_object":
                        out.insert(0, ("force", proto))
                        break
        # split nonlethal: 'knock Hobbs out', 'knock him unconscious'
        if not any(p.get("action_type") == "nonlethal_attack" for _, p in out):
            if re.search(r"\bknock\s+\w+\s+(?:out|unconscious)\b", norm):
                for proto in self.prototypes:
                    if proto.get("action_type") == "nonlethal_attack":
                        out.insert(0, ("knock out", proto))
                        break
        # v2.8.1.7 P0-4: 'throw a flying knee / a punch / a kick' is a melee
        # strike — the Throw skill is never bound by body weapons.
        if _THROW_STRIKE_RE.search(norm):
            out = [(p, pr) for p, pr in out
                   if pr.get("action_type") != "athletics"]
        return out

    def _disambiguate(self, keeper, char, rest: str, matches, prior=None):
        """Pick the prototype whose targets fit the words used.
        Returns (proto, verb, alts) — alts feed the clarify path."""
        if not matches:
            return None, "", []
        if len(matches) == 1:
            return matches[0][1], matches[0][0], []
        # A matched word that is part of an object NAME is not the verb:
        # 'kick the study door down' — 'study' belongs to the door.
        _OBJW = (r"door|lock|window|gate|latch|padlock|hatch|cabinet|chest|"
                 r"barricade|chain")
        matches = [m for m in matches if not re.search(
            rf"\b{re.escape(m[0])}\s+(?:{_OBJW})\b", rest)] or matches
        # A matched word after a movement preposition is a destination noun:
        # 'go to the study' — 'study' is where, not what you do.
        # v2.8.1.x P0-3: 'enter the Study' is likewise a crossing, never an
        # inspect/study frame — the room name is not the verb.
        matches = [m for m in matches
                   if not re.search(rf"(?:to|into|toward|towards|enter)\s+"
                                    rf"(?:the\s+)?{re.escape(m[0])}\b", rest)] or matches
        has_npc_word = any(
            re.search(rf"\b{re.escape(b)}\b", rest)
            for c in keeper.characters.values() if c.id != char.id
            for b in [c.id.replace("_", " ")] + c.name.lower().split() if b)
        # body parts mark a person target even when the name is mangled:
        # 'kick hobs in the shin' is an attack, not a door question.
        if not has_npc_word and re.search(
                r"\b(ribs|shin|shins|face|head|gut|stomach|belly|jaw|"
                r"throat|chest|leg|legs|arm|arms|groin|teeth)\b", rest):
            has_npc_word = True
        has_obj_word = bool(re.search(
            r"\b(door|lock|window|gate|latch|padlock|hatch|cabinet|chest|"
            r"barricade|chain)\b", rest))
        # pronoun 'it' points back at the previous frame's target
        if re.search(r"\bit\b", rest) and prior and prior[-1].target_type:
            want = {"object": ("object", "exit"), "npc": ("npc",)}.get(
                prior[-1].target_type)
            if want:
                for phrase, proto in matches:
                    if any(t in proto.get("targets", []) for t in want):
                        return proto, phrase, [p for _, p in matches if p is not proto]
        if has_npc_word and not has_obj_word:
            for phrase, proto in matches:
                if "npc" in proto.get("targets", []):
                    return proto, phrase, []
        if has_obj_word and not has_npc_word:
            for phrase, proto in matches:
                if any(t in proto.get("targets", []) for t in ("object", "exit")):
                    return proto, phrase, []
        # unresolvable from words alone: first match, rest become alternatives
        return matches[0][1], matches[0][0], [p for _, p in matches[1:]]

    # ------------------------------------------------------------- binding
    def _bind_target(self, keeper, char, rest: str, frame: IntentFrame,
                     proto: Optional[dict], prior: List[IntentFrame]):
        # NPCs by name (same room first, then any)
        chars = [c for c in keeper.characters.values() if c.id != char.id]
        same_room = [c for c in chars if c.location == char.location]
        for pool in (same_room, chars):
            for c in pool:
                bits = [c.id.replace("_", " ")] + c.name.lower().split()
                if any(b and re.search(rf"\b{re.escape(b)}\b", rest) for b in bits):
                    frame.target_id, frame.target_type = c.id, "npc"
                    return
        # pronouns: 'him', 'his face', 'her' with exactly one NPC around —
        # but only when the action can target people at all: 'burn them'
        # must never bind Hobbs (v2.8.1.7 P0-4).
        wants = set((proto or {}).get("targets", []))
        people_ok = not wants or "npc" in wants or "self" in wants
        if people_ok and re.search(r"\b(him|her|his face|them)\b", rest):
            if len(same_room) == 1:
                frame.target_id, frame.target_type = same_room[0].id, "npc"
                return
        # pronoun 'it'/'them' inherits the previous frame's target — with
        # the same target-type gate as direct pronouns.
        if re.search(r"\b(it|them)\b", rest) and prior and prior[-1].target_id:
            inherited = prior[-1].target_type
            if not wants or inherited in wants \
                    or (inherited == "npc" and people_ok):
                frame.target_id = prior[-1].target_id
                frame.target_type = inherited
                return
        # room objects by name, then generic object words; documents and
        # items next. When several match, the longest name wins, and on a
        # tie the LAST-mentioned noun wins ('study the letter' -> letter).
        cands = []  # (matched_name_len, position, id, type)
        for obj in keeper.world_objects.values():
            if obj.location_id != char.location:
                continue
            for b in obj.name.lower().split():
                m = re.search(rf"\b{re.escape(b)}\b", rest)
                if m:
                    cands.append((len(b), m.start(), obj.id, "object"))
        for inst in keeper.item_instances.values():
            visible = (inst.location_id == char.location and inst.owner_id is None
                       and "hidden" not in inst.tags)
            carried = inst.owner_id == char.id
            if not (visible or carried):
                continue
            for b in inst.name.lower().split():
                m = re.search(rf"\b{re.escape(b)}\b", rest)
                if m:
                    # 'breach the door with the shotgun' — the weapon after
                    # 'with' is the instrument, never the target.
                    with_at = rest.find(" with ")
                    if inst.item_type == "weapon" and with_at != -1 \
                            and m.start() > with_at:
                        continue
                    cands.append((len(b), m.start(), inst.id,
                                  "document" if inst.item_type in ("document", "clue")
                                  else "item"))
        if cands:
            _len, _pos, tid, ttype = max(cands, key=lambda c: (c[0], c[1]))
            frame.target_id, frame.target_type = tid, ttype
            return
        # 'the lock' belongs to the locked thing in the room
        if re.search(r"\block\b", rest):
            locked = next((o for o in keeper.world_objects.values()
                           if o.location_id == char.location
                           and o.properties.get("locked")), None)
            if locked is not None:
                frame.target_id, frame.target_type = locked.id, "object"
                return
        m = re.search(r"\b(door|lock|window|gate|latch|padlock|hatch|"
                      r"cabinet|chest|barricade|chain)\b", rest)
        if m:
            frame.target_id, frame.target_type = m.group(1), "object"
            return
        # v2.8.1.7 P0-4: 'burn them' points at the papers, never a person —
        # when every visible document in reach is the same kind of thing,
        # that is what burns. Genuinely different documents stay ambiguous.
        if frame.action_type == "fire_setting":
            docs = [i for i in keeper.item_instances.values()
                    if i.location_id == char.location and i.owner_id is None
                    and "hidden" not in i.tags
                    and i.item_type in ("document", "clue")]
            if docs and len({d.template_id for d in docs}) == 1:
                frame.target_id, frame.target_type = docs[0].id, "document"
                return
        # exits / rooms by name
        for lid, loc in keeper.locations.items():
            names = [loc.name.lower(), lid.replace("_", " ")]
            if any(re.search(rf"\b{re.escape(n)}\b", rest) for n in names if len(n) > 3):
                frame.target_id, frame.target_type = lid, "exit"
                return
        # generic 'the room'
        if re.search(r"\b(room|area|place)\b", rest):
            frame.target_id, frame.target_type = char.location, "room"

    def _bind_instrument(self, keeper, char, rest: str, frame: IntentFrame):
        gun_words = {"shotgun", "revolver", "pistol", "rifle", "gun", "knife"}
        for iid in char.inventory:
            inst = keeper.item_instances.get(iid)
            if inst is None:
                continue
            bits = set(inst.name.lower().split()) | {inst.template_id.replace("_", " ")}
            if any(re.search(rf"\b{re.escape(b)}\b", rest) for b in bits if len(b) > 2):
                frame.instrument_id = inst.id
                return
        m = re.search(r"\b(shotgun|revolver|pistol|rifle|knife)\b", rest)
        if m and char.equipped_item_id:
            frame.instrument_id = char.equipped_item_id
        elif m:
            frame.instrument_id = m.group(1)   # conceptual; resolver checks

    # ------------------------------------------------------------- scoring
    def _choose_skill(self, char, frame: IntentFrame, proto: Optional[dict],
                      explicit: Optional[str]) -> Optional[str]:
        if explicit:
            # 'roll strength for a round house kick' at a person is a brawl,
            # not a feat of strength — redirect with a note.
            if explicit == "STR" and (frame.target_type == "npc" or
                                      frame.action_type in ("melee_attack",
                                                            "nonlethal_attack")):
                frame.reason = "a strike at a person is Fighting Brawl, not a STR feat"
                return "Fighting_Brawl"
            return explicit
        if proto is None:
            return None
        cands = proto.get("skills") or []
        if not cands:
            return None
        # a verb may pin its own skill ('jump the fence' -> Jump, not Climb)
        vs = (proto.get("verb_skills") or {}).get(frame.verb)
        if vs:
            return vs
        at = frame.action_type
        # firearm-flavored skills need a firearm in hand; else raw muscle
        if at in ("object_attack", "ranged_attack"):
            has_gun = bool(char.weapon and char.weapon.base_range > 0)
            if not has_gun:
                return "Fighting_Brawl" if at == "ranged_attack" else "STR"
            return ("Firearms_Rifle_Shotgun" if char.weapon.is_shotgun
                    else "Firearms_Handgun")
        if len(cands) == 1:
            return cands[0][0]
        # instrument disambiguates remaining ties
        inst = str(frame.instrument_id or "").lower()
        if inst:
            for skill, _w in cands:
                if "Shotgun" in skill and "shotgun" in inst:
                    return skill
                if "Handgun" in skill and any(
                        w in inst for w in ("revolver", "pistol", "handgun")):
                    return skill
        return max(cands, key=lambda sw: sw[1])[0]

    def _score(self, frame: IntentFrame, proto: Optional[dict],
               explicit: Optional[str]) -> float:
        if explicit:
            return 0.95
        if proto is None:
            return 0.3
        words = len(frame.verb.split())
        score = {1: 0.55, 2: 0.65}.get(words, 0.75)
        if frame.target_id:
            score += 0.1
        if frame.instrument_id:
            score += 0.05
        return min(score, 0.95)

    # ------------------------------------------------------------- decision
    def _decide(self, keeper, char, frame: IntentFrame,
                proto: Optional[dict], explicit: Optional[str],
                alts: Optional[list] = None):
        if explicit and frame.skill:
            # 'roll intimidation to compell' — target inferred when the
            # scene offers exactly one sensible one.
            if not frame.target_id and proto is not None and \
                    "npc" in proto.get("targets", []):
                npc = self._nearest_npc(keeper, char)
                if npc is not None:
                    frame.target_id, frame.target_type = npc.id, "npc"
                    frame.reason = (frame.reason or
                                    f"target inferred: {npc.name}")
            frame.decision = "roll"
            frame.needs_roll = True
            if not frame.reason:
                frame.reason = f"explicit roll override: {frame.skill}"
            return
        if proto is None:
            frame.decision = "passthrough"
            frame.reason = "no action prototype matched"
            return

        policy = proto.get("roll", "never")
        wants = set(proto.get("targets", []))

        if policy == "never":
            # take/read/use/movement/observation resolve through the local
            # command layer — never a roll, never the LLM for the mechanics.
            if frame.action_type == "movement" and not frame.target_id:
                frame.decision = "passthrough"
                frame.reason = "no destination the engine can bind"
                return
            if wants and not frame.target_id and frame.action_type in (
                    "take_item", "read", "use_item"):
                frame.decision = "clarify"
                frame.clarify_options = ["name the thing you mean"]
                frame.reason = f"{frame.action_type} what, exactly?"
                frame.confidence = 0.5
                return
            frame.decision = "local"
            frame.reason = "deterministic local action"
            return

        if policy == "always":
            frame.decision = "roll"
            frame.needs_roll = True
            frame.reason = frame.reason or f"{frame.action_type} is uncertain; failure matters"
            return

        # policy == 'target': roll when the target is real and failure matters
        if frame.target_id:
            frame.decision = "roll"
            frame.needs_roll = True
            frame.reason = frame.reason or "target bound; outcome opposed or uncertain"
            return

        # genuine ambiguity with two concrete readings comes BEFORE the
        # nearest-NPC fallback: bare 'kick' with a person AND a door in the
        # room is a question, not an assault.
        if alts:
            options = self._clarify_options(keeper, char, frame, proto, alts)
            if options and frame.confidence >= CLARIFY_THRESHOLD:
                frame.decision = "clarify"
                frame.clarify_options = options
                frame.reason = " or ".join(options) + "?"
                return

        # attacks with no named target fall back to the nearest NPC (as the
        # preroll net has always done for a bare 'shoot' mid-fight)
        if frame.action_type in ("melee_attack", "ranged_attack", "nonlethal_attack",
                                 "coercion", "persuasion", "deception",
                                 "npc_handling"):
            npc = self._nearest_npc(keeper, char)
            if npc is not None:
                frame.target_id, frame.target_type = npc.id, "npc"
                frame.decision = "roll"
                frame.needs_roll = True
                frame.reason = frame.reason or "target inferred: nearest person here"
                return
            frame.decision = "passthrough"
            frame.reason = "no reachable target"
            return

        if frame.confidence >= ACT_THRESHOLD and frame.action_type in (
                "force_object", "object_attack", "locksmith"):
            frame.decision = "passthrough"
            frame.reason = "no breakable target in reach"
            return

        # genuine ambiguity with two concrete readings -> clarify locally
        options = self._clarify_options(keeper, char, frame, proto, alts)
        if options and frame.confidence >= CLARIFY_THRESHOLD:
            frame.decision = "clarify"
            frame.clarify_options = options
            frame.reason = " or ".join(options) + "?"
            return
        frame.decision = "passthrough"
        frame.reason = "nothing concrete to bind"

    def _nearest_npc(self, keeper, char):
        candidates = [c for c in keeper.characters.values()
                      if c.id != char.id and c.char_type != "player"]
        same_room = [c for c in candidates if c.location == char.location]
        return same_room[0] if same_room else (candidates[0] if candidates else None)

    def _clarify_options(self, keeper, char, frame, proto, alts=None) -> List[str]:
        """Two concrete readings -> a real choice; fewer -> nothing to ask."""
        alts = alts or []
        types = {frame.action_type} | {p.get("action_type") for p in alts}
        if {"melee_attack", "force_object"} & types and not frame.target_id:
            npc = self._nearest_npc(keeper, char)
            has_door = any(o.location_id == char.location
                           for o in keeper.world_objects.values())
            if has_door and npc is not None and \
                    {"melee_attack", "force_object"} <= types:
                return [f"{frame.verb} the {npc.name} (attack)",
                        f"{frame.verb} the door open (force)"]
        if frame.action_type in ("coercion", "persuasion") and not frame.target_id:
            return ["frighten them (Intimidate)", "appeal to reason (Persuade)"]
        return []
