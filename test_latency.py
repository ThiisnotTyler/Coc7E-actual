"""Kimi latency benchmark + timing-log analyzer — answers "why was that turn slow?"

Modes, run from the project root:

  python test_latency.py            # BENCHMARK (spends tokens, 2 calls): one
                                    # default-tier call + one heavy-tier call.
  python test_latency.py --ab       # A/B BUDGET BENCHMARK (spends tokens, 4 calls):
                                    # default 8192 vs 4096, heavy 8192 vs 6144.
  python test_latency.py --ab 2     # same, 2 reps per cell (8 calls, capped at 3).
  python test_latency.py --report   # REPORT (free, no API): analyzes
                                    # logs/llm_timing.jsonl, separated by project
                                    # version, successful-only latency stats.

Paid calls are always explicit: nothing here spends tokens unless you run it
without --report.

How to read the numbers:
- Every turn is up to 3 API calls: initial (1x budget) -> strict-retry (2x) ->
  final-retry (4x). strict-retry/final-retry rows = malformed or empty output.
- Rows written before v2.8.0 carry no "version" field; they are bucketed as
  "legacy". Legacy rows also mix real sessions with offline test-suite
  simulations (tiny prompt_chars) — that blending is exactly why v2.8.0
  stamps version, source, and fingerprints on every row.
- heavy tier (kimi-k3) only fires on INDIVIDUAL-mode turns (keeper.py).
"""
import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

TIMING_LOG = os.path.join("logs", "llm_timing.jsonl")

SMOKE_PROMPT = """
TURN 1
MODE: SQUAD
CURRENT SCENE: corbitt_house_exterior (Outside Corbitt House)
EXITS: {"corbitt_house_ground_floor": "Ground Floor"}
ACTIVE CHARACTERS:
- Eleanor Vance (Journalist): HP 11/11, SAN 60, Spot Hidden 45, Library Use 70
- Samuel Carter (Professor): HP 10/10, SAN 55, History 65, Occult 40
- Martha Finn (Nurse): HP 12/12, SAN 65, First Aid 60, Listen 50
PLAYER DECLARATIONS:
- Eleanor: "Approach the front door and knock"
- Samuel: "Stand back and observe the house"
- Martha: "Check if anyone is watching us from the street"
DICE RESULTS:
- Spot Hidden (Eleanor): 34, Regular
- Spot Hidden (Martha): 67, Failure
- Listen (Samuel): 23, Regular
FRONTS: {"ritual": 0}
PLOT POINTS: []
NARRATE THIS TURN.
"""


def _pct(sorted_vals, q):
    from src.latency import percentile
    return percentile(sorted_vals, q)


def estimate_cost(rows, pricing):
    """Token + cost totals per model (v2.7.5: 'what does this game cost?').

    Uses the provider's own meter: rows logged since v2.7.5 carry pt
    (prompt tokens), ct (completion tokens), and cached (cache-hit prompt
    tokens) when the provider reports them. pricing maps a model name to
    USD-per-1M-token rates: {"input": x, "input_cached": y, "output": z}.
    Older rows and unpriced models are counted, never costed — no invented
    numbers."""
    out = {}
    for r in rows:
        m = r.get("model", "?")
        d = out.setdefault(m, {"calls": 0, "pt": 0, "ct": 0, "cached": 0,
                               "cost": 0.0, "priced": bool(pricing.get(m))})
        d["calls"] += 1
        pt = r.get("pt") or 0
        ct = r.get("ct") or 0
        cached = min(r.get("cached") or 0, pt)
        d["pt"] += pt
        d["ct"] += ct
        d["cached"] += cached
        p = pricing.get(m)
        if p:
            d["cost"] += (cached * p.get("input_cached", p.get("input", 0))
                          + (pt - cached) * p.get("input", 0)
                          + ct * p.get("output", 0)) / 1_000_000
    return out


def _load_pricing():
    try:
        with open("config/settings.json", encoding="utf-8") as f:
            return json.load(f).get("pricing", {})
    except (OSError, json.JSONDecodeError):
        return {}


