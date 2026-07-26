"""Offline mock Keeper — lets you test the ENTIRE engine loop (dice, combat,
state deltas, scene transitions, saving, chronicle batching) without an API
key and without spending a single token.

Enable with:  python -m src.main --mock
"""
import json
import re


class MockKeeperClient:
    """Duck-types GeminiClient.query(). Deterministic, free, instant."""

    def __init__(self, *args, **kwargs):
        self.calls = 0

    def query(self, system_prompt: str, user_prompt: str, use_heavy: bool = False,
              timing: dict = None) -> dict:
        self.calls += 1
        if isinstance(timing, dict):
            timing["api_wait"] = 0.0
            timing["parse"] = 0.0
        declarations = self._extract_json_block(user_prompt, "PLAYER DECLARATIONS:")
        mode = self._extract_field(user_prompt, "MODE:") or "individual"
        scene = self._extract_field(user_prompt, "CURRENT SCENE:") or "unknown"
        exits = self._extract_json_block(user_prompt, "EXITS:")
        dice_results = self._extract_json_block(user_prompt, "DICE RESULTS:")

        actions = " ".join(declarations.values()).lower()
        state_delta = {"characters": {}, "fronts": {}, "plot_points": [],
                       "scene_transitions": [], "sound_events": []}

        bits = []
        dice_requests = []
        for cid, action in declarations.items():
            if re.search(r"\b(shoot|attack|stab|punch|fight)\b", action, re.I):
                bits.append(f"{cid} commits to violence — resolve it from DICE RESULTS.")
            elif re.search(r"\b(search|look|examine|inspect|spot)\b", action, re.I):
                bits.append(f"{cid} pores over the surroundings; check DICE RESULTS for what surfaces.")
            elif re.search(r"\b(jimmy|lockpick|locksmith|pry|latch|tumbler|padlock)\b"
                           r"|pick\s+(the|a|that)\s+(lock|padlock)", action, re.I):
                # v2.7.1: exercise the dice-request channel offline — if the
                # preroll net already rolled this, resolve from DICE RESULTS;
                # otherwise ask the engine to roll next turn.
                if cid in dice_results:
                    bits.append(f"{cid} works the mechanism — the DICE RESULTS decide whether it gives.")
                else:
                    bits.append(f"{cid} sets to work on the mechanism; steady hands will decide it.")
                    dice_requests.append({"character": cid, "skill": "Locksmith",
                                          "reason": action.strip()[:80]})
            elif re.search(r"\b(enter|go|walk|head|move|inside|climb|descend)\b", action, re.I):
                bits.append(f"{cid} moves through the house.")
                if exits:
                    dest = next(iter(exits))
                    state_delta["scene_transitions"].append({cid: dest})
            else:
                bits.append(f"{cid} hesitates, and the old house seems to listen.")

        # Idle players nudge the ritual clock forward (NARRATION RULES say so)
        state_delta["fronts"]["ritual"] = min(6, self.calls // 3)

        narration = (
            f"[MOCK KEEPER — no API call made] Turn proceeds in {scene.replace('_', ' ')}. "
            + " ".join(bits)
            + " The air smells of mildew and candle wax; somewhere below, something "
              "shifts against the floorboards."
        )
        return {
            "mode": mode,
            "narration": narration,
            "private_narrations": {},
            "state_delta": state_delta,
            "required_actions": "What do you do? (mock: try 'search the room', 'enter the house', or 'shoot lusk')",
            "dice_requests": dice_requests,
            "mode_switch": None,
        }

    @staticmethod
    def _extract_field(prompt: str, label: str) -> str:
        for line in prompt.splitlines():
            if line.startswith(label):
                return line.split(":", 1)[1].strip()
        return ""

    @staticmethod
    def _extract_json_block(prompt: str, label: str) -> dict:
        idx = prompt.find(label)
        if idx == -1:
            return {}
        start = prompt.find("{", idx)
        if start == -1:
            return {}
        depth = 0
        for i in range(start, len(prompt)):
            if prompt[i] == "{":
                depth += 1
            elif prompt[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(prompt[start:i + 1])
                    except json.JSONDecodeError:
                        return {}
        return {}
