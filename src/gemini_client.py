"""Gemini API client for the CoC 7e LLM Keeper.

v2.2 — migrated from the deprecated `google-generativeai` SDK to the
current `google-genai` SDK, and from the retired gemini-1.5-* models
(shut down 2025-09-29) to the stable 2.5 series.

The genai package is imported lazily so that --mock offline testing works
on machines that haven't installed the dependencies yet.
"""
import json
import os
import re
import time


class GeminiClient:
    def __init__(
        self,
        api_key: str,
        default_model: str = "gemini-2.5-flash",
        heavy_model: str = "gemini-2.5-pro",
        temperature: float = 0.7,
        max_output_tokens: int = 4096,
    ):
        from google import genai  # lazy import: keeps --mock mode dependency-free

        self._genai = genai
        self.client = genai.Client(api_key=api_key)
        self.default_model = default_model
        self.heavy_model = heavy_model
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens

    def query(self, system_prompt: str, user_prompt: str, use_heavy: bool = False,
              timing: dict = None, context: dict = None, budget: int = None) -> dict:
        from google.genai import types
        from src import latency as _lat

        model_name = self.heavy_model if use_heavy else self.default_model
        max_tokens = budget or self.max_output_tokens
        t_api = time.perf_counter()
        try:
            response = self.client.models.generate_content(
                model=model_name,
                contents=user_prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    temperature=self.temperature,
                    max_output_tokens=max_tokens,
                    response_mime_type="application/json",
                ),
            )
        except Exception as e:
            self._record(model_name, use_heavy, system_prompt, user_prompt,
                         time.perf_counter() - t_api, ok=False,
                         error=str(e)[:200], stage="api-error", context=context)
            raise
        api_wait = time.perf_counter() - t_api
        text = (response.text or "").strip()
        t_parse = time.perf_counter()
        try:
            parsed = self._parse_json(text)
        except ValueError as e:
            parse_time = time.perf_counter() - t_parse
            self._record(model_name, use_heavy, system_prompt, user_prompt,
                         api_wait + parse_time, ok=False, error="invalid-json",
                         stage="invalid-json", api_wait=api_wait,
                         parse_s=parse_time, response_chars=len(text),
                         context=context)
            raise
        parse_time = time.perf_counter() - t_parse
        self._record(model_name, use_heavy, system_prompt, user_prompt,
                     api_wait + parse_time, ok=True, stage="ok",
                     api_wait=api_wait, parse_s=parse_time,
                     response_chars=len(text), context=context)
        if isinstance(timing, dict):
            timing["api_wait"] = api_wait
            timing["parse"] = parse_time
        return parsed

    @staticmethod
    def _record(model, use_heavy, system_prompt, user_prompt, seconds, ok,
                error=None, stage=None, api_wait=None, parse_s=None,
                response_chars=0, context=None):
        """Versioned timing row, same schema as the OpenAI-compat path."""
        from datetime import datetime, timezone
        from src import latency as _lat
        rec = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "version": _lat.project_version(),
            "commit": _lat.git_commit(),
            "provider": "gemini", "model": model,
            "tier": "heavy" if use_heavy else "default",
            "attempt": "initial", "retry": 0,
            "stage": stage or ("ok" if ok else "error"),
            "prompt_hash": _lat.prompt_fingerprint(system_prompt, user_prompt),
            "prompt_chars": len(system_prompt) + len(user_prompt),
            "response_chars": response_chars,
            "seconds": round(seconds, 2), "ok": ok,
        }
        if api_wait is not None:
            rec["api_wait"] = round(api_wait, 3)
        if parse_s is not None:
            rec["parse_s"] = round(parse_s, 4)
        if error:
            rec["error"] = error
        if context:
            for k in ("resolution_mode", "turn", "scenario", "source",
                      "bench", "prompt_build", "base_budget"):
                if k in context:
                    rec[k] = context[k]
        _lat.write_timing_row(os.path.join("logs", "llm_timing.jsonl"), rec)

    @staticmethod
    def _parse_json(text: str) -> dict:
        """Delegates to the shared repair-capable parser in llm_client
        (raw -> fenced -> brace span -> sanitize)."""
        from src.llm_client import _parse_json_text
        return _parse_json_text(text)

    def estimate_cost(self, input_tokens: int, output_tokens: int, use_heavy: bool = False) -> float:
        """Rough USD estimate at standard-tier rates (prompts <=200K tokens).

        gemini-2.5-pro:   $1.25 / 1M input, $10.00 / 1M output
        gemini-2.5-flash: $0.30 / 1M input, $2.50  / 1M output
        """
        if use_heavy:
            return (input_tokens * 1.25 + output_tokens * 10.00) / 1_000_000
        return (input_tokens * 0.30 + output_tokens * 2.50) / 1_000_000
