"""Provider-agnostic LLM client for the CoC 7e Keeper.

v2.3 - the Keeper no longer cares which LLM narrates. Two backend families:

1. "gemini"        -> Google AI Studio, via the google-genai SDK (gemini_client.py)
2. "openai_compat" -> any service speaking the OpenAI chat-completions wire
                      format. One class covers all of them; they differ only
                      in base_url, API key, and model names.

Verified OpenAI-compatible services (July 2026):
  kimi / kimi-cn   Moonshot AI  https://api.moonshot.ai/v1  (China: .cn/v1)
                   Models: kimi-k2.6 (256K general), kimi-k3 (1M flagship),
                   kimi-k2.7-code(-highspeed). JSON Mode supported.
  deepseek         https://api.deepseek.com  - deepseek-v4-flash / -v4-pro,
                   1M context, JSON Output supported. (deepseek-chat /
                   deepseek-reasoner aliases die 2026-07-24 - do not use them.)
  openai           SDK default base URL (https://api.openai.com/v1)
  openrouter       https://openrouter.ai/api/v1  (one key, many models)
  groq             https://api.groq.com/openai/v1
  together         https://api.together.xyz/v1
  xai              https://api.x.ai/v1
  ollama           http://localhost:11434/v1  (local, free, no key needed)
  lmstudio         http://localhost:1234/v1   (local, free, no key needed)
  custom           set llm.base_url yourself
  human            v2.8.1.5 — a human host narrates from engine packets
                   (no API, no key, no tokens; see src/human_keeper.py)

API keys resolve from config/api-key.json first, then environment variables.
"""
import json
import os
import re
import sys
import threading
import time
from datetime import datetime, timezone

PROVIDERS = {
    "gemini": {"type": "gemini"},
    "kimi": {
        "type": "openai_compat", "base_url": "https://api.moonshot.ai/v1",
        "key_fields": ["kimi_api_key", "moonshot_api_key"], "env": ["MOONSHOT_API_KEY"],
        "models": {"default": "kimi-k2.6", "heavy": "kimi-k3", "test": "kimi-k2.6"},
    },
    "kimi-cn": {
        "type": "openai_compat", "base_url": "https://api.moonshot.cn/v1",
        "key_fields": ["kimi_api_key", "moonshot_api_key"], "env": ["MOONSHOT_API_KEY"],
        "models": {"default": "kimi-k2.6", "heavy": "kimi-k3", "test": "kimi-k2.6"},
    },
    "deepseek": {
        "type": "openai_compat", "base_url": "https://api.deepseek.com",
        "key_fields": ["deepseek_api_key"], "env": ["DEEPSEEK_API_KEY"],
        "models": {"default": "deepseek-v4-flash", "heavy": "deepseek-v4-pro",
                   "test": "deepseek-v4-flash"},
    },
    "openai": {
        "type": "openai_compat", "base_url": None,
        "key_fields": ["openai_api_key"], "env": ["OPENAI_API_KEY"],
        "models": {"default": "gpt-4.1-mini", "heavy": "gpt-4.1", "test": "gpt-4.1-mini"},
    },
    "openrouter": {
        "type": "openai_compat", "base_url": "https://openrouter.ai/api/v1",
        "key_fields": ["openrouter_api_key"], "env": ["OPENROUTER_API_KEY"],
        "models": {"default": "moonshotai/kimi-k2", "heavy": "moonshotai/kimi-k2",
                   "test": "moonshotai/kimi-k2"},
    },
    "groq": {
        "type": "openai_compat", "base_url": "https://api.groq.com/openai/v1",
        "key_fields": ["groq_api_key"], "env": ["GROQ_API_KEY"],
        "models": {"default": "llama-3.3-70b-versatile", "heavy": "llama-3.3-70b-versatile",
                   "test": "llama-3.1-8b-instant"},
    },
    "together": {
        "type": "openai_compat", "base_url": "https://api.together.xyz/v1",
        "key_fields": ["together_api_key"], "env": ["TOGETHER_API_KEY"],
        "models": {"default": "moonshotai/Kimi-K2-Instruct",
                   "heavy": "moonshotai/Kimi-K2-Instruct",
                   "test": "moonshotai/Kimi-K2-Instruct"},
    },
    "xai": {
        "type": "openai_compat", "base_url": "https://api.x.ai/v1",
        "key_fields": ["xai_api_key"], "env": ["XAI_API_KEY"],
        "models": {"default": "grok-4-fast", "heavy": "grok-4", "test": "grok-4-fast"},
    },
    "ollama": {
        "type": "openai_compat", "base_url": "http://localhost:11434/v1",
        "key_fields": [], "env": [], "key_placeholder": "ollama",
        "models": {"default": "qwen3:32b", "heavy": "qwen3:32b", "test": "qwen3:8b"},
    },
    "lmstudio": {
        "type": "openai_compat", "base_url": "http://localhost:1234/v1",
        "key_fields": [], "env": [], "key_placeholder": "lm-studio",
        "models": {"default": "local-model", "heavy": "local-model", "test": "local-model"},
    },
    "custom": {
        "type": "openai_compat", "base_url": None,
        "key_fields": ["custom_api_key"], "env": [],
        "models": {"default": "default", "heavy": "default", "test": "default"},
    },
}


