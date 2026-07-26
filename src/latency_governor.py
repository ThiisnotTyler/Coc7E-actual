"""Latency Governor (v2.8.1.6) — measures, limits, and controls every LLM call.

Field data behind this module (logs/llm_timing.jsonl, July 22-24):
- prompts ran 12-14k chars against no cap at all;
- routine k2.6 calls took 110-260s, and two "180s timeouts" actually stalled
  ~542s because the SDK retried the timed-out request internally — the
  timeout REPORTED after the request returned instead of aborting it;
- the timeout retry re-sent the SAME 13k prompt and burned another 600s+.

The Governor decides, for every narration call: whether the LLM is needed,
prompt tier, model tier, max output budget, per-call deadline, retry policy,
and the fallback policy. It never touches gameplay rules — only the shape
and lifetime of the API call.

Prompt tiers:
  minimal        routine squad turns       (cap: max_routine_prompt_chars)
  standard       individual/complex turns  (cap: max_complex_prompt_chars)
  cinematic      CINEMATIC mode            (cap: max_cinematic_prompt_chars)
  compact_retry  timeout recovery only     (built fresh, materially smaller)

True cancellation: run_with_deadline() abandons the request at the deadline
(the client then closes and rebuilds its HTTP session), and the SDK's own
retry multiplication is disabled at construction — a 120s deadline means
120 seconds, not 3 x 180.
"""
import os
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

PROMPT_TIERS = ("minimal", "standard", "cinematic", "compact_retry")

# Output-length policy (words). The voice-task line in the prompt carries
# the target for the tier; the engine never truncates prose itself.
WORD_TARGETS = {
    "routine": (150, 300),
    "standard": (300, 500),
    "complex": (500, 700),
    "cinematic": (700, 900),
    "compact_retry": (80, 150),
}

# Output token budgets per prompt tier (JSON overhead included; reasoning
# models burn budget on hidden thinking — the benchmark showed k2.6 EMPTY
# (finish_reason=length) at 2048/3072, so the floor is the proven 5120 zone
# for standard turns; compact_retry stays small because its prompt is tiny).
TIER_BUDGETS = {
    "minimal": 4096,
    "standard": 5120,
    "cinematic": 8192,
    "compact_retry": 2048,
}

# The compact retry's system prompt: schema + restrictions only. The full
# rules document is ~11k chars and would dwarf the compact user prompt —
# with it, a "smaller retry" is 90% of the original call (benchmark, turn 1).
COMPACT_SYSTEM_PROMPT = (
    "You are the Keeper for Call of Cthulhu 7th Edition. Narrate ONLY the "
    "outcome described in the user message. Respond with RAW JSON ONLY, no "
    "markdown fences, with this schema: {\"mode\": \"squad|individual|"
    "cinematic\", \"narration\": \"...\", \"private_narrations\": {}, "
    "\"state_delta\": {}, \"required_actions\": \"What do you do?\", "
    "\"dice_requests\": [], \"mode_switch\": null}. Never change mechanics, "
    "move tracked items, reveal hidden facts, change NPC condition, or move "
    "characters between rooms."
)

CONFIG_DEFAULTS = {
    "routine_timeout": 120,
    "complex_timeout": 180,
    "heavy_timeout": 180,
    "compact_retry_timeout": 90,
    # v2.8.1.7 P0-1: caps measure the REAL provider request — system prompt
    # (~11.1k chars) + dynamic payload — never the payload alone.
    "max_routine_prompt_chars": 15000,
    "max_complex_prompt_chars": 17000,
    "max_cinematic_prompt_chars": 21000,
}

# v2.8.1.7 P0-7: provider-aware compact-retry budgets. Reasoning models
# (kimi-k2.6) starve below 4096 even on a tiny prompt — the field test saw
# EMPTY/invalid-JSON compact retries at 2048. Never lower without benchmark
# evidence. Config: latency.compact_budget_by_provider.
COMPACT_BUDGET_BY_PROVIDER = {
    "kimi": 4096,
    "kimi-cn": 4096,
    "deepseek": 4096,
}
COMPACT_BUDGET_DEFAULT = 2048

DEBUG_PROMPT_PATH = os.path.join("logs", "prompt_debug.txt")


class GovernorTimeout(Exception):
    """The call exceeded its deadline and the request was abandoned."""


class GovernorDegraded(Exception):
    """Initial call timed out AND the compact retry failed. The Keeper
    preserves the turn and offers the degraded-mode menu."""


