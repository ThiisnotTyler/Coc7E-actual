"""Status view (v2.8.1.x Phase 0) — the status sheet projection.

A READ-ONLY projection of engine truth for one character: vitals, the
equipped weapon with its kind and ammo, what's carried, and where they
stand. `build_status` returns the structured dict — that is the contract
a future UI consumes; `render_status` is just the terminal renderer over
it (the build_human_keeper_packet / render_human_keeper_packet pair
pattern).

Truth Firewall: this module displays only fields the engine already owns.
Stance became an engine field in Phase 2 (see
docs/MOVEMENT-STEALTH-ROADMAP.md) and is projected here; sneaking is NOT
an engine field yet (Phase 3), so it remains deliberately ABSENT — the UI
must never invent mechanical state.
"""
from src import items as items_mod

# What a position band means at the table (bands mirror combat's nominal
# yards; melee reach is ~3y). Display semantics only — combat owns the math.
_MELEE_REACH = {"close": "within striking reach"}


def build_status(keeper, char) -> dict:
    """The status projection for one character, engine truth only."""
    weapon = None
    if char.equipped_item_id:
        inst = keeper.item_instances.get(char.equipped_item_id)
        if inst is not None:
            tmpl = keeper.item_templates.get(inst.template_id)
            weapon = {
                "name": inst.name,
                "kind": items_mod.weapon_kind_label(tmpl, char.weapon),
                # ammo is a firearm figure; a melee weapon shows none
                "ammo": (char.weapon.ammo
                         if char.weapon is not None
                         and char.weapon.base_range > 0 else None),
            }
    inventory = []
    for iid in char.inventory:
        inst = keeper.item_instances.get(iid)
        inventory.append(inst.name if inst is not None else iid)
    return {
        "id": char.id,
        "name": char.name,
        "hp": char.hp,
        "max_hp": char.max_hp,
        "condition": char.get_condition(),
        "san": char.san,
        "weapon": weapon,
        "inventory": inventory,
        "position": char.position,
        # Phase 2: engine-owned melee defense choice (None = engine policy)
        "stance": char.stance,
    }


def render_status(status: dict) -> str:
    """The terminal sheet for one projection dict."""
    lines = [f"--- {status['name']} ---"]
    condition = str(status.get("condition", "")).replace("_", " ")
    lines.append(f"HP {status['hp']}/{status['max_hp']} ({condition})"
                 f" — SAN {status.get('san', '—')}")
    weapon = status.get("weapon")
    if weapon is not None:
        ammo = (f" [ammo {weapon['ammo']}]"
                if weapon.get("ammo") is not None else "")
        lines.append(f"In hand: {weapon['name']} ({weapon['kind']}){ammo}")
    else:
        lines.append("In hand: empty.")
    carrying = status.get("inventory") or []
    lines.append("Carrying: " + ("; ".join(carrying)
                                 if carrying else "empty."))
    position = str(status.get("position", "")).replace("_", " ")
    reach = _MELEE_REACH.get(status.get("position"),
                             "out of striking reach")
    lines.append(f"Position: {position} — {reach}")
    stance = status.get("stance")
    stance_text = (str(stance).replace("_", " ") if stance
                   else "auto (engine policy)")
    lines.append(f"Stance: {stance_text}")
    return "\n".join(lines)