# ---------------------------------------------------------------------------
# Tolerant JSON extraction & repair
#
# LLMs routinely emit *almost*-JSON: raw newlines/tabs inside string values,
# trailing commas, Python literals (True/False/None), markdown fences, prose
# around the object. Pipeline: raw -> fenced block -> brace span -> sanitize
# (escape control chars in strings, strip trailing commas, fix Python
# literals) -> brace span again. On total failure the raw text is dumped to
# logs/llm_raw_response.txt so the failure is diagnosable.
# ---------------------------------------------------------------------------

def _escape_control_chars_in_strings(s: str) -> str:
    out, in_str, esc = [], False, False
    for ch in s:
        if esc:
            out.append(ch)
            esc = False
            continue
        if ch == "\\" and in_str:
            out.append(ch)
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
            out.append(ch)
            continue
        if in_str and ch == "\n":
            out.append("\\n")
            continue
        if in_str and ch == "\r":
            continue
        if in_str and ch == "\t":
            out.append("\\t")
            continue
        out.append(ch)
    return "".join(out)


def _replace_python_literals(s: str) -> str:
    """True/False/None -> true/false/null, but only OUTSIDE string values."""
    out, in_str, esc, i = [], False, False, 0
    while i < len(s):
        ch = s[i]
        if esc:
            out.append(ch)
            esc = False
            i += 1
            continue
        if ch == "\\" and in_str:
            out.append(ch)
            esc = True
            i += 1
            continue
        if ch == '"':
            in_str = not in_str
            out.append(ch)
            i += 1
            continue
        if not in_str:
            hit = False
            for lit, rep in (("True", "true"), ("False", "false"), ("None", "null")):
                end = i + len(lit)
                if (s.startswith(lit, i)
                        and (i == 0 or not (s[i - 1].isalnum() or s[i - 1] == "_"))
                        and (end >= len(s) or not (s[end].isalnum() or s[end] == "_"))):
                    out.append(rep)
                    i = end
                    hit = True
                    break
            if hit:
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def _sanitize_json(s: str) -> str:
    s = _escape_control_chars_in_strings(s)
    s = re.sub(r",\s*([}\]])", r"\1", s)   # trailing commas
    s = _replace_python_literals(s)
    return s


def _brace_span(text: str) -> str | None:
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        return text[start:end + 1]
    return None


def _balanced_spans(text: str):
    """Yield every top-level balanced {...} span, string-aware.

    The old parser took first-'{' to last-'}', which breaks whenever the model
    wraps the JSON in prose that itself contains braces (reasoning, markdown,
    examples). Scanning balanced spans lets us try each candidate object and
    keep the first one that actually parses — extraction gets safer without
    weakening any downstream validation."""
    depth = 0
    start = -1
    in_str = False
    esc = False
    for i, ch in enumerate(text):
        if esc:
            esc = False
            continue
        if ch == "\\" and in_str:
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0 and start != -1:
                yield text[start:i + 1]
                start = -1


def _try_loads(s: str):
    try:
        return json.loads(s)
    except (json.JSONDecodeError, ValueError):
        return None


def _parse_json_text(text: str) -> dict:
    text = (text or "").strip()
    result = _try_loads(text)
    if result is not None:
        return result
    candidates = []
    if "```" in text:
        candidates += [b.strip() for b in
                       re.findall(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL)]
    candidates += list(_balanced_spans(text))
    span = _brace_span(text)
    if span and span not in candidates:
        candidates.append(span)
    for cand in candidates:
        result = _try_loads(cand)
        if result is not None:
            return result
    for cand in candidates:
        result = _try_loads(_sanitize_json(cand))
        if result is not None:
            return result
    raise ValueError(f"Could not parse JSON from model response ({len(text)} chars).")