@dataclass
class CallPlan:
    """The Governor's decision for one narration call."""
    needed: bool = True
    prompt_tier: str = "minimal"
    model_tier: str = "default"          # default | heavy
    budget: int = TIER_BUDGETS["minimal"]
    timeout: float = 120.0
    compact_timeout: float = 90.0
    compact_budget: int = COMPACT_BUDGET_DEFAULT
    prompt_cap: int = 15000
    word_target: Tuple[int, int] = WORD_TARGETS["routine"]
    json_retries: int = 1                # invalid-JSON retries (strict suffix)
    tier_reasons: List[str] = field(default_factory=list)
    allow_compact_retry: bool = True     # False on the compact attempt itself
    # v2.8.1.x P0-1: timing-row category for the first attempt when it is not
    # an ordinary 'initial' call (e.g. 'narration_validation_retry').
    attempt_label: Optional[str] = None

    def voice_task(self) -> str:
        lo, hi = self.word_target
        return (f"VOICE TASK: narrate ONLY the new outcome in {lo}-{hi} words. "
                "No repeated static room description, no command lists, no "
                "private notes for unaffected characters.")

    def for_compact_retry(self) -> "CallPlan":
        """The plan for a compact-retry attempt (v2.8.1.7 P0-2): compact
        budget, compact deadline, no further retries — exactly one attempt."""
        return CallPlan(
            needed=self.needed,
            prompt_tier="compact_retry",
            model_tier=self.model_tier,
            budget=self.compact_budget,
            timeout=self.compact_timeout,
            compact_timeout=self.compact_timeout,
            compact_budget=self.compact_budget,
            prompt_cap=self.prompt_cap,
            word_target=WORD_TARGETS["compact_retry"],
            json_retries=0,
            tier_reasons=list(self.tier_reasons) + ["compact retry"],
            allow_compact_retry=False,
        )

    def for_validation_retry(self) -> "CallPlan":
        """The plan for a narration-validation correction (v2.8.1.x P0-1).

        A rejected narration earns exactly ONE compact attempt: compact
        budget, compact deadline, no strict-retry ladder, no second compact
        retry — and its own attempt category in the timing log so --report
        can price validation retries separately from turn calls."""
        return CallPlan(
            needed=self.needed,
            prompt_tier="compact_retry",
            model_tier=self.model_tier,
            budget=self.compact_budget,
            timeout=self.compact_timeout,
            compact_timeout=self.compact_timeout,
            compact_budget=self.compact_budget,
            prompt_cap=self.prompt_cap,
            word_target=WORD_TARGETS["compact_retry"],
            json_retries=0,
            tier_reasons=list(self.tier_reasons) + ["narration validation retry"],
            allow_compact_retry=False,
            attempt_label="narration_validation_retry",
        )


def run_with_deadline(fn, timeout: float):
    """Run fn() in a daemon thread; raise GovernorTimeout at the deadline.

    Python cannot kill a thread, so 'cancellation' means: stop WAITING. The
    abandoned request dies with the process (daemon) and the caller closes
    the HTTP session behind it (see OpenAICompatClient.abort_in_flight).
    Combined with max_retries=0 on the SDK client, this is the difference
    between the field-log 542s stall and a real 120s deadline."""
    box: dict = {}

    def work():
        try:
            box["value"] = fn()
        except BaseException as e:          # noqa: BLE001 - re-raised below
            box["error"] = e

    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise GovernorTimeout(
            f"call abandoned at the {timeout}s deadline (request cancelled)")
    if "error" in box:
        raise box["error"]
    return box.get("value")


