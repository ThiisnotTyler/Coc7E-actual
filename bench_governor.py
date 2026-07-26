"""Latency Governor benchmark (v2.8.1.6) — REAL API calls, spends tokens.

Run from the project root:  py bench_governor.py

Three live turns against the shipped provider (routine solo, complex solo,
routine duo) plus one offline timeout simulation (free). Every row lands in
logs/llm_timing.jsonl with source=bench-governor; analyze with
py test_latency.py --report.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.keeper import CoCKeeper
from src.character import Character
from src.latency_governor import GovernorTimeout, run_with_deadline


def _config():
    with open("config/settings.json", encoding="utf-8") as f:
        cfg = json.load(f)
    cfg["chronicle"] = {"backend": "off"}
    cfg["llm"]["debug"] = True
    return cfg


def _keeper(n_players, loc="house_exterior"):
    k = CoCKeeper(_config())
    k.load_scenario("data/scenarios/five-minute-house")
    if os.path.exists(k.save_path):
        os.remove(k.save_path)
    for i in range(n_players):
        k.add_player(Character(
            id=f"bench{i + 1}", name=f"Bench{i + 1}", char_type="player",
            STR=50, CON=50, SIZ=50, DEX=50,
            skills={"Spot_Hidden": 50, "Intimidate": 60}, location=loc))
        k.locations[loc].occupants.add(f"bench{i + 1}")
    k.current_scene = loc
    return k


def _turn(k, decls, label):
    t0 = time.perf_counter()
    r = k.take_turn(decls)
    dt = time.perf_counter() - t0
    ok = r is not None and bool(r.get("narration"))
    print(f"\n>>> {label}: {dt:.1f}s, narration={'yes' if ok else 'NO'}")
    return dt


def main():
    print("== Latency Governor benchmark (real API, 3 paid calls) ==\n")

    k = _keeper(1)
    _turn(k, {"bench1": "search the front of the house"},
          "routine solo turn (standard tier, 180s deadline)")

    k = _keeper(1, loc="house_study")
    _turn(k, {"bench1": "demand hobbs stop the counting"},
          "complex solo turn (Intimidate + standard tier)")

    k = _keeper(2)
    _turn(k, {"bench1": "search the front of the house",
              "bench2": "search the front of the house"},
          "routine duo turn (minimal tier, 120s deadline)")

    print("\n== timeout simulation (offline, free) ==")
    t0 = time.perf_counter()
    try:
        run_with_deadline(lambda: time.sleep(30), 0.5)
    except GovernorTimeout as e:
        print(f">>> abandoned at deadline in "
              f"{time.perf_counter() - t0:.2f}s ({e})")

    for f in ("saves/five-minute-house/world-state.json",):
        if os.path.exists(f):
            os.remove(f)


if __name__ == "__main__":
    main()