def _dump_raw_response(text: str, tag: str = "") -> str:
    """Persist the offending raw response so failures are diagnosable.
    Tag-aware filenames so a later (possibly empty) retry never overwrites
    the evidence from an earlier attempt."""
    os.makedirs("logs", exist_ok=True)
    name = f"llm_raw_{tag}.txt" if tag else "llm_raw_response.txt"
    path = os.path.join("logs", name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


class _Presence:
    """Thematic 'still alive' line for long API calls (v2.6.0).

    The SDK call is blocking and non-streaming, so a 200s k3 turn looked like
    a hung terminal. While a call runs, this paints one self-erasing line —
    spinner + elapsed timer + rotating Keeper flavor. It only appears after
    `delay` seconds (instant calls stay silent), only on a real terminal
    (never when output is redirected), and it can never raise.
    """

    SPIN = "|/-\\"
    FLAVOR = (
        "The Keeper consults forbidden marginalia",
        "Something shifts behind the walls",
        "The candles gutter, then revive",
        "Rain worries at the windows",
        "Dice are rolled behind the screen",
        "The ritual clock ticks somewhere below",
        "A streetcar fades; the house leans closer",
        "The veil between scenes thins",
    )

    def __init__(self, stream, delay=1.5, interval=0.5):
        self.stream = stream
        self.delay = delay
        self.interval = interval
        self._stop = threading.Event()
        self._thread = None
        self._shown = False

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        t0 = time.perf_counter()
        frames = 0
        while not self._stop.wait(self.interval):
            elapsed = time.perf_counter() - t0
            if elapsed < self.delay:
                continue
            spin = self.SPIN[frames % len(self.SPIN)]
            flavor = self.FLAVOR[int(elapsed // 7) % len(self.FLAVOR)]
            frames += 1
            try:
                self.stream.write(f"\r  {spin} {elapsed:6.1f}s — {flavor}...   ")
                self.stream.flush()
                self._shown = True
            except Exception:
                return

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)
        if self._shown:
            try:
                self.stream.write("\r" + " " * 78 + "\r")
                self.stream.flush()
            except Exception:
                pass


class EmptyResponseError(RuntimeError):
    """The API call succeeded but message.content came back empty.
    Typical causes: finish_reason='length' (token budget exhausted — with
    reasoning models, internal thinking invisibly consumes max_tokens) or
    finish_reason='content_filter' (provider moderation blocked the scene)."""
    def __init__(self, finish_reason):
        self.finish_reason = finish_reason
        super().__init__(
            f"Model returned EMPTY content (finish_reason={finish_reason!r}).")


STRICT_JSON_SUFFIX = (
    "\n\nCRITICAL OUTPUT REQUIREMENT: Respond with RAW JSON ONLY. No markdown "
    "fences, no commentary, no text before or after the JSON object. Every "
    "double quote inside a string value must be escaped as \\\" and every "
    "newline inside a string value must be written as \\n."
)

# v2.8.1.3: the timeout recovery attempt trades richness for reliability.
TIMEOUT_RETRY_SUFFIX = (
    "\n\nTIMEOUT RECOVERY: The previous request timed out. Respond with the "
    "SAME JSON schema, but keep narration under 150 words and skip all "
    "optional fields. Raw JSON only."
)


def _is_timeout(exc: Exception) -> bool:
    name = type(exc).__name__.lower()
    return "timeout" in name or "timed out" in str(exc).lower()


class OpenAICompatClient:
    """One client for every OpenAI-compatible service (Kimi, DeepSeek, ...)."""

    def __init__(self, provider, api_key, base_url, models,
                 temperature=0.7, max_output_tokens=4096, debug=False,
                 loading=True, extra_body=None, max_output_tokens_heavy=None,
                 pricing=None, config_fingerprint=None, call_timeout=180,
                 disable_thinking=False):
        from openai import OpenAI  # lazy: --mock stays dependency-free

        kwargs = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        # v2.8.1.6: the Governor owns retry policy now. The SDK's built-in
        # retries multiplied the timeout — the field log's '180s timeout'
        # stalled 542s (3 x 180). max_retries=0 makes one deadline one
        # deadline; cancellation is enforced by run_with_deadline +
        # abort_in_flight, not by hoping the request comes home.
        self._client_kwargs = dict(kwargs)
        self._client = OpenAI(max_retries=0, **kwargs)
        self.provider = provider
        self.default_model = models.get("default", "default")
        self.heavy_model = models.get("heavy", self.default_model)
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens
        # v2.7.0: heavy tier may start the ladder at its own base budget.
        # Field data: k3's initial 4096 call burned the budget on hidden
        # reasoning and emitted 140 chars of invalid JSON after 174.6s; the
        # 8192 strict-retry succeeded. Starting heavy turns at 8192 skips a
        # paid, known-too-short first attempt.
        self.max_output_tokens_heavy = max_output_tokens_heavy
        # v2.7.0: provider-specific switches (e.g. reasoning effort) with no
        # code change — merged into every chat.completions.create call.
        self.extra_body = dict(extra_body) if extra_body else None
        # v2.8.1.x: kimi instant mode for the DEFAULT (routine-turn) model.
        # Field benchmark: k2.6's default thinking burned all 5120 completion
        # tokens on hidden reasoning and returned 132 chars of truncated
        # JSON (109.7s, FAIL); the 10240 strict-retry then spent ~5k tokens
        # reasoning for ~600 tokens of content. Disabling thinking removes
        # the whole class of budget-starved truncation for routine turns.
        # Scoped to kimi providers + the default model only: k3 does not
        # accept a thinking parameter at all, and non-kimi providers must
        # never see it. (Temperature is handled separately in _call: both
        # kimi models pin it provider-side and 400 on any other value.)
        self.disable_thinking = bool(disable_thinking)
        self.debug = debug
        self.loading = loading
        # v2.8.0: versioned, cost-aware timing rows. pricing comes from
        # config/settings.json -> pricing; config_fingerprint lets us tell
        # which knob set produced a row.
        self.pricing = dict(pricing) if pricing else {}
        self.config_fingerprint = config_fingerprint
        # v2.8.1.3: per-call read timeout + consecutive-timeout circuit
        # breaker. The 576-second spinner hang is dead.
        self.call_timeout = call_timeout
        self._consecutive_timeouts = 0
        # Every attempt is always appended here (one JSON object per line);
        # analyze with `python test_latency.py --report`. self.debug only
        # controls the live console echo. Override the path in tests.
        self.timing_log = os.path.join("logs", "llm_timing.jsonl")

    def describe(self):
        return f"{self.provider}/{self.default_model} (heavy: {self.heavy_model})"

    def abort_in_flight(self):
        """v2.8.1.6: close the HTTP session so an abandoned request actually
        dies, then rebuild it for the next call. Called by the Governor path
        after a deadline abandonment."""
        try:
            self._client.close()
        except Exception:
            pass
        try:
            from openai import OpenAI
            self._client = OpenAI(max_retries=0, **self._client_kwargs)
        except Exception:
            pass

    def _record_timing(self, model, use_heavy, attempt, budget, system_prompt,
                       user_prompt, seconds, ok, error=None, response_chars=0,
                       usage=None, stage=None, retry=0, api_wait=None,
                       parse_s=None, finish=None, context=None):
        """One timing record per attempt — the data behind 'why was that turn
        slow?' and, since v2.7.5, 'what does this game COST?'.

        v2.8.0 versioning: every row now stamps the project version, git
        commit (when the tree is a checkout), prompt/config fingerprints, the
        resolution mode and caller source (via `context`), the pipeline stage
        reached, and the retry index, so historical rows can be separated by
        version instead of being blended across releases. Token usage
        (pt/ct/cached) comes from the provider's own meter; cost is priced
        from config/settings.json -> pricing. Never raises: diagnostics must
        not break a session."""
        from src import latency as _lat
        usage = usage or {}
        rec = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "version": _lat.project_version(),
            "commit": _lat.git_commit(),
            "provider": self.provider, "model": model,
            "tier": "heavy" if use_heavy else "default",
            "attempt": attempt, "retry": retry,
            "stage": stage or ("ok" if ok else "error"),
            "budget": budget,
            "prompt_hash": _lat.prompt_fingerprint(system_prompt, user_prompt),
            "config_hash": self.config_fingerprint,
            "prompt_chars": len(system_prompt) + len(user_prompt),
            "response_chars": response_chars,
            "seconds": round(seconds, 2), "ok": ok,
        }
        if api_wait is not None:
            rec["api_wait"] = round(api_wait, 3)
        if parse_s is not None:
            rec["parse_s"] = round(parse_s, 4)
        if finish:
            rec["finish"] = finish
        if error:
            rec["error"] = error
        if usage:
            rec.update(usage)   # pt / ct / cached — the cost meter (v2.7.5)
            cost = _lat.estimate_cost(model, usage.get("pt", 0),
                                      usage.get("ct", 0), usage.get("cached", 0),
                                      self.pricing)
            if cost is not None:
                rec["cost"] = round(cost, 6)
        if context:
            for k in ("resolution_mode", "turn", "scenario", "source",
                      "bench", "prompt_build", "base_budget", "prompt_tier",
                      "dynamic_prompt_chars", "system_prompt_chars",
                      "total_prompt_chars"):
                if k in context:
                    rec[k] = context[k]
        _lat.write_timing_row(self.timing_log, rec)
        if self.debug:
            status = "ok" if ok else f"FAIL ({error})"
            line = (f"[llm {seconds:6.1f}s] {model} {attempt} budget={budget} "
                    f"prompt={rec['prompt_chars']}ch resp={response_chars}ch -> {status}")
            if usage and "pt" in usage and "ct" in usage:
                line += f" tok={usage['pt']}+{usage['ct']}"
            print(line)

    def _call(self, model, system_prompt, user_prompt, json_mode, with_temperature,
              max_tokens=None, use_extra_body=True):
        kwargs = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": max_tokens or self.max_output_tokens,
            "timeout": self.call_timeout,
        }
        instant = (self.disable_thinking
                   and self.provider.startswith("kimi")
                   and model == self.default_model)
        # Live-verified 2026-07-26 against the production key: kimi-k2.6 AND
        # kimi-k3 reject any temperature but the pinned one — HTTP 400
        # "invalid temperature: only 1 is allowed for this model". Until now
        # the _generate ladder swallowed that 400 and silently retried
        # WITHOUT json_mode — a hidden cause of invalid-JSON turns. Never
        # send temperature to kimi; non-kimi providers are unaffected.
        if with_temperature and not self.provider.startswith("kimi"):
            kwargs["temperature"] = self.temperature
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if use_extra_body and self.extra_body:
            kwargs["extra_body"] = self.extra_body
        if instant:
            # Merge, never replace: config extra_body keys survive.
            body = dict(kwargs.get("extra_body") or {})
            body["thinking"] = {"type": "disabled"}
            kwargs["extra_body"] = body
        return self._client.chat.completions.create(**kwargs)

    def _generate(self, model, system_prompt, user_prompt, max_tokens=None):
        """One API call, degrading gracefully if the model rejects
        temperature, response_format, or provider-specific extra_body keys.
        Unsupported parameters must no-op, never crash the turn.
        Returns (text, usage, api_wait_seconds, finish_reason); raises
        EmptyResponseError if the content comes back empty."""
        combos = [(True, True, True), (False, False, True), (False, False, False)]
        resp = None
        api_wait = 0.0
        for i, (jm, wt, xb) in enumerate(combos):
            t_api = time.perf_counter()
            try:
                resp = self._call(model, system_prompt, user_prompt,
                                  json_mode=jm, with_temperature=wt,
                                  max_tokens=max_tokens, use_extra_body=xb)
                api_wait += time.perf_counter() - t_api
                break
            except Exception as e:
                api_wait += time.perf_counter() - t_api
                msg = str(e).lower()
                param_err = any(k in msg for k in
                                ("temperature", "response_format", "json",
                                 "parameter", "param", "unsupported",
                                 "unrecognized"))
                if not param_err or i + 1 >= len(combos):
                    raise
        choice = resp.choices[0] if getattr(resp, "choices", None) else None
        text = (choice.message.content or "") if choice else ""
        finish = getattr(choice, "finish_reason", None) if choice else None
        if not text.strip():
            raise EmptyResponseError(finish)
        return text, self._usage_of(resp), api_wait, finish

    @staticmethod
    def _usage_of(resp) -> dict:
        """The provider's meter, flattened (v2.7.5): pt = prompt tokens,
        ct = completion tokens, cached = cache-hit prompt tokens when the
        provider reports them. Missing fields are simply absent."""
        usage = getattr(resp, "usage", None)
        if usage is None:
            return {}
        out = {}
        pt = getattr(usage, "prompt_tokens", None)
        ct = getattr(usage, "completion_tokens", None)
        cached = getattr(usage, "cached_tokens", None)
        if cached is None:
            details = getattr(usage, "prompt_tokens_details", None)
            cached = getattr(details, "cached_tokens", None) if details is not None else None
        if isinstance(pt, int):
            out["pt"] = pt
        if isinstance(ct, int):
            out["ct"] = ct
        if isinstance(cached, int):
            out["cached"] = cached
        return out

    def query(self, system_prompt, user_prompt, use_heavy=False, timing=None,
              context=None, budget=None, plan=None, compact_prompt=None):
        """Three-strike query: initial -> strict+json retry at 2x tokens ->
        final retry at 4x tokens. Empty responses and unparsable JSON both
        escalate; raw failures are preserved in logs/ per attempt.

        When `plan` (a Latency Governor CallPlan, v2.8.1.6) is given, the
        legacy ladder is bypassed and the Governor owns budget, deadline,
        and retry policy — see _query_governed.

        `budget` overrides the ladder's base token budget (A/B benchmarks);
        the 1x/2x/4x escalation shape is preserved.
        `context` is an optional dict of caller metadata (resolution_mode,
        turn, scenario, source, prompt_build) stamped onto each timing row.
        If `timing` is a dict, it is populated with:
          api_wait  seconds spent waiting on the provider across all attempts
          parse     seconds spent parsing the final successful response
        """
        if plan is not None:
            return self._query_governed(system_prompt, user_prompt, plan,
                                        compact_prompt=compact_prompt,
                                        timing=timing, context=context)
        model = self.heavy_model if use_heavy else self.default_model
        base = budget or self.max_output_tokens
        if budget is None and use_heavy and self.max_output_tokens_heavy:
            base = self.max_output_tokens_heavy
        attempts = [
            ("initial", user_prompt, base),
            ("strict-retry", user_prompt + STRICT_JSON_SUFFIX, base * 2),
            ("final-retry", user_prompt + STRICT_JSON_SUFFIX, base * 4),
        ]
        last_err = None
        animate = self.loading and sys.stdout.isatty()
        api_wait_total = 0.0
        for retry, (tag, prompt, tok_budget) in enumerate(attempts):
            presence = _Presence(sys.stdout) if animate else None
            t0 = time.perf_counter()
            try:
                if presence:
                    presence.start()
                text, usage, api_wait, finish = self._generate(
                    model, system_prompt, prompt, max_tokens=tok_budget)
            except EmptyResponseError as e:
                self._record_timing(model, use_heavy, tag, tok_budget, system_prompt,
                                    prompt, time.perf_counter() - t0, ok=False,
                                    error=f"empty (finish_reason={e.finish_reason})",
                                    stage="empty", retry=retry,
                                    finish=e.finish_reason, context=context)
                last_err = e
                print(f"[LLM returned EMPTY content (finish_reason={e.finish_reason}). "
                      f"Retrying with a larger token budget...]")
                continue
            except Exception as e:
                if not _is_timeout(e):
                    raise
                # v2.8.1.3 circuit breaker: log the timeout, retry ONCE with a
                # compact short-narration request, then preserve the turn.
                self._record_timing(model, use_heavy, tag, tok_budget, system_prompt,
                                    prompt, time.perf_counter() - t0, ok=False,
                                    error="timeout", stage="timeout", retry=retry,
                                    context=context)
                self._consecutive_timeouts += 1
                if self._consecutive_timeouts >= 2:
                    raise RuntimeError(
                        "LLM timed out twice in a row (circuit breaker open). "
                        "Check connectivity or provider status; the turn was "
                        "preserved — re-declare when ready.") from e
                print(f"[LLM timed out after {self.call_timeout}s. "
                      f"One compact retry...]")
                try:
                    text, usage, api_wait, finish = self._generate(
                        model, system_prompt, prompt + TIMEOUT_RETRY_SUFFIX,
                        max_tokens=tok_budget)
                except Exception as e2:
                    self._record_timing(model, use_heavy, tag + "-retry", tok_budget,
                                        system_prompt, prompt,
                                        time.perf_counter() - t0, ok=False,
                                        error="timeout", stage="timeout",
                                        retry=retry, context=context)
                    raise RuntimeError(
                        "LLM timed out and the compact retry failed. The turn "
                        "was preserved — re-declare when ready.") from e2
                self._record_timing(model, use_heavy, tag + "-timeout-recovery",
                                    tok_budget, system_prompt, prompt,
                                    time.perf_counter() - t0, ok=True,
                                    response_chars=len(text), usage=usage,
                                    stage="ok", retry=retry,
                                    api_wait=api_wait, finish=finish,
                                    context=context)
            finally:
                if presence:
                    presence.stop()
            elapsed = time.perf_counter() - t0
            api_wait_total += api_wait
            t_parse = time.perf_counter()
            try:
                parsed = _parse_json_text(text)
            except ValueError as e:
                parse_time = time.perf_counter() - t_parse
                self._record_timing(model, use_heavy, tag, tok_budget, system_prompt,
                                    prompt, elapsed, ok=False,
                                    error="invalid-json", response_chars=len(text),
                                    usage=usage, stage="invalid-json", retry=retry,
                                    api_wait=api_wait, parse_s=parse_time,
                                    finish=finish, context=context)
                last_err = e
                dump = _dump_raw_response(text, tag)
                print(f"[LLM returned invalid JSON; raw response saved to {dump}. Retrying...]")
                continue
            parse_time = time.perf_counter() - t_parse
            self._record_timing(model, use_heavy, tag, tok_budget, system_prompt,
                                prompt, elapsed, ok=True, response_chars=len(text),
                                usage=usage, stage="ok", retry=retry,
                                api_wait=api_wait, parse_s=parse_time,
                                finish=finish, context=context)
            self._consecutive_timeouts = 0
            if isinstance(timing, dict):
                timing["api_wait"] = api_wait_total
                timing["parse"] = parse_time
            return parsed
        raise RuntimeError(
            f"LLM failed after {len(attempts)} attempts. Last error: {last_err}\n"
            f"How to read this: finish_reason='length' -> raise llm.max_output_tokens "
            f"in config/settings.json (reasoning models burn budget on hidden thinking). "
            f"finish_reason='content_filter' -> the provider's moderation blocked the "
            f"scene; soften the prompt or switch provider. Raw responses are in logs/.")

    def _query_governed(self, system_prompt, user_prompt, plan,
                        compact_prompt=None, timing=None, context=None):
        """The v2.8.1.6 governed path — the Latency Governor's CallPlan owns
        budget, deadline, and retry policy.

        - Every attempt runs under run_with_deadline: at the deadline the
          wait is abandoned and the HTTP session is closed (abort_in_flight),
          so a 120s timeout means 120 seconds — not the field log's 542s.
        - A timeout earns exactly ONE compact retry with a materially smaller
          prompt (compact_prompt), never the original. If that also fails,
          GovernorDegraded is raised so the Keeper can offer the
          degraded-mode menu with the turn preserved.
        - Invalid JSON still earns strict-suffix retries (plan.json_retries)
          — those are cheap reparses, not latency events.
        """
        from src.latency_governor import (GovernorDegraded, GovernorTimeout,
                                          run_with_deadline)
        use_heavy = plan.model_tier == "heavy"
        model = self.heavy_model if use_heavy else self.default_model
        context = dict(context or {})
        context["prompt_tier"] = plan.prompt_tier
        animate = self.loading and sys.stdout.isatty()
        api_wait_total = 0.0
        last_err = None
        attempts = [(getattr(plan, "attempt_label", None) or "initial",
                     user_prompt, plan.budget)]
        for r in range(1, plan.json_retries + 1):
            attempts.append((f"strict-retry-{r}", user_prompt + STRICT_JSON_SUFFIX,
                             plan.budget * (2 ** r)))
        for retry, (tag, prompt, budget) in enumerate(attempts):
            presence = _Presence(sys.stdout) if animate else None
            t0 = time.perf_counter()

            def _attempt():
                # Provider-side timeouts (SDK read timeouts) are normalized
                # to the Governor's own timeout type so both deadline kinds
                # share one recovery path.
                try:
                    return self._generate(model, system_prompt, prompt,
                                          max_tokens=budget)
                except Exception as e:
                    if _is_timeout(e):
                        raise GovernorTimeout(str(e)) from e
                    raise

            try:
                if presence:
                    presence.start()
                text, usage, api_wait, finish = run_with_deadline(
                    _attempt, plan.timeout)
            except GovernorTimeout as e:
                if presence:
                    presence.stop()
                self.abort_in_flight()
                self._record_timing(model, use_heavy, tag, budget, system_prompt,
                                    prompt, time.perf_counter() - t0, ok=False,
                                    error="timeout", stage="timeout", retry=retry,
                                    context=context)
                if not plan.allow_compact_retry:
                    # v2.8.1.7 P0-2: the compact attempt itself gets exactly
                    # one shot — no unbounded recovery loop.
                    raise GovernorDegraded(
                        "compact retry attempt timed out") from e
                # exactly one compact retry — never the original prompt, and
                # never the 11k system prompt (v2.8.1.6 benchmark: the system
                # prompt was 90% of the "compact" call).
                from src.latency_governor import COMPACT_SYSTEM_PROMPT
                cprompt = compact_prompt or user_prompt[:2000]
                print(f"[LLM timed out after {plan.timeout}s. Compact retry "
                      f"({len(system_prompt) + len(prompt)} -> "
                      f"{len(COMPACT_SYSTEM_PROMPT) + len(cprompt)} chars)...]")
                t1 = time.perf_counter()
                cctx = dict(context, prompt_tier="compact_retry")
                try:
                    text, usage, api_wait, finish = run_with_deadline(
                        lambda: self._generate(model, COMPACT_SYSTEM_PROMPT,
                                               cprompt,
                                               max_tokens=plan.compact_budget),
                        plan.compact_timeout)
                except GovernorTimeout as e2:
                    self.abort_in_flight()
                    self._record_timing(model, use_heavy, tag + "-compact-retry",
                                        plan.compact_budget, COMPACT_SYSTEM_PROMPT,
                                        cprompt, time.perf_counter() - t1,
                                        ok=False, error="timeout", stage="timeout",
                                        retry=retry, context=cctx)
                    raise GovernorDegraded(
                        "initial call and compact retry both timed out") from e2
                except EmptyResponseError as e2:
                    # Benchmark (v2.8.1.6): the compact call can also starve on
                    # hidden reasoning — that is a degraded provider, not a
                    # turn to burn.
                    self._record_timing(model, use_heavy, tag + "-compact-retry",
                                        plan.compact_budget, COMPACT_SYSTEM_PROMPT,
                                        cprompt, time.perf_counter() - t1,
                                        ok=False,
                                        error=f"empty (finish_reason={e2.finish_reason})",
                                        stage="empty", retry=retry,
                                        finish=e2.finish_reason, context=cctx)
                    raise GovernorDegraded(
                        f"compact retry returned EMPTY content "
                        f"(finish_reason={e2.finish_reason})") from e2
                api_wait_total += api_wait
                t_parse = time.perf_counter()
                try:
                    parsed = _parse_json_text(text)
                except ValueError as e2:
                    self._record_timing(model, use_heavy, tag + "-compact-retry",
                                        plan.compact_budget, COMPACT_SYSTEM_PROMPT,
                                        cprompt, time.perf_counter() - t1,
                                        ok=False, error="invalid-json",
                                        response_chars=len(text), usage=usage,
                                        stage="invalid-json", retry=retry,
                                        context=cctx)
                    raise GovernorDegraded(
                        "compact retry returned invalid JSON") from e2
                self._record_timing(model, use_heavy, tag + "-compact-retry",
                                    plan.compact_budget, COMPACT_SYSTEM_PROMPT,
                                    cprompt, time.perf_counter() - t1, ok=True,
                                    response_chars=len(text), usage=usage,
                                    stage="ok", retry=retry, api_wait=api_wait,
                                    parse_s=time.perf_counter() - t_parse,
                                    finish=finish, context=cctx)
                if isinstance(timing, dict):
                    timing["api_wait"] = api_wait_total
                    timing["parse"] = time.perf_counter() - t_parse
                return parsed
            except EmptyResponseError as e:
                self._record_timing(model, use_heavy, tag, budget, system_prompt,
                                    prompt, time.perf_counter() - t0, ok=False,
                                    error=f"empty (finish_reason={e.finish_reason})",
                                    stage="empty", retry=retry,
                                    finish=e.finish_reason, context=context)
                last_err = e
                print(f"[LLM returned EMPTY content (finish_reason={e.finish_reason}). "
                      f"Retrying with a larger token budget...]")
                continue
            finally:
                if presence:
                    presence.stop()
            elapsed = time.perf_counter() - t0
            api_wait_total += api_wait
            t_parse = time.perf_counter()
            try:
                parsed = _parse_json_text(text)
            except ValueError as e:
                parse_time = time.perf_counter() - t_parse
                self._record_timing(model, use_heavy, tag, budget, system_prompt,
                                    prompt, elapsed, ok=False,
                                    error="invalid-json", response_chars=len(text),
                                    usage=usage, stage="invalid-json", retry=retry,
                                    api_wait=api_wait, parse_s=parse_time,
                                    finish=finish, context=context)
                last_err = e
                dump = _dump_raw_response(text, tag)
                print(f"[LLM returned invalid JSON; raw response saved to {dump}. Retrying...]")
                continue
            parse_time = time.perf_counter() - t_parse
            self._record_timing(model, use_heavy, tag, budget, system_prompt,
                                prompt, elapsed, ok=True, response_chars=len(text),
                                usage=usage, stage="ok", retry=retry,
                                api_wait=api_wait, parse_s=parse_time,
                                finish=finish, context=context)
            if isinstance(timing, dict):
                timing["api_wait"] = api_wait_total
                timing["parse"] = parse_time
            return parsed
        raise RuntimeError(
            f"LLM failed after {len(attempts)} governed attempts. "
            f"Last error: {last_err}")