class LatencyGovernor:
    """Policy engine for LLM call shape and lifetime. Config: settings.json
    -> "latency" (hard targets), with CONFIG_DEFAULTS as the floor."""

    def __init__(self, config: Optional[dict] = None):
        lat = (config or {}).get("latency", {}) or {}
        self.cfg = {**CONFIG_DEFAULTS, **{k: v for k, v in lat.items() if v}}
        budgets = dict(TIER_BUDGETS)
        budgets.update(lat.get("budgets") or {})
        self.budgets = budgets
        self.debug_prompt_path = DEBUG_PROMPT_PATH

    # -------------------------------------------------------------- decision
    def narration_needed(self, declarations, dice_results, movement_events) -> bool:
        """Whether anything needs a voice at all. Local-resolution turns
        never reach this (the Keeper returns early), so this is the last
        gate, not the first."""
        return bool(declarations or dice_results or movement_events)

    def compact_budget_for(self, provider: Optional[str]) -> int:
        """v2.8.1.7 P0-7: provider-aware compact-retry budget."""
        overrides = dict(COMPACT_BUDGET_BY_PROVIDER)
        overrides.update(self.cfg.get("compact_budget_by_provider") or {})
        return int(overrides.get((provider or "").lower(),
                                 COMPACT_BUDGET_DEFAULT))

    def plan(self, mode, declarations=None, has_movement_events: bool = False,
             heavy_hint: bool = False, escalations: Optional[List[str]] = None,
             provider: Optional[str] = None) -> CallPlan:
        """Pick prompt tier, model tier, budget, deadline for this turn.

        heavy_hint is the Keeper's existing v2.8.1.3 computation (CINEMATIC,
        Mythos/creature scenes, front thresholds). Threat LANGUAGE never
        reaches this method — no automatic heavy escalation from words.
        v2.8.1.7 P0-6: escalating scene facts (NPC reveal, combat, hazard/
        SAN tags, clue reveal, front/timeline events, multi-character
        outcomes) override party-size heuristics — a duo entering an
        NPC's room is NOT a minimal turn."""
        mode_v = mode.value if hasattr(mode, "value") else str(mode)
        escalations = list(escalations or [])
        reasons: List[str] = []
        if mode_v == "cinematic":
            tier, model, timeout = "cinematic", "heavy", self.cfg["heavy_timeout"]
            cap, words = self.cfg["max_cinematic_prompt_chars"], WORD_TARGETS["cinematic"]
            reasons.append("cinematic mode")
        elif heavy_hint:
            # complex turn: Mythos/creature scene or a front at a threshold
            tier, model, timeout = "standard", "heavy", self.cfg["heavy_timeout"]
            cap, words = self.cfg["max_complex_prompt_chars"], WORD_TARGETS["complex"]
            reasons.append("engine heavy trigger (Mythos/front)")
        elif mode_v == "individual":
            tier, model, timeout = "standard", "default", self.cfg["complex_timeout"]
            cap, words = self.cfg["max_complex_prompt_chars"], WORD_TARGETS["standard"]
            reasons.append("individual mode")
        else:
            tier, model, timeout = "minimal", "default", self.cfg["routine_timeout"]
            cap, words = self.cfg["max_routine_prompt_chars"], WORD_TARGETS["routine"]
            reasons.append("squad routine")
        if escalations and tier == "minimal":
            # P0-6: escalation facts override the party-size heuristic.
            tier = "standard"
            timeout = self.cfg["complex_timeout"]
            cap = self.cfg["max_complex_prompt_chars"]
            words = WORD_TARGETS["standard"]
            reasons.extend(f"escalation:{e}" for e in escalations)
            reasons.append("escalation overrides minimal")
        elif escalations:
            reasons.extend(f"escalation:{e}" for e in escalations)
        return CallPlan(
            needed=True,
            prompt_tier=tier,
            model_tier=model,
            budget=self.budgets[tier],
            timeout=float(timeout),
            compact_timeout=float(self.cfg["compact_retry_timeout"]),
            compact_budget=self.compact_budget_for(provider),
            prompt_cap=int(cap),
            word_target=words,
            tier_reasons=reasons,
        )

    # ------------------------------------------------------- prompt assembly
    def assemble(self, sections: List[dict], plan: CallPlan,
                 system_prompt: str = "") -> Tuple[str, dict]:
        """Join prompt sections under the tier's char cap.

        Each section: {"key", "bucket", "text", "slim"?, "droppable"?}.
        Trimming is ordered: slim variants first (room details), then
        droppable sections (off-screen characters, fronts/plot). Every trim
        is recorded in the telemetry block."""
        secs = [dict(s) for s in sections]
        # The output-length policy rides the task section (v2.8.1.6 §7).
        for s in secs:
            if s.get("key") == "task":
                s["text"] = plan.voice_task() + "\n" + s["text"]
                break
        else:
            secs.append({"key": "task", "bucket": "other",
                         "text": plan.voice_task()})

        def joined():
            return "\n".join(s["text"] for s in secs if s["text"])

        def total():
            # v2.8.1.7 P0-1: caps measure the REAL provider request —
            # system prompt + dynamic payload, never the payload alone.
            return len(system_prompt or "") + len(joined())

        trimmed: List[str] = []
        for s in secs:
            if total() <= plan.prompt_cap:
                break
            if s.get("slim") and s["text"] != s["slim"]:
                s["text"] = s["slim"]
                trimmed.append(s["key"] + " slimmed")
        for s in secs:
            if total() <= plan.prompt_cap:
                break
            if s.get("droppable"):
                s["text"] = ""
                trimmed.append(s["key"] + " dropped")
        prompt = joined()
        telemetry = self.section_telemetry(system_prompt, secs, prompt, plan,
                                           trimmed)
        return prompt, telemetry

    def section_telemetry(self, system_prompt: str, sections: List[dict],
                          prompt: str, plan: CallPlan,
                          trimmed: Optional[List[str]] = None) -> dict:
        """Per-section char accounting: system / scenario / scene /
        characters / items/objects / fronts/plot / adjudication / chronicle /
        commands/help / other."""
        buckets: Dict[str, int] = {}
        for s in sections:
            b = s.get("bucket", "other")
            buckets[b] = buckets.get(b, 0) + s.get("telemetry_chars",
                                                   len(s.get("text", "")))
        tel = {
            "system": len(system_prompt or ""),
            "scenario": buckets.get("scenario", 0),
            "scene": buckets.get("scene", 0),
            "characters": buckets.get("characters", 0),
            "items/objects": buckets.get("items/objects", 0),
            "fronts/plot": buckets.get("fronts/plot", 0),
            "adjudication": buckets.get("adjudication", 0),
            "chronicle": buckets.get("chronicle", 0),
            "commands/help": buckets.get("commands/help", 0),
            "other": buckets.get("other", 0),
        }
        tel.update({
            "prompt_tier": plan.prompt_tier,
            "cap": plan.prompt_cap,
            # v2.8.1.7 P0-1: honest accounting, all three numbers.
            "dynamic_prompt_chars": len(prompt),
            "system_prompt_chars": len(system_prompt or ""),
            "total_prompt_chars": len(prompt) + len(system_prompt or ""),
            "over_cap": max(0, len(prompt) + len(system_prompt or "")
                            - plan.prompt_cap),
            "tier_reasons": list(plan.tier_reasons),
            "trimmed": list(trimmed or []),
            # legacy aliases (pre-P0-1 debug dumps)
            "user_prompt_chars": len(prompt),
            "total_chars": len(prompt) + len(system_prompt or ""),
        })
        return tel

    # ---------------------------------------------------------- compact retry
    def build_compact_prompt(self, keeper, mode, declarations,
                             dice_results, plan: Optional[CallPlan] = None) -> str:
        """A MATERIALLY smaller timeout-retry prompt (v2.8.1.6).

        The v2.8.1.3 recovery re-sent the original 13k prompt plus a suffix;
        the field log shows it taking 604-684s. This prompt is built fresh
        and carries only: scenario name/tone, current location, acting
        character, declaration, adjudication/dice outcome, visible NPCs,
        relevant state changes, and a short voice task."""
        from src.human_keeper import _dice_lines, _movement_lines

        mode_v = mode.value if hasattr(mode, "value") else str(mode)
        focus = next((keeper.characters[cid] for cid in (declarations or {})
                      if cid in keeper.characters), None)
        focus_loc = focus.location if focus is not None else keeper.current_scene
        loc = keeper.locations.get(focus_loc)
        names = {cid: c.name for cid, c in keeper.characters.items()}
        packet = {"character_names": names, "dice_results": dice_results or {},
                  "movement_events": list(keeper._movement_events or [])}

        L = [f"COMPACT RETRY — narrate briefly. Turn {keeper.turn}.",
             f"SCENARIO: {keeper.scenario_title} — {keeper.scenario_tone}",
             f"MODE: {mode_v}",
             f"LOCATION: {loc.name if loc else focus_loc}"
             + (f" — {loc.description[:200]}" if loc and loc.description else "")]
        for cid, text in (declarations or {}).items():
            L.append(f"ACTING: {names.get(cid, cid)} — \"{text}\"")
        outcomes = _dice_lines(packet)
        if outcomes:
            L.append("OUTCOME (engine-resolved, authoritative):")
            L.extend("  " + o for o in outcomes)
        npcs = [c for c in keeper.characters.values()
                if c.char_type != "player" and c.location == focus_loc
                and not c.extra.get("hidden")]
        if npcs:
            L.append("NPCS PRESENT: " + "; ".join(
                f"{c.name} ({c.get_condition()})" for c in npcs))
        changes = _movement_lines(packet)
        if changes:
            L.append("STATE CHANGES (already applied by the engine):")
            L.extend("  " + c for c in changes)
        lo, hi = WORD_TARGETS["compact_retry"]
        L.append(f"VOICE TASK: {lo}-{hi} words, outcome only — no room "
                 "re-description, no command lists. Respond with the SAME "
                 "JSON schema as the full prompt.")
        return "\n".join(L)

    # ------------------------------------------------------------- debug dump
    def dump_debug_prompt(self, system_prompt: str, prompt: str,
                          telemetry: dict) -> str:
        """--debug: persist the full prompt + telemetry for inspection.
        logs/ is gitignored, so this never leaves the machine. Never raises."""
        try:
            os.makedirs(os.path.dirname(self.debug_prompt_path) or ".",
                        exist_ok=True)
            with open(self.debug_prompt_path, "w", encoding="utf-8") as f:
                f.write("== latency governor telemetry ==\n")
                for k, v in telemetry.items():
                    f.write(f"{k}: {v}\n")
                f.write("\n== system prompt ==\n" + (system_prompt or ""))
                f.write("\n\n== user prompt ==\n" + (prompt or ""))
        except OSError:
            pass
        return self.debug_prompt_path
