"""Sanity mechanics for CoC 7e — the module the v2.1 guide listed but never shipped.

Implements: SAN rolls with "loss_if_success/loss_if_fail" strings ("0/1D6"),
temporary insanity (5+ SAN in one roll -> INT roll), indefinite insanity
(20% of starting SAN lost in one day), Cthulhu Mythos gain, and the
99-minus-Mythos maximum SAN ceiling.

The engine returns an `events` list; the Keeper feeds those into the LLM
prompt so the *narration* of the bout of madness stays with the model while
the *math* stays here, deterministic and auditable.
"""
from src.character import Character
from src.dice import DiceEngine
from src.combat import CombatEngine


class SanityEngine:
    def __init__(self, dice: DiceEngine, combat: CombatEngine, config: dict | None = None):
        cfg = (config or {}).get("sanity", config or {})
        self.dice = dice
        self.combat = combat
        self.temp_threshold = int(cfg.get("temp_insanity_threshold", 5))
        self.indefinite_fraction = 0.20
        self.mythos_first_bonus = int(cfg.get("mythos_first_bonus", 5))
        self.mythos_subsequent_bonus = int(cfg.get("mythos_subsequent_bonus", 1))

    def sanity_roll(self, char: Character, loss_if_success: str = "0",
                    loss_if_fail: str = "1D6", mythos_source: bool = False) -> dict:
        """Roll SAN and apply the appropriate loss. Returns a full report dict."""
        report = {"char_id": char.id, "events": []}
        roll = self.dice.d100()
        success = roll <= char.san
        report["roll"] = roll
        report["san_at_roll"] = char.san
        report["success"] = success

        loss_str = loss_if_success if success else loss_if_fail
        loss = self.combat.roll_damage(loss_str) if loss_str not in ("", "0") else 0
        report["loss"] = loss

        if loss <= 0:
            report["events"].append("Held firm.")
            return report

        char.san = max(0, char.san - loss)
        char.san_loss_today += loss
        report["san_now"] = char.san

        # Temporary insanity: 5+ SAN lost from a single roll -> INT roll
        if loss >= self.temp_threshold and not char.temporarily_insane:
            int_roll = self.dice.d100()
            understood = int_roll <= char.INT
            report["int_roll"] = int_roll
            if understood:
                char.temporarily_insane = True
                report["events"].append(
                    f"TEMPORARY INSANITY (lost {loss}, INT roll {int_roll} <= {char.INT}): "
                    "bout of madness — 1D10 rounds if with the party, 1D10 hours if alone.")
            else:
                report["events"].append(
                    f"Mind rejects it (INT roll {int_roll} > {char.INT}) — no insanity, "
                    "but the Keeper may describe shaken nerves.")

        # Indefinite insanity: lost 20%+ of the day's starting SAN
        day_start = char.san + char.san_loss_today
        if day_start > 0 and char.san_loss_today >= max(1, round(day_start * self.indefinite_fraction)):
            if not char.indefinitely_insane:
                char.indefinitely_insane = True
                report["events"].append(
                    f"INDEFINITE INSANITY ({char.san_loss_today} SAN lost today, "
                    f"threshold {round(day_start * self.indefinite_fraction)}). "
                    "Requires care/recovery to lift; further SAN loss triggers bouts.")

        # Cthulhu Mythos gain from Mythos-induced insanity
        if mythos_source and (char.temporarily_insane or char.indefinitely_insane):
            gain = self.mythos_first_bonus if char.cthulhu_mythos == 0 else self.mythos_subsequent_bonus
            char.cthulhu_mythos += gain
            char.max_san = 99 - char.cthulhu_mythos
            char.san = min(char.san, char.max_san)
            report["events"].append(
                f"Cthulhu Mythos +{gain}% (now {char.cthulhu_mythos}%). Max SAN is now {char.max_san}.")

        if char.san == 0:
            report["events"].append("SAN 0 — the mind is gone. Permanent insanity.")
        return report