def _load_keys_file(keys_file):
    """Read api-key.json with a human-readable error for the classic mistakes
    (unquoted key, trailing comma, smart quotes) instead of a raw traceback."""
    if not keys_file or not os.path.exists(keys_file):
        return {}
    try:
        with open(keys_file, encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"{keys_file} is not valid JSON: {e.msg} (line {e.lineno}, column {e.colno}). "
            'Every key must be wrapped in double quotes, e.g. "kimi_api_key": "sk-...". '
            "Also check for trailing commas and curly/smart quotes from word processors."
        ) from None


def _resolve_api_key(preset, keys_file):
    """config/api-key.json first, then environment, then placeholder for local."""
    if preset.get("key_placeholder") and not preset.get("key_fields"):
        return preset["key_placeholder"]
    data = _load_keys_file(keys_file)
    for field in preset.get("key_fields", []):
        if data.get(field) and "PASTE" not in str(data[field]).upper():
            return data[field]
    for env in preset.get("env", []):
        if os.environ.get(env):
            return os.environ[env]
    raise RuntimeError(
        f"No API key found. Put one of {preset.get('key_fields')} in {keys_file} "
        f"or set env var {preset.get('env')}.")


def build_llm_client(config, mock=False):
    """Factory: returns MockKeeperClient, GeminiClient, or OpenAICompatClient."""
    if mock:
        from src.mock_keeper import MockKeeperClient
        return MockKeeperClient()

    llm = config.get("llm", {})
    provider = llm.get("provider", "gemini").lower()
    if provider == "human":
        # v2.8.1.5: a human host replaces the narration layer. No API key
        # resolution, no SDK import, no network — ever.
        from src.human_keeper import HumanKeeperClient
        return HumanKeeperClient(config=config,
                                 debug=bool(llm.get("debug", False)))
    if provider not in PROVIDERS:
        raise ValueError(f"Unknown llm.provider '{provider}'. Known: {sorted(PROVIDERS)}")
    preset = PROVIDERS[provider]

    # Model resolution: llm.models{...} wins, legacy default_model/heavy_model
    # still honored, provider preset is the floor.
    models = dict(preset.get("models", {}))
    models.update({k: v for k, v in (llm.get("models") or {}).items() if v})
    if llm.get("default_model"):
        models["default"] = llm["default_model"]
    if llm.get("heavy_model"):
        models["heavy"] = llm["heavy_model"]

    if preset["type"] == "gemini":
        from src.gemini_client import GeminiClient
        keys_file = llm.get("api_key_file", "config/api-key.json")
        data = _load_keys_file(keys_file)
        api_key = data.get("gemini_api_key") or os.environ.get("GEMINI_API_KEY")
        if not api_key or "PASTE" in str(api_key).upper():
            raise RuntimeError("No Gemini API key. Set gemini_api_key in config/api-key.json.")
        return GeminiClient(
            api_key=api_key,
            default_model=models.get("default", "gemini-2.5-flash"),
            heavy_model=models.get("heavy", "gemini-2.5-pro"),
            temperature=llm.get("temperature", 0.7),
            max_output_tokens=llm.get("max_output_tokens", 4096),
        )

    api_key = _resolve_api_key(preset, llm.get("api_key_file", "config/api-key.json"))
    base_url = llm.get("base_url") or preset.get("base_url")
    if provider == "custom" and not base_url:
        raise ValueError("provider 'custom' requires llm.base_url in settings.json")
    from src import latency as _lat
    return OpenAICompatClient(
        provider=provider, api_key=api_key, base_url=base_url, models=models,
        temperature=llm.get("temperature", 0.7),
        max_output_tokens=llm.get("max_output_tokens", 4096),
        debug=bool(llm.get("debug", False)),
        loading=bool(llm.get("loading_bar", True)),
        extra_body=llm.get("extra_body"),
        max_output_tokens_heavy=llm.get("max_output_tokens_heavy"),
        pricing=config.get("pricing"),
        config_fingerprint=_lat.config_fingerprint(llm),
        call_timeout=llm.get("call_timeout_seconds", 180),
        disable_thinking=bool(llm.get("disable_thinking", False)),
    )
