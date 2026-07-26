"""Live Kimi (Moonshot AI) smoke test — spends a few hundred tokens.

Uses the DEFAULT model tier (kimi-k2.6) only — NOT heavy/k3. In the real
game, heavy is reserved for INDIVIDUAL-mode turns (keeper.py); this test
passes use_heavy=False, so the request goes to the cheap default model.

Prereq: pip install openai, and kimi_api_key set in config/api-key.json
(create the key at platform.moonshot.ai -> API Keys).
Run from the project root:  python test_kimi.py
Expected: a JSON object with narration / state_delta / required_actions.
"""
import json
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.llm_client import build_llm_client

with open("config/settings.json", encoding="utf-8") as f:
    config = json.load(f)
config["llm"]["provider"] = "kimi"          # force, in case settings say otherwise

client = build_llm_client(config)
print(f"Sending ONE request to provider={client.provider} model={client.default_model} "
      f"(default tier — heavy model {client.heavy_model} is NOT used by this test) ...")

with open("config/system-prompt.txt", encoding="utf-8") as f:
    system_prompt = f.read()

test_prompt = """
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

result = client.query(system_prompt, test_prompt, use_heavy=False)
print(json.dumps(result, indent=2, ensure_ascii=False))
assert "narration" in result, "Response missing 'narration' key"
print("\nLIVE KIMI TEST PASSED")
