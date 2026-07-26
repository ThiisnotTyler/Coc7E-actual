# LLM Providers — Kimi API & Compatible Services (v2.3)

The Keeper talks to the LLM through one interface (`query(system_prompt, user_prompt)`).
`src/llm_client.py` implements that interface for **any OpenAI-compatible API** plus
Google Gemini. Switching providers is a one-line change in `config/settings.json` —
no other code changes, and the Google Docs chronicle is independent of which LLM you use.

```json
"llm": {
  "provider": "kimi",                       // <-- the only line that matters
  "api_key_file": "config/api-key.json",
  "temperature": 0.7,
  "max_output_tokens": 4096,
  "models": { "default": "kimi-k2.6", "heavy": "kimi-k3", "test": "kimi-k2.6" }
}
```

- `default` — routine turns (cheap, fast)
- `heavy` — combat / sanity / horror scenes (INDIVIDUAL mode)
- `test` — used by the live smoke tests

---

## 1. Using the Kimi API (Moonshot AI) — step by step

The Kimi API is fully **OpenAI-compatible**: you call it with the standard `openai`
Python SDK, just pointed at Moonshot's base URL.

### 1.1 Get your key
1. Go to **platform.moonshot.ai** (international) or **platform.moonshot.cn** (China) and sign in.
2. Open **API Keys**, create a key, copy it.
3. Paste it into `config/api-key.json`:
   ```json
   { "kimi_api_key": "sk-..." }
   ```
   or set the environment variable instead: `export MOONSHOT_API_KEY="sk-..."`
   (Windows PowerShell: `$env:MOONSHOT_API_KEY="sk-..."`)

### 1.2 Configure
`config/settings.json` ships preconfigured for Kimi:

```json
"llm": {
  "provider": "kimi",
  "models": { "default": "kimi-k2.6", "heavy": "kimi-k3", "test": "kimi-k2.6" }
}
```

- Use `"provider": "kimi-cn"` if your key is from the **China** platform
  (`https://api.moonshot.cn/v1`) — the two platforms have separate accounts and keys.
- `pip install openai` (already in requirements.txt).

### 1.3 Test
```bash
python test_engine.py     # offline, no tokens
python -m src.main --mock # full loop, no tokens
python test_kimi.py       # live smoke test, a few hundred tokens
python -m src.main        # real session on Kimi
```

### 1.4 Kimi model guide (verified July 2026, platform.moonshot.ai docs)

| Model ID | Context | Use for | Notes |
|---|---|---|---|
| `kimi-k2.6` | 256K | **default** | General-purpose, thinking + non-thinking modes, text/image/video input |
| `kimi-k3` | **1M** | **heavy** | Flagship (2.8T params). Uses a top-level `reasoning_effort` field — parameter handling differs from other models; the client auto-degrades if a parameter is rejected |
| `kimi-k2.7-code` | 256K | optional | Coding-focused; overkill for narration |
| `kimi-k2.7-code-highspeed` | 256K | optional | Same, higher output speed |
| `kimi-k2.5` | 256K | fallback | Previous generation |
| `moonshot-v1-*` | — | avoid | Classic series, platform sunset announced for Aug 31 |

JSON Mode (forced valid JSON output) is supported platform-wide — the client enables
it on every call. Current pricing is on the platform's pricing page
(platform.moonshot.ai/docs/pricing/chat); it changes, so check there rather than
trusting any hardcoded number, including this document.

---

## 2. Compatible services (one client, many backends)

Everything below speaks the OpenAI chat-completions format, so it works through the
same `OpenAICompatClient` — set `provider`, drop the key in `config/api-key.json`, done.

