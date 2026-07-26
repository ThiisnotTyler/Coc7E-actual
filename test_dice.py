"""test_dice.py - statistical accuracy suite for the CoC 7e dice engine.

Standalone module (no scenario, no saves, no LLM). Answers one question:
"are the dice actually fair, and do the derived mechanics (success levels,
bonus/penalty dice, fumble thresholds) produce the probabilities the 7e
rulebook promises?"

Deterministic: every section reseeds the RNG, so results are reproducible
run-to-run. Tolerances are wide enough (+-3pp or +-12 sigma) that a fair
RNG never fails; a skewed one fails hard.

Run from the project root:  py test_dice.py
"""
import math
import random
import sys

from src.dice import DiceEngine

_passed = 0
_failed = 0


def check(name, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ok  {name}")
    else:
        _failed += 1
        print(f"  FAIL: {name}")


def pct(n, total):
    return 100.0 * n / total


dice = DiceEngine()

print("== d100: bounds and uniformity ==")
N = 100_000
random.seed(7)
rolls = [dice.d100() for _ in range(N)]
check("every d100 lands in [1, 100]", all(1 <= r <= 100 for r in rolls))
# chi-square over the 100 faces; df=99, reject blatant skew (crit ~150 @ p=0.001)
expected = N / 100
chi2 = sum((rolls.count(f) - expected) ** 2 / expected for f in range(1, 101))
print(f"      chi2={chi2:.1f} (df=99, critical ~150)")
check("d100 faces are uniform (chi-square)", chi2 < 150)
lowest = min(rolls.count(f) for f in range(1, 101))
highest = max(rolls.count(f) for f in range(1, 101))
print(f"      face counts: min={lowest} max={highest} (expected ~1000, sd ~31.6)")
check("no face is starved or hot (+-12 sigma)", lowest > 600 and highest < 1400)

print("== d(sides): sums and spread ==")
random.seed(11)
d6x3 = [dice.d(6, 3) for _ in range(50_000)]
mean = sum(d6x3) / len(d6x3)
var = sum((x - mean) ** 2 for x in d6x3) / len(d6x3)
print(f"      3D6 mean={mean:.3f} (10.5) sd={math.sqrt(var):.3f} (2.958)")
check("3D6 mean and spread match theory",
      abs(mean - 10.5) < 0.05 and abs(math.sqrt(var) - 2.958) < 0.05)
check("3D6 respects bounds", min(d6x3) >= 3 and max(d6x3) <= 18)

print("== success-level distribution (target = 60) ==")
N = 200_000
random.seed(13)
levels = {}
for _ in range(N):
    _, lv = dice.skill_check(60)
    levels[lv] = levels.get(lv, 0) + 1
# Theory @60: Critical roll=1 (1%), Extreme 2-12 (11%), Hard 13-30 (18%),
# Regular 31-60 (30%), Failure 61-99 (39%), Fumble 100 (1%).
exp60 = {"Critical": 1.0, "Extreme": 11.0, "Hard": 18.0, "Regular": 30.0, "Failure": 39.0, "Fumble": 1.0}
for lv, e in exp60.items():
    got = pct(levels.get(lv, 0), N)
    print(f"      {lv:8s} {got:5.2f}% (theory {e}%)")
    check(f"{lv} rate within +-3pp of theory @60", abs(got - e) < (3.0 if e > 2 else 0.7))

print("== fumble threshold switches at skill < 50 (RAW) ==")
N = 200_000
random.seed(17)
f40 = sum(1 for _ in range(N) if dice.skill_check(40)[1] == "Fumble")
print(f"      fumble @40: {pct(f40, N):.2f}% (theory 5%: rolls 96-100)")
check("fumble @40 is 96-100 (5%)", abs(pct(f40, N) - 5.0) < 1.5)
random.seed(19)
f60 = sum(1 for _ in range(N) if dice.skill_check(60)[1] == "Fumble")
print(f"      fumble @60: {pct(f60, N):.2f}% (theory 1%: roll 100 only)")
check("fumble @60 is 100 only (1%)", abs(pct(f60, N) - 1.0) < 0.7)
random.seed(21)
low = [dice.skill_check(5)[1] for _ in range(N)]
succ5 = pct(sum(1 for lv in low if lv in ("Extreme", "Hard", "Regular")), N)
print(f"      success @5: {succ5:.2f}% (theory 5%)")
check("low-skill success rate holds", abs(succ5 - 5.0) < 1.5)

print("== bonus / penalty dice (RAW tens-die method) ==")
N = 200_000
SUCCESS = ("Regular", "Hard", "Extreme", "Critical")

random.seed(23)
b1 = pct(sum(1 for _ in range(N) if dice.skill_check(50, bonus=1)[1] in SUCCESS), N)
print(f"      1 bonus die @50: {b1:.2f}% (theory ~75%)")
check("bonus die lifts 50% to ~75%", abs(b1 - 75.0) < 3.0)

random.seed(29)
b2 = pct(sum(1 for _ in range(N) if dice.skill_check(50, bonus=2)[1] in SUCCESS), N)
print(f"      2 bonus dice @50: {b2:.2f}% (theory ~87.5%)")
check("two bonus dice lift 50% to ~87.5%", abs(b2 - 87.5) < 3.0)

random.seed(31)
p1 = pct(sum(1 for _ in range(N) if dice.skill_check(50, penalty=1)[1] in SUCCESS), N)
print(f"      1 penalty die @50: {p1:.2f}% (theory ~25%)")
check("penalty die drops 50% to ~25%", abs(p1 - 25.0) < 3.0)

print("== independence: no detectable streak structure ==")
random.seed(37)
seq = [dice.d100() for _ in range(100_000)]
m = sum(seq) / len(seq)
num = sum((seq[i] - m) * (seq[i + 1] - m) for i in range(len(seq) - 1))
den = sum((x - m) ** 2 for x in seq)
r = num / den
print(f"      lag-1 autocorrelation r={r:+.4f} (fair RNG: ~0)")
check("consecutive rolls are uncorrelated", abs(r) < 0.01)

print("== 'bad luck' reality check (binomial math, no dice) ==")
def binom_pmf(n, k, p):
    return math.comb(n, k) * p ** k * (1 - p) ** (n - k)

f3of5 = sum(binom_pmf(5, k, 0.30) for k in range(3, 6))
print(f"      P(3+ failures in 5 rolls @70% skill)      = {f3of5 * 100:5.1f}%")
f2 = 0.30 * 0.30
print(f"      P(two failures in a row @70% skill)       = {f2 * 100:5.1f}%")
f4 = 0.35 ** 4
print(f"      P(four failures in a row @65% skill)      = {f4 * 100:5.1f}%  (~1 in {round(1 / f4)})")
print("      (your field log: 99/99/98/90 on 60-75% skills is a ~1.5%")
print("       streak - genuinely unlucky, but expected once every ~67 sessions)")

print()
if _failed:
    print(f"DICE AUDIT FAILED: {_failed} check(s) failed, {_passed} passed")
    sys.exit(1)
print(f"DICE AUDIT PASSED ({_passed} checks)")
