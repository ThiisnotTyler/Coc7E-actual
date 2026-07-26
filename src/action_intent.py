"""Intent frames for the adjudication pipeline (v2.8.1.2).

A raw declaration becomes one or more IntentFrames. The frame is the
contract between the adjudicator (which fills it) and the resolver (which
executes it) — and, in --debug, the thing printed so the table can see why
the engine decided what it decided.
"""
import re
from dataclasses import dataclass, field
from typing import List, Optional

DECISIONS = ("roll", "no_roll", "local", "clarify", "impossible", "passthrough")

_WS = re.compile(r"\s+")
_TRAIL = re.compile(r"[.!?]+$")
_ARTICLE = re.compile(r"^(?:the|a|an)\s+")


def normalize(text: str) -> str:
    """Lowercase, collapse whitespace, strip trailing sentence punctuation."""
    t = _WS.sub(" ", (text or "").strip().lower())
    return _TRAIL.sub("", t)


def strip_article(text: str) -> str:
    return _ARTICLE.sub("", (text or "").strip())


@dataclass
class IntentFrame:
    """One adjudicated action."""
    raw: str                                   # original segment text
    action_type: str = "unknown"
    verb: str = ""                             # normalized verb phrase matched
    target_id: Optional[str] = None
    target_type: Optional[str] = None          # npc/object/item/document/exit/room/self
    dest_id: Optional[str] = None              # forced-movement destination
    instrument_id: Optional[str] = None
    goal: str = ""                             # what the player wants, best guess
    manner: List[str] = field(default_factory=list)  # nonlethal, quiet, ...
    explicit_skill: Optional[str] = None       # 'roll Intimidate' override
    skill: Optional[str] = None                # chosen skill for the roll
    confidence: float = 0.0
    needs_roll: bool = False
    decision: str = "passthrough"              # see DECISIONS
    reason: str = ""
    conditional_on: Optional[int] = None       # index of an earlier frame
    clarify_options: List[str] = field(default_factory=list)

    def debug_line(self) -> str:
        tgt = f"{self.target_type}:{self.target_id}" if self.target_id else "-"
        return (f"{self.action_type}/{self.verb or '?'} target={tgt} "
                f"skill={self.skill or self.explicit_skill or '-'} "
                f"conf={self.confidence:.2f} -> {self.decision}"
                + (f" ({self.reason})" if self.reason else ""))