def _version_report(version, rows):
    """One version bucket: successful-only latency stats + reliability rates."""
    oks = [r for r in rows if r.get("ok")]
    secs = sorted(r.get("seconds", 0.0) for r in oks)
    initial = [r for r in rows if r.get("attempt", "initial") == "initial"]
    clean = sum(1 for r in initial if r.get("ok"))
    malformed = sum(1 for r in rows if r.get("error") == "invalid-json")
    empty = sum(1 for r in rows if "empty" in str(r.get("error", "")))
    retry_rows = sum(1 for r in rows if r.get("attempt", "initial") != "initial")
    retried_turns = sum(1 for r in initial if not r.get("ok"))

    print(f"\n== version {version} — {len(rows)} attempt row(s), "
          f"{len(oks)} successful ==")
    srcs = {}
    for r in rows:
        s = r.get("source", "-")
        srcs[s] = srcs.get(s, 0) + 1
    if len(srcs) > 1 or (srcs and list(srcs) != ["-"]):
        print("  sources: " + ", ".join(f"{k} x{v}" for k, v in sorted(srcs.items())))
    print(f"  {'model':<16}{'tier':<9}{'calls':>6}{'fail':>6}{'avg s':>8}{'max s':>8}")
    print("  " + "-" * 53)
    by_model = {}
    for r in rows:
        by_model.setdefault((r.get("model", "?"), r.get("tier", "?")), []).append(r)
    for (model, tier), rs in sorted(by_model.items()):
        s = [x.get("seconds", 0.0) for x in rs]
        fails = sum(1 for x in rs if not x.get("ok"))
        print(f"  {model:<16}{tier:<9}{len(rs):>6}{fails:>6}"
              f"{sum(s) / len(s):>8.1f}{max(s):>8.1f}")

    if secs:
        p50, p95 = _pct(secs, 0.50), _pct(secs, 0.95)
        print(f"\n  successful-only latency: avg {sum(secs) / len(secs):.1f}s, "
              f"p50 {p50:.1f}s, p95 {p95:.1f}s, max {max(secs):.1f}s "
              f"(n={len(secs)})")
    else:
        print("\n  successful-only latency: no successful rows")
    n_init = len(initial) or 1
    print(f"  clean first-try rate: {clean}/{len(initial)} initial calls "
          f"({100.0 * clean / n_init:.0f}%)")
    print(f"  malformed-output rate: {malformed}/{len(rows)} rows "
          f"({100.0 * malformed / (len(rows) or 1):.0f}%)"
          + (f"  [+ {empty} empty-response row(s)]" if empty else ""))
    print(f"  retry rate: {retry_rows}/{len(rows)} rows were retries "
          f"({100.0 * retry_rows / (len(rows) or 1):.0f}%); "
          f"{retried_turns}/{len(initial)} turns needed a retry")

    # --- v2.8.1.6 Latency Governor metrics -------------------------------
    timeouts = [r for r in rows if r.get("error") == "timeout"]
    print(f"  timeout rate: {len(timeouts)}/{len(rows)} rows "
          f"({100.0 * len(timeouts) / (len(rows) or 1):.0f}%)")
    print(f"\n  {'model':<16}{'tier':<9}{'ok n':>5}{'p50 s':>8}{'p95 s':>8}"
          f"{'avg dyn ch':>11}{'avg tot ch':>11}{'avg tok out':>12}")
    print("  " + "-" * 82)
    for (model, tier), rs in sorted(by_model.items()):
        ok_rs = [x for x in rs if x.get("ok")]
        ok_secs = sorted(x.get("seconds", 0.0) for x in ok_rs)
        p50 = _pct(ok_secs, 0.50)
        p95 = _pct(ok_secs, 0.95)
        dyn = [x["dynamic_prompt_chars"] for x in rs
               if isinstance(x.get("dynamic_prompt_chars"), int)]
        tot = [x["total_prompt_chars"] for x in rs
               if isinstance(x.get("total_prompt_chars"), int)]
        pchars = [x.get("prompt_chars", 0) for x in rs if x.get("prompt_chars")]
        cts = [x["ct"] for x in rs if isinstance(x.get("ct"), int)]
        # v2.8.1.7 P0-1: dynamic/total when rows carry them; legacy rows
        # fall back to prompt_chars as the total.
        if not tot and pchars:
            tot = pchars
        print(f"  {model:<16}{tier:<9}{len(ok_rs):>5}"
              f"{(f'{p50:.1f}' if p50 is not None else '-'):>8}"
              f"{(f'{p95:.1f}' if p95 is not None else '-'):>8}"
              f"{(f'{sum(dyn) / len(dyn):.0f}' if dyn else '-'):>11}"
              f"{(f'{sum(tot) / len(tot):.0f}' if tot else '-'):>11}"
              f"{(f'{sum(cts) / len(cts):.0f}' if cts else '-'):>12}")
    compact = [r for r in rows if "compact" in str(r.get("attempt", ""))
               or r.get("prompt_tier") == "compact_retry"]
    if compact:
        c_ok = sum(1 for r in compact if r.get("ok"))
        print(f"\n  compact retry success rate: {c_ok}/{len(compact)} "
              f"({100.0 * c_ok / len(compact):.0f}%)")
    recovery = [r for r in rows
                if "timeout-recovery" in str(r.get("attempt", ""))
                or "compact-retry" in str(r.get("attempt", ""))]
    if recovery:
        r_ok = sum(1 for r in recovery if r.get("ok"))
        print(f"  timeout recovery success rate: {r_ok}/{len(recovery)} "
              f"({100.0 * r_ok / len(recovery):.0f}%)")