| `provider` | Service | Base URL (preset) | Key field in api-key.json | Notes |
|---|---|---|---|---|
| `kimi` | Moonshot AI (intl) | `https://api.moonshot.ai/v1` | `kimi_api_key` | Recommended default |
| `kimi-cn` | Moonshot AI (China) | `https://api.moonshot.cn/v1` | `kimi_api_key` | Separate account/key from intl |
| `deepseek` | DeepSeek | `https://api.deepseek.com` | `deepseek_api_key` | Cheapest quality option, 1M context |
| `openai` | OpenAI | SDK default | `openai_api_key` | Set models yourself (e.g. gpt-4.1-mini) |
| `openrouter` | OpenRouter | `https://openrouter.ai/api/v1` | `openrouter_api_key` | One key, hundreds of models (`vendor/model` IDs) |
| `groq` | Groq | `https://api.groq.com/openai/v1` | `groq_api_key` | Very fast inference, open-weight models |
| `together` | Together AI | `https://api.together.xyz/v1` | `together_api_key` | Hosts Kimi-K2-Instruct and many others |
| `xai` | xAI (Grok) | `https://api.x.ai/v1` | `xai_api_key` | grok-4 family |
| `ollama` | Ollama (local) | `http://localhost:11434/v1` | none needed | Free, offline, private; needs a GPU/Apple Silicon for decent models |
| `lmstudio` | LM Studio (local) | `http://localhost:1234/v1` | none needed | Free local server with GUI |
| `custom` | Anything else | you set `llm.base_url` | `custom_api_key` | For proxies and exotic endpoints |
| `gemini` | Google AI Studio | (native SDK) | `gemini_api_key` | The original backend, still fully supported |

### DeepSeek specifics (verified against official docs, July 2026)
- Use `deepseek-v4-flash` (default) and `deepseek-v4-pro` (heavy). Both 1M context, JSON Output supported.
- The old `deepseek-chat` / `deepseek-reasoner` aliases are **deprecated on 2026-07-24** — do not use them.
- Pricing: flash $0.14 / 1M input (cache miss; **$0.0028 on cache hit** — the stable
  system prompt makes Keeper turns cache-friendly) and $0.28 / 1M output.
  Pro: $0.435 / 1M input, $0.87 / 1M output.
- Best budget pick: flash for `default` + `test`, pro for `heavy`.

### Local / free options
`ollama` and `lmstudio` need no key at all:
```json
"llm": { "provider": "ollama", "models": { "default": "qwen3:32b", "heavy": "qwen3:32b" } }
```
Start the server (`ollama serve` / LM Studio's local server tab) and play for free.
Expect weaker instruction-following than the hosted models — if narration quality
drops, raise `temperature` slightly or use a bigger local model.

### Google Gemini (unchanged)
```json
"llm": { "provider": "gemini",
         "models": { "default": "gemini-2.5-flash", "heavy": "gemini-2.5-pro", "test": "gemini-2.5-flash-lite" } }
```
Uses the `google-genai` SDK rather than the OpenAI format, but the Keeper doesn't care.

---

## 3. "Do I still need Google Cloud at all?"

No. There were always two separate Google pieces:

| Piece | Needed? | Replaceable? |
|---|---|---|
| Gemini API (narration) | Only if `provider: gemini` | Yes — any provider above |
| Google Docs API (chronicle log) | No — optional | Set `"google_docs": {"enabled": false}` and the engine just skips it |

Running Kimi + no Docs = zero Google dependencies. Your campaign log then lives only
in `saves/<scenario>/world-state.json` (which is the authoritative save either way).

## 4. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `RuntimeError: No API key found` | Key missing or still the `PASTE_...` placeholder; check the key field name matches the provider table |
| 401 Unauthorized | Wrong platform (`kimi` vs `kimi-cn` keys are separate), or key deleted |
| 404 model not found | Model ID typo or retired model — check the provider's current model list |
| `openai` import error | `pip install openai` |
| Model rejects a parameter | The client auto-retries once without `temperature`/JSON mode; if it persists, the system prompt already orders raw JSON, so parsing still succeeds |
| Weird/empty narration on local models | Local model too small for the system prompt's rules; try a bigger one or lower `max_output_tokens` |
| `finish_reason='length'` / empty response | Token budget exhausted — reasoning models spend `max_tokens` on hidden thinking before writing. Raise `llm.max_output_tokens` (8192+); the client already retries at 2x and 4x automatically |
| `finish_reason='content_filter'` | The provider's moderation blocked the scene's content. Soften the system prompt's horror vocabulary, or switch provider (`deepseek` is the usual escape hatch) |
| `LLM failed after 3 attempts` | All retries exhausted; raw responses are in `logs/llm_raw_*.txt`. The game session itself survives — just re-enter your actions |
