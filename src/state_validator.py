"""State-delta validation for the CoC 7e LLM Keeper.

v2.7.6.1 -- the Truth Firewall.

The LLM may narrate and propose consequences, but it may not directly assign
canonical mechanical state. This module accepts a model-produced state_delta,
returns the subset that is safe for the engine to apply, and records every
rejected write for debugging and regression tests.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Set


# Canonical state owned by deterministic systems. The model may describe
# changes to these things, but it may not assign them directly.
ENGINE_OWNED_CHARACTER_FIELDS: Set[str] = {
    "id", "name", "char_type", "owner",
    "STR", "CON", "SIZ", "DEX", "APP", "INT", "POW", "EDU",
    "hp", "max_hp", "san", "max_san", "mp", "luck",
    "build", "damage_bonus", "move",
    "skills", "checked_skills",
    "major_wound", "dying", "unconscious",
    "temporarily_insane", "indefinitely_insane",
    "cthulhu_mythos", "san_loss_today",
    "weapon", "weapon_instances", "equipped_item_id", "armor",
    "scars", "phobias", "manias", "key_items", "inventory",
    "location",
    # v2.8.1.x P0-4: position is mechanically significant (combat range is
    # derived from it), so it is engine-owned mechanical state. Only
    # deterministic systems may change it: close distance, retreat, take
    # cover, forced movement, combat movement, scenario-authored placement.
    # Narration may describe apparent distance; it may not assign position.
    "position",
    # v2.8.1.x Phase 2: stance is engine-owned mechanical state (opposed
    # melee consumes it). Only the `stance` command changes it; narration
    # may reference a dodge or a fight-back, never assign the field.
    "stance",
}

# Narrative/adjacent fields the model may still update during the transition
# period. Phase 8 moves these into explicit proposal fields as well.
MODEL_SAFE_CHARACTER_FIELDS: Set[str] = {
    "declared_action", "personal_log", "extra",
}

# State-delta sections that contain proposals rather than direct mutations.
PROPOSAL_TOP_LEVEL_FIELDS: Set[str] = {
    "proposed_facts", "proposed_consequences", "npc_reactions", "sound_events",
}


def _short(value: Any, limit: int = 120) -> str:
    text = repr(value)
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


@dataclass(frozen=True)
class StateRejection:
    """One attempted model write that the engine refused."""

    path: str
    value: Any
    reason: str

    def format(self) -> str:
        return f"{self.path}: {self.reason} (value={_short(self.value)})"


@dataclass
class StateValidationReport:
    """Cleaned delta plus an audit trail of rejected writes."""

    delta: Dict[str, Any]
    rejected: List[StateRejection] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.rejected


class StateDeltaValidator:
    """Validate model-produced state deltas before they touch world state."""

    def validate(
        self,
        delta: Optional[Mapping[str, Any]],
        *,
        characters: Mapping[str, Any],
        fronts: Mapping[str, Any],
        locations: Mapping[str, Any],
    ) -> StateValidationReport:
        if not isinstance(delta, Mapping):
            return StateValidationReport(
                {},
                [StateRejection("state_delta", delta, "state_delta must be an object")],
            )

        clean: Dict[str, Any] = {}
        rejected: List[StateRejection] = []

        for key, value in delta.items():
            if key == "characters":
                accepted, errors = self._validate_characters(value, characters)
            elif key == "fronts":
                accepted, errors = self._validate_fronts(value, fronts)
            elif key == "plot_points":
                accepted, errors = self._validate_plot_points(value)
            elif key == "scene_transitions":
                accepted, errors = self._validate_scene_transitions(
                    value, characters=characters, locations=locations
                )
            elif key in PROPOSAL_TOP_LEVEL_FIELDS:
                accepted, errors = self._validate_proposal_field(
                    key, value, characters, locations
                )
            else:
                accepted, errors = None, [
                    StateRejection(
                        f"state_delta.{key}",
                        value,
                        "top-level field is not approved for model writes",
                    )
                ]

            rejected.extend(errors)
            if accepted not in (None, {}, []):
                clean[key] = accepted

        return StateValidationReport(clean, rejected)

    # ------------------------------------------------------------- characters
    def _validate_characters(
        self, value: Any, characters: Mapping[str, Any]
    ) -> tuple[Dict[str, Dict[str, Any]], List[StateRejection]]:
        if not isinstance(value, Mapping):
            return {}, [
                StateRejection("state_delta.characters", value, "must be an object")
            ]

        accepted: Dict[str, Dict[str, Any]] = {}
        rejected: List[StateRejection] = []

        for char_id, changes in value.items():
            path = f"state_delta.characters.{char_id}"
            if char_id not in characters:
                rejected.append(StateRejection(path, changes, "unknown character"))
                continue
            if not isinstance(changes, Mapping):
                rejected.append(
                    StateRejection(path, changes, "character changes must be an object")
                )
                continue

            safe_changes: Dict[str, Any] = {}
            for field_name, field_value in changes.items():
                field_path = f"{path}.{field_name}"
                if field_name in ENGINE_OWNED_CHARACTER_FIELDS:
                    rejected.append(
                        StateRejection(
                            field_path,
                            field_value,
                            "engine-owned field; use an engine event or proposal",
                        )
                    )
                elif field_name in MODEL_SAFE_CHARACTER_FIELDS:
                    safe_changes[field_name] = field_value
                else:
                    rejected.append(
                        StateRejection(
                            field_path,
                            field_value,
                            "field is not approved for direct model writes",
                        )
                    )

            if safe_changes:
                accepted[char_id] = safe_changes

        return accepted, rejected

    # ---------------------------------------------------------------- fronts
    def _validate_fronts(
        self, value: Any, fronts: Mapping[str, Any]
    ) -> tuple[Dict[str, int], List[StateRejection]]:
        if not isinstance(value, Mapping):
            return {}, [
                StateRejection("state_delta.fronts", value, "must be an object")
            ]

        accepted: Dict[str, int] = {}
        rejected: List[StateRejection] = []

        for front_id, raw_clock in value.items():
            path = f"state_delta.fronts.{front_id}"
            front = fronts.get(front_id)
            if front is None:
                rejected.append(StateRejection(path, raw_clock, "unknown front"))
                continue
            if not isinstance(raw_clock, (int, float)) or isinstance(raw_clock, bool):
                rejected.append(
                    StateRejection(path, raw_clock, "front clock must be numeric")
                )
                continue

            maximum = front.get("max", raw_clock) if isinstance(front, Mapping) else raw_clock
            try:
                maximum = int(maximum)
            except (TypeError, ValueError):
                maximum = int(raw_clock)
            accepted[front_id] = max(0, min(int(raw_clock), maximum))

        return accepted, rejected

    # ----------------------------------------------------------- plot points
    def _validate_plot_points(self, value: Any) -> tuple[List[str], List[StateRejection]]:
        if not isinstance(value, list):
            return [], [
                StateRejection("state_delta.plot_points", value, "must be a list")
            ]

        accepted: List[str] = []
        rejected: List[StateRejection] = []
        for i, point in enumerate(value):
            if isinstance(point, str) and point.strip():
                accepted.append(point.strip())
            else:
                rejected.append(
                    StateRejection(
                        f"state_delta.plot_points[{i}]",
                        point,
                        "plot points must be non-empty strings",
                    )
                )
        return accepted, rejected

    # ------------------------------------------------------ scene transitions
    def _validate_scene_transitions(
        self,
        value: Any,
        *,
        characters: Mapping[str, Any],
        locations: Mapping[str, Any],
    ) -> tuple[List[Dict[str, str]], List[StateRejection]]:
        if not isinstance(value, list):
            return [], [
                StateRejection("state_delta.scene_transitions", value, "must be a list")
            ]

        accepted: List[Dict[str, str]] = []
        rejected: List[StateRejection] = []

        for i, transition in enumerate(value):
            path = f"state_delta.scene_transitions[{i}]"
            if not isinstance(transition, Mapping):
                rejected.append(
                    StateRejection(path, transition, "transition must be an object")
                )
                continue

            clean_transition: Dict[str, str] = {}
            for char_id, destination in transition.items():
                char_path = f"{path}.{char_id}"
                char = characters.get(char_id)
                if char is None:
                    rejected.append(
                        StateRejection(char_path, destination, "unknown character")
                    )
                    continue
                if not isinstance(destination, str) or destination not in locations:
                    rejected.append(
                        StateRejection(char_path, destination, "unknown destination")
                    )
                    continue

                current = getattr(char, "location", None)
                current_location = locations.get(current)
                if destination == current:
                    clean_transition[char_id] = destination
                    continue

                connections = getattr(current_location, "connections", {}) or {}
                if current_location is None or destination not in connections:
                    rejected.append(
                        StateRejection(
                            char_path,
                            destination,
                            f"destination is not connected to current location {current!r}",
                        )
                    )
                    continue

                clean_transition[char_id] = destination

            if clean_transition:
                accepted.append(clean_transition)

        return accepted, rejected

    # ------------------------------------------------------------ proposals
    def _validate_proposal_field(
        self,
        key: str,
        value: Any,
        characters: Mapping[str, Any],
        locations: Mapping[str, Any],
    ) -> tuple[Any, List[StateRejection]]:
        """Validate proposal payloads without treating them as state writes."""
        if key in ("proposed_facts", "proposed_consequences", "npc_reactions"):
            if not isinstance(value, list):
                return None, [
                    StateRejection(
                        f"state_delta.{key}", value, "proposal field must be a list"
                    )
                ]
            return list(value), []

        if key == "sound_events":
            if not isinstance(value, list):
                return None, [
                    StateRejection(
                        "state_delta.sound_events", value, "sound events must be a list"
                    )
                ]
            accepted = []
            rejected: List[StateRejection] = []
            for i, event in enumerate(value):
                path = f"state_delta.sound_events[{i}]"
                if not isinstance(event, Mapping):
                    rejected.append(
                        StateRejection(path, event, "sound event must be an object")
                    )
                    continue
                location = event.get("location")
                noise = event.get("noise")
                heard_by = event.get("heard_by", [])
                if location is not None and location not in locations:
                    rejected.append(
                        StateRejection(f"{path}.location", location, "unknown location")
                    )
                    continue
                if (
                    not isinstance(noise, int)
                    or isinstance(noise, bool)
                    or not 1 <= noise <= 5
                ):
                    rejected.append(
                        StateRejection(
                            f"{path}.noise", noise, "noise must be an integer from 1 to 5"
                        )
                    )
                    continue
                if not isinstance(heard_by, list):
                    rejected.append(
                        StateRejection(
                            f"{path}.heard_by", heard_by, "heard_by must be a list"
                        )
                    )
                    continue
                clean_event = dict(event)
                clean_event["heard_by"] = [cid for cid in heard_by if cid in characters]
                accepted.append(clean_event)
            return accepted, rejected

        return None, [StateRejection(f"state_delta.{key}", value, "unsupported proposal field")]