def _source_class(r):
    """Bucket a timing row by provenance (v2.8.1.1).

    Optimization decisions must never blend real sessions with offline
    test-suite simulations. Rows from the game carry source=keeper, the
    benchmarks carry bench/bench-ab, pre-versioning rows are legacy, and
    anything else version-stamped but source-unset is a test simulation
    (the suites now write to logs/test_llm_timing.jsonl instead)."""
    s = r.get("source")
    if s in ("keeper", "bench", "bench-ab"):
        return s
    if not r.get("version"):
        return "legacy"
    return "test-simulation"


def report():
    if not os.path.exists(TIMING_LOG):
        print("No timing data yet — play a session or run the benchmark first.")
        return
    recs = [json.loads(l) for l in open(TIMING_LOG, encoding="utf-8") if l.strip()]
    if not recs:
        print(f"{TIMING_LOG} is empty.")
        return

    buckets = {}
    for r in recs:
        buckets.setdefault(_source_class(r), []).append(r)
    sim = buckets.get("test-simulation", [])
    legacy = buckets.get("legacy", [])
    opt = [r for cls in ("keeper", "bench", "bench-ab")
           for r in buckets.get(cls, [])]

    print(f"== {len(recs)} recorded attempt(s) in {TIMING_LOG} ==")
    print(f"   optimization metrics exclude {len(sim)} offline test-simulation row(s)"
          + (f" and {len(legacy)} legacy row(s)" if legacy else "")
          + " (shown separately below)")

    # --- optimization metrics: real sessions + benchmarks only ---
    if opt:
        by_version = {}
        for r in opt:
            by_version.setdefault(r.get("version") or "unknown", []).append(r)
        for v in sorted(by_version):
            _version_report(v, by_version[v])
    else:
        print("\n  no keeper/bench rows yet — play a session or run the benchmark")

    # --- legacy rows: pre-versioning, mixed provenance, informational only ---
    if legacy:
        secs = [r.get("seconds", 0.0) for r in legacy]
        oks = sum(1 for r in legacy if r.get("ok"))
        print(f"\n== legacy bucket (pre-versioning; mixes real sessions with "
              f"simulations — NOT used for optimization metrics) ==")
        print(f"  {len(legacy)} rows, {oks} ok, avg {sum(secs) / len(secs):.1f}s, "
              f"max {max(secs):.1f}s")

    # --- simulations: counted, never trusted ---
    if sim:
        print(f"\n== test-simulation bucket (offline suite artifacts — excluded) ==")
        print(f"  {len(sim)} rows; timing content is synthetic (stubbed clients)")

    slow = sorted(opt, key=lambda r: -r.get("seconds", 0))[:5]
    if slow:
        print("\n  slowest calls (real/bench only):")
        for r in slow:
            err = f"  [{r.get('error')}]" if r.get("error") else ""
            print(f"    {r.get('seconds', 0):>6.1f}s  {r.get('ts', '?')}  {r.get('model', '?')}"
                  f"  {r.get('attempt', '?')} budget={r.get('budget', '?')}  "
                  f"v={r.get('version', 'legacy')} src={r.get('source', '-')}{err}")

    # v2.7.5: the cost meter. Priced over the same rows the metrics trust,
    # plus legacy (those were real paid calls too).
    cost_rows = opt + legacy
    if any(r.get("pt") for r in cost_rows):
        pricing = _load_pricing()
        est = estimate_cost(cost_rows, pricing)
        print("\n  tokens & estimated cost (provider meter; real+bench+legacy rows):")
        print(f"  {'model':<16}{'calls':>6}{'tok in':>12}{'cached':>10}{'tok out':>10}{'~cost':>10}")
        print("  " + "-" * 63)
        grand = 0.0
        for model, d in sorted(est.items()):
            grand += d["cost"]
            cost = f"${d['cost']:.4f}" if d["priced"] else "  (no rates)"
            print(f"  {model:<16}{d['calls']:>6}{d['pt']:>12,}{d['cached']:>10,}"
                  f"{d['ct']:>10,}{cost:>10}")
        print(f"  {'TOTAL':<16}{sum(d['calls'] for d in est.values()):>6}"
              f"{sum(d['pt'] for d in est.values()):>12,}"
              f"{sum(d['cached'] for d in est.values()):>10,}"
              f"{sum(d['ct'] for d in est.values()):>10,}"
              f"{'$' + format(grand, '.4f'):>10}")
    print("""
  reading it:
  - optimization decisions use keeper/bench/bench-ab rows only; legacy and
    test-simulation buckets are informational.
  - offline suites now write simulations to logs/test_llm_timing.jsonl.
  - many strict/final-retry rows  -> malformed JSON regens are your multiplier;
    check logs/llm_raw_*.txt and the 'finish' field (length = truncation).
  - high p95 on 'heavy'           -> k3 turns (INDIVIDUAL mode) are the cost.""")


