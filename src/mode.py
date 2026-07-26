"""Resolution mode selection (squad / individual / cinematic).

v2.2: uses the setup guide's complete version — the review draft dropped
_same_action(), which meant SQUAD mode could never trigger and every turn
burned the expensive heavy model. Restored, so routine group exploration
stays on Flash.
"""
from enum import Enum
from typing import Dict, List
from src.character import Character


class ResolutionMode(Enum):
    SQUAD = "squad"
    INDIVIDUAL = "individual"
    CINEMATIC = "cinematic"


class ModeSelector:
    COMBAT_KEYWORDS = ["attack", "fight", "shoot", "stab", "kill", "ambush",
                       "surprise", "grapple", "punch", "draw weapon"]
    SANITY_KEYWORDS = ["witness", "behold", "ghoul", "entity", "tentacle",
                       "corpse", "ritual", "sacrifice"]
    CHASE_KEYWORDS = ["chase", "escape", "pursue", "flee"]

    def select_mode(self, characters: List[Character], declarations: Dict[str, str],
                    scene_tension: int) -> ResolutionMode:
        actions = " ".join(declarations.values()).lower()
        if any(k in actions for k in self.CHASE_KEYWORDS):
            return ResolutionMode.CINEMATIC
        if any(k in actions for k in self.COMBAT_KEYWORDS):
            return ResolutionMode.INDIVIDUAL
        if scene_tension >= 3 or any(k in actions for k in self.SANITY_KEYWORDS):
            return ResolutionMode.INDIVIDUAL
        if any(c.dying or c.unconscious or c.major_wound for c in characters):
            return ResolutionMode.INDIVIDUAL
        if len(characters) >= 3 and self._same_action(declarations) and scene_tension < 2:
            return ResolutionMode.SQUAD
        return ResolutionMode.INDIVIDUAL

    def _same_action(self, declarations: Dict[str, str]) -> bool:
        types = [self._categorize(a) for a in declarations.values()]
        return len(set(types)) == 1

    def _categorize(self, action: str) -> str:
        a = action.lower()
        if any(w in a for w in ["search", "look", "find", "examine", "inspect"]):
            return "search"
        if any(w in a for w in ["talk", "ask", "persuade", "question"]):
            return "talk"
        if any(w in a for w in ["move", "go", "walk", "enter", "head"]):
            return "move"
        if any(w in a for w in ["fight", "attack", "shoot"]):
            return "fight"
        return "other"
