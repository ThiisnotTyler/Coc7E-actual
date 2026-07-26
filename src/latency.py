"""Turn-level latency instrumentation.

v2.8.0 — debug-mode only.  Splits a turn into the phases that can hide
latency, so we know where time goes before we optimize anything.

Phases:
  prompt_build   time to build the JSON prompt sent to the LLM
  api_wait       time waiting on the LLM provider (network + generation)
  parse          time to parse/repair the model's JSON response
  state_apply    time to validate and apply the returned state delta
  save           time to persist the world state
"""
import hashlib
import json
import os
import subprocess
import sys
import time
from typing import Dict, List, Optional

PHASES = ["prompt_build", "api_wait", "parse", "state_apply", "save"]

TURN_TIMING_LOG = os.path.join("logs", "turn_timing.jsonl")

_commit_cache: Optional[str] = None
_commit_resolved = False


def project_version() -> str:
    """Single source of truth is src/__init__.py."""
    try:
        from src import __version__
        return __version__
    except Exception:
        return "unknown"


def git_commit() -> Optional[str]:
    """Short commit hash when the tree is a git checkout; None otherwise.

    Resolved once per process. Never raises — instrumentation must not break
    a session on a machine without git.
    """
    global _commit_cache, _commit_resolved
    if _commit_resolved:
        return _commit_cache
    _commit_resolved = True
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=2,
        )
        _commit_cache = out.stdout.strip() or None
    except Exception:
        _commit_cache = None
    return _commit_cache


def short_hash(text: str) -> str:
    """12-hex fingerprint — enough to tell two prompts/configs apart."""
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:12]


def prompt_fingerprint(system_prompt: str, user_prompt: str) -> str:
    return short_hash(system_prompt + "\n" + user_prompt)


def config_fingerprint(llm_config: dict) -> str:
    """Stable fingerprint of the LLM-relevant config. Never includes keys —
    api-key.json is a separate file and is never part of this dict."""
    try:
        canon = json.dumps(llm_config or {}, sort_keys=True, default=str)
    except (TypeError, ValueError):
        canon = repr(llm_config)
    return short_hash(canon)


def estimate_cost(model: str, pt: int = 0, ct: int = 0, cached: int = 0,
                  pricing: Optional[dict] = None) -> Optional[float]:
    """USD estimate from config/settings.json -> pricing rates (per 1M tokens).

    Returns None when the model has no rate card — never invents numbers."""
    rates = (pricing or {}).get(model)
    if not rates:
        return None
    cached = min(cached or 0, pt or 0)
    return ((cached or 0) * rates.get("input_cached", rates.get("input", 0))
            + ((pt or 0) - (cached or 0)) * rates.get("input", 0)
            + (ct or 0) * rates.get("output", 0)) / 1_000_000


def percentile(sorted_vals: List[float], q: float) -> Optional[float]:
    """Nearest-rank percentile of an already-sorted list."""
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    import math
    rank = max(1, math.ceil(q * len(sorted_vals)))
    return sorted_vals[min(rank, len(sorted_vals)) - 1]


def write_timing_row(path: str, row: dict):
    """Append one JSON line. Never raises: diagnostics must not break a session."""
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    except OSError:
        pass


class LatencyCollector:
    """Collects per-turn phase timings and prints a profile."""

    def __init__(self):
        self.turns: List[dict] = []

    def record(self, turn: int, **phases):
        """Store one turn's phase times (seconds)."""
        rec = {"turn": turn}
        for p in PHASES:
            rec[p] = float(phases.get(p, 0.0))
        self.turns.append(rec)

    def profile(self) -> dict:
        """Aggregate statistics across all recorded turns (milliseconds)."""
        if not self.turns:
            return {}
        out = {}
        for p in PHASES:
            vals = [t[p] * 1000.0 for t in self.turns]
            out[p] = {
                "avg": sum(vals) / len(vals),
                "min": min(vals),
                "max": max(vals),
                "total": sum(vals),
                "n": len(vals),
            }
        totals = [sum(t[p] for p in PHASES) * 1000.0 for t in self.turns]
        out["total"] = {
            "avg": sum(totals) / len(totals),
            "min": min(totals),
            "max": max(totals),
            "total": sum(totals),
            "n": len(totals),
        }
        return out

    def _format_phase(self, name: str, stats: dict) -> str:
        return (f"  {name:<14}"
                f"{stats['avg']:>9.2f}"
                f"{stats['min']:>9.2f}"
                f"{stats['max']:>9.2f}"
                f"{stats['total']:>10.2f}")

    def _historical_api_summary(self, log_path: str) -> List[str]:
        """Summarize any existing logs/llm_timing.jsonl rows."""
        if not os.path.exists(log_path):
            return ["No historical timing log found."]
        try:
            rows = [json.loads(l) for l in open(log_path, encoding="utf-8") if l.strip()]
        except (OSError, json.JSONDecodeError):
            return ["Historical timing log exists but could not be read."]
        if not rows:
            return ["Historical timing log is empty."]

        secs = [r.get("seconds", 0.0) for r in rows]
        by_model = {}
        for r in rows:
            key = (r.get("model", "?"), r.get("tier", "?"))
            by_model.setdefault(key, []).append(r)

        lines = [
            f"Historical API attempts from {log_path}:",
            f"  {len(rows)} attempt(s), avg {sum(secs) / len(secs):.2f}s, max {max(secs):.2f}s",
            "",
            f"  {'model':<18}{'tier':<9}{'calls':>7}{'avg s':>10}{'max s':>10}",
            "  " + "-" * 56,
        ]
        for (model, tier), rs in sorted(by_model.items()):
            s = [x.get("seconds", 0.0) for x in rs]
            lines.append(
                f"  {model:<18}{tier:<9}{len(rs):>7}{sum(s) / len(s):>10.2f}{max(s):>10.2f}"
            )
        return lines

    def print_profile(self, existing_log: str = "logs/llm_timing.jsonl",
                      file=None):
        """Print the aggregate profile plus any historical API data."""
        file = file or sys.stdout
        prof = self.profile()
        if not prof:
            print("[LATENCY PROFILE] no turns recorded", file=file)
            return

        print("\n" + "=" * 60, file=file)
        print("[LATENCY PROFILE — last {} turn(s)]".format(prof["prompt_build"]["n"]), file=file)
        print("  phase          avg ms    min ms    max ms   total ms", file=file)
        print("  " + "-" * 52, file=file)
        for p in PHASES:
            print(self._format_phase(p, prof[p]), file=file)
        print("  " + "-" * 52, file=file)
        print(self._format_phase("total", prof["total"]), file=file)
        print("=" * 60, file=file)

        print("\n" + "\n".join(self._historical_api_summary(existing_log)), file=file)
        print("=" * 60 + "\n", file=file)