def _build_bench_client():
    from src.llm_client import build_llm_client

    with open("config/settings.json", encoding="utf-8") as f:
        config = json.load(f)
    config["llm"]["provider"] = "kimi"          # force, like test_kimi.py
    config["llm"]["debug"] = True               # live per-attempt echo
    client = build_llm_client(config)
    with open("config/system-prompt.txt", encoding="utf-8") as f:
        system_prompt = f.read()
    return client, system_prompt


def bench():
    client, system_prompt = _build_bench_client()
    print(f"Benchmark: 2 paid calls on provider={client.provider} "
          f"(default={client.default_model}, heavy={client.heavy_model})\n")
    for use_heavy in (False, True):
        tier = "heavy" if use_heavy else "default"
        model = client.heavy_model if use_heavy else client.default_model
        t0 = time.perf_counter()
        result = client.query(system_prompt, SMOKE_PROMPT, use_heavy=use_heavy,
                              context={"source": "bench"})
        dt = time.perf_counter() - t0
        narration = str(result.get("narration", ""))
        print(f"  {tier:<8}{model:<14}{dt:>7.1f}s total, narration {len(narration)} chars\n")

    print("Benchmark rows appended to the log; full history:\n")
    report()


# --------------------------------------------------------------------- A/B
# Output-budget A/B: does a smaller max_output_tokens keep JSON reliability
# and narration quality? One session id tags every row so the analysis reads
# only this run.

AB_CELLS = [
    # (tier label, use_heavy, base budget)
    ("default", False, 8192),
    ("default", False, 4096),
    ("heavy", True, 8192),
    ("heavy", True, 6144),
]


