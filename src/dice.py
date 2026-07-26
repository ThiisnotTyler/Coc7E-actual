"""CoC 7e dice engine.

v2.2: bonus/penalty dice are now rolled the way the rulebook actually says —
extra TENS dice, keep the best (bonus) or worst (penalty) tens result, units
die unchanged. Both prior drafts re-rolled full d100s and min/maxed them,
which skews probabilities hard. Also added the RAW fumble threshold:
96-100 is a fumble when the skill target is below 50.
"""
import random
from typing import Tuple

# CoC 7e success-level ordering for opposed (roll-vs-roll) resolution.
# 01 is ALWAYS a Critical and outranks everything; a Fumble is the worst
# possible outcome. Used by opposed melee (dodge / fight back).
LEVEL_RANK = {"Fumble": 0, "Failure": 1, "Regular": 2, "Hard": 3,
              "Extreme": 4, "Critical": 5}


def _tens_units(roll: int) -> Tuple[int, int]:
    if roll == 100:
        return 0, 0
    return roll // 10, roll % 10


def _compose(tens: int, units: int) -> int:
    value = tens * 10 + units
    return 100 if value == 0 else value


class DiceEngine:
    def d100(self) -> int:
        return random.randint(1, 100)

    def d(self, sides: int, count: int = 1) -> int:
        return sum(random.randint(1, sides) for _ in range(count))

    def skill_check(self, target: int, bonus: int = 0, penalty: int = 0) -> Tuple[int, str]:
        """Roll vs target. Returns (final_roll, success_level).

        v2.8.1.x combat conversion: 01 is ALWAYS a Critical success (RAW) —
        it outranks Extreme and, for impaling weapons at extreme range, is
        the only roll that impales. Bonus and penalty dice cancel 1:1 (RAW)
        before either is applied."""
        if bonus > 0 and penalty > 0:
            bonus, penalty = max(0, bonus - penalty), max(0, penalty - bonus)
        base = self.d100()
        tens, units = _tens_units(base)
        if bonus > 0:
            tens = min([tens] + [random.randint(0, 9) for _ in range(bonus)])
        elif penalty > 0:
            tens = max([tens] + [random.randint(0, 9) for _ in range(penalty)])
        roll = _compose(tens, units)

        fumble_at = 100 if target >= 50 else 96
        if roll >= fumble_at:
            return roll, "Fumble"
        if roll == 1:
            return roll, "Critical"
        if roll <= target // 5:
            return roll, "Extreme"
        if roll <= target // 2:
            return roll, "Hard"
        if roll <= target:
            return roll, "Regular"
        return roll, "Failure"

    def luck_roll(self, luck: int) -> Tuple[int, str]:
        return self.skill_check(luck)