def bench_ab(reps=1):
    reps = max(1, min(3, reps))
    client, system_prompt = _build_bench_client()
    session = datetime.now(timezone.utc).strftime("ab-%Y%m%dT%H%M%SZ")
    total_calls = len(AB_CELLS) * reps
    print(f"A/B budget benchmark: {total_calls} paid call(s), session {session}")
    print("cells: " + ", ".join(f"{t}@{b}" for t, _, b in AB_CELLS) + "\n")

    narrations = {}
    for tier, use_heavy, budget in AB_CELLS:
        model = client.heavy_model if use_heavy else client.default_model
        for rep in range(reps):
            t0 = time.perf_counter()
            try:
                result = client.query(
                    system_prompt, SMOKE_PROMPT, use_heavy=use_heavy,
                    budget=budget,
                    context={"source": "bench-ab", "bench": session,
                             "base_budget": budget})
                dt = time.perf_counter() - t0
                narr = str(result.get("narration", ""))
                narrations.setdefault((tier, budget), []).append(narr)
                print(f"  {tier}@{budget:<5} rep{rep + 1}: {dt:6.1f}s, "
                      f"narration {len(narr)} chars")
            except Exception as e:
                dt = time.perf_counter() - t0
                narrations.setdefault((tier, budget), []).append("")
                print(f"  {tier}@{budget:<5} rep{rep + 1}: FAILED after {dt:.1f}s "
                      f"({str(e)[:120]})")

    # Analyze only this session's rows.
    recs = [json.loads(l) for l in open(TIMING_LOG, encoding="utf-8") if l.strip()]
    rows = [r for r in recs if r.get("bench") == session]

    print(f"\n== A/B results (session {session}) ==")
    print(f"  {'cell':<15}{'calls':>6}{'clean1st':>9}{'malformed':>10}{'retries':>8}"
          f"{'avg ok s':>9}{'p95 ok s':>9}{'tok in':>9}{'tok out':>9}"
          f"{'narr ch':>8}{'~cost':>9}")
    print("  " + "-" * 99)
    for tier, use_heavy, budget in AB_CELLS:
        cell = [r for r in rows
                if r.get("tier") == tier and r.get("base_budget") == budget]
        calls = sum(1 for r in cell if r.get("attempt") == "initial")
        clean = sum(1 for r in cell if r.get("attempt") == "initial" and r.get("ok"))
        malformed = sum(1 for r in cell if r.get("error") == "invalid-json")
        retries = sum(1 for r in cell if r.get("attempt") != "initial")
        ok_secs = sorted(r.get("seconds", 0.0) for r in cell if r.get("ok"))
        avg_ok = sum(ok_secs) / len(ok_secs) if ok_secs else 0.0
        p95 = _pct(ok_secs, 0.95) or 0.0
        tok_in = sum(r.get("pt") or 0 for r in cell)
        tok_out = sum(r.get("ct") or 0 for r in cell)
        cost = sum(r.get("cost") or 0.0 for r in cell)
        narr_lens = [len(n) for n in narrations.get((tier, budget), [])]
        narr = max(narr_lens) if narr_lens else 0
        print(f"  {tier + '@' + str(budget):<15}{calls:>6}"
              f"{(f'{clean}/{calls}'):>9}{malformed:>10}{retries:>8}"
              f"{avg_ok:>9.1f}{p95:>9.1f}{tok_in:>9,}{tok_out:>9,}"
              f"{narr:>8}{('$' + format(cost, '.4f')):>9}")

    # Truncation / quality flags: finish=length on any row, empty narrations,
    # or a smaller-budget cell whose best narration is <60% of its tier
    # sibling's.
    print("\n  quality flags:")
    flagged = False
    for tier, use_heavy, budget in AB_CELLS:
        cell = [r for r in rows
                if r.get("tier") == tier and r.get("base_budget") == budget]
        trunc = [r for r in cell if r.get("finish") == "length"]
        if trunc:
            flagged = True
            print(f"    {tier}@{budget}: {len(trunc)} row(s) ended "
                  f"finish_reason=length (truncated generation)")
    for tier in ("default", "heavy"):
        budgets = [b for t, _, b in AB_CELLS if t == tier]
        big, small = max(budgets), min(budgets)
        big_n = max((len(n) for n in narrations.get((tier, big), [""])), default=0)
        small_n = max((len(n) for n in narrations.get((tier, small), [""])), default=0)
        if big_n and small_n and small_n < 0.6 * big_n:
            flagged = True
            print(f"    {tier}@{small}: best narration {small_n} chars vs "
                  f"{big_n} at @{big} — possible quality regression")
        if big_n and not small_n:
            flagged = True
            print(f"    {tier}@{small}: produced no narration while "
                  f"@{big} succeeded")
    if not flagged:
        print("    none — no truncation markers, no obvious narration collapse")

    print("""
  decision rule: only lower max_output_tokens permanently if the smaller
  budget shows equal or better clean first-try rate AND no quality flags.
  One rep per cell is a smoke signal, not a statistic — rerun with '--ab 2'
  or '--ab 3' before touching config/settings.json.""")


if __name__ == "__main__":
    if "--report" in sys.argv:
        report()
    elif "--ab" in sys.argv:
        idx = sys.argv.index("--ab")
        reps = 1
        if idx + 1 < len(sys.argv):
            try:
                reps = int(sys.argv[idx + 1])
            except ValueError:
                pass
        bench_ab(reps)
    else:
        bench()
