# backend/llm.py — bounded Responses API calls with validated structured outputs.
import asyncio
import json
import random

from openai import AsyncOpenAI, APIConnectionError, APITimeoutError, AuthenticationError, PermissionDeniedError, NotFoundError, BadRequestError, InternalServerError, RateLimitError
from pydantic import ValidationError

from backend.config import settings


class BudgetExceeded(RuntimeError):
    """Stop further model work once the configured session allowance is exhausted."""


class ModelUnavailable(RuntimeError):
    """Carry an actionable, sanitized configuration error to the workspace."""


class ModelClient:
    """Use separate schemas and prompts without granting the model executable code access."""

    def __init__(self, session, store):
        # Disable SDK retries so all retries share our call and token accounting.
        self.session, self.store = session, store
        self.unavailable_reason = None
        if not settings.openai_api_key:
            raise ModelUnavailable("Set OPENAI_API_KEY in the project's .env file and restart the backend; system API keys are ignored.")
        self.client = AsyncOpenAI(api_key=settings.openai_api_key, timeout=settings.api_timeout_seconds, max_retries=0)

    async def close(self):
        # Release the underlying HTTP pool after each planning or execution operation.
        await self.client.close()

    def unavailable(self, message):
        # Do not send another synthesis request after credentials, quota, or retries have failed.
        self.unavailable_reason = message
        return ModelUnavailable(message)

    @staticmethod
    def output_limit(phase):
        # Short action choices do not need the same output allowance as a cited report.
        return {"Planning": 1400, "Browser action": 700, "Extraction": 2200, "Report": 3000}[phase]

    @classmethod
    def allowance(cls, schema, prompt, payload, phase):
        # UTF-8 bytes conservatively bound input tokens without adding a tokenizer dependency.
        content = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        schema_text = json.dumps(schema.model_json_schema(), ensure_ascii=False, separators=(",", ":"))
        return len((prompt + content + schema_text).encode("utf-8")) + cls.output_limit(phase) + 512

    def can_afford(self, schema, prompt, payload, phase, report_reserve=0):
        # Browsing and extraction must leave one model attempt and its token allowance for synthesis.
        usage = self.session["usage"]
        reserve_call = int(phase in {"Browser action", "Extraction"})
        return (usage["calls"] + 1 + reserve_call <= settings.max_model_calls
                and usage["budget_tokens"] + self.allowance(schema, prompt, payload, phase)
                + report_reserve <= settings.max_session_tokens)

    async def ask(self, schema, prompt: str, payload: dict, phase: str, report_reserve=0):
        # Count requests before dispatch, including failures, and reserve a conservative token bound.
        content = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if self.unavailable_reason:
            raise ModelUnavailable(self.unavailable_reason)
        for attempt in range(2):
            usage = self.session["usage"]
            estimate = self.allowance(schema, prompt, payload, phase)
            if not self.can_afford(schema, prompt, payload, phase, report_reserve):
                raise BudgetExceeded("Stopped model work to preserve the session budget and final-report allowance")
            usage["calls"] += 1
            usage["budget_tokens"] += estimate
            self.store.event(self.session["id"], "model", {"message": f"{phase} request {usage['calls']}; reserving final-report capacity",
                             "phase": phase, "attempt": attempt + 1, "reserved_tokens": estimate,
                             "report_reserve": report_reserve, "calls": usage["calls"]})
            self.store.save(self.session)
            try:
                # Obtain usage before local schema parsing, including malformed/incomplete responses.
                response = await self.client.responses.create(
                    model=settings.openai_model,
                    input=[{"role": "system", "content": prompt}, {"role": "user", "content": content}],
                    text={"format": {"type": "json_schema", "name": schema.__name__,
                                     "schema": schema.model_json_schema(), "strict": True}},
                    max_output_tokens=self.output_limit(phase),
                    store=False,
                )
                if response.usage:
                    actual = response.usage.input_tokens + response.usage.output_tokens
                    usage["input_tokens"] += response.usage.input_tokens
                    usage["output_tokens"] += response.usage.output_tokens
                    usage["budget_tokens"] += actual - estimate
                    self.store.event(self.session["id"], "model", {"message": f"{phase} token usage received",
                                     "calls": usage["calls"], "input_tokens": response.usage.input_tokens,
                                     "output_tokens": response.usage.output_tokens, "response_status": response.status})
                if response.status != "completed" or not response.output_text:
                    raise ValueError("The model refused or returned an incomplete structured response")
                parsed = schema.model_validate_json(response.output_text)
                self.store.event(self.session["id"], "model", {"message": f"{phase} response validated", "phase": phase, "input_tokens": response.usage.input_tokens if response.usage else None, "output_tokens": response.usage.output_tokens if response.usage else None, "calls": usage["calls"], "budget_tokens": usage["budget_tokens"]})
                self.store.save(self.session)
                return parsed
            except (AuthenticationError, PermissionDeniedError) as exc:
                raise self.unavailable("OpenAI rejected the server API credentials; check OPENAI_API_KEY and project permissions.") from exc
            except (NotFoundError, BadRequestError) as exc:
                raise self.unavailable("The configured OpenAI model or structured-output request is unavailable; check OPENAI_MODEL and model access.") from exc
            except (RateLimitError, APIConnectionError, APITimeoutError, InternalServerError, ValidationError, ValueError) as exc:
                # Billing exhaustion cannot recover through retries, unlike a temporary rate limit.
                if isinstance(exc, RateLimitError) and (exc.code in {"insufficient_quota", "credit_balance_exhausted"} or exc.type == "insufficient_quota"):
                    self.store.event(self.session["id"], "error", {"message": "OpenAI API credit balance exhausted"})
                    raise self.unavailable("OpenAI API credits are exhausted; add API billing credits or configure a funded project key, then start a new plan.") from exc
                # Keep unknown failed-call usage reserved, and retry invalid JSON with the same schema.
                self.store.event(self.session["id"], "retry", {"message": f"{phase}: {type(exc).__name__}; attempt {attempt + 1}/2"})
                self.store.save(self.session)
                if attempt == 1:
                    raise self.unavailable(f"{phase} could not complete after two attempts ({type(exc).__name__}); collected evidence is preserved.") from exc
                await asyncio.sleep(min(2 ** attempt + random.random(), 5))
            finally:
                # Persist reported usage even when structured parsing fails or a request is cancelled.
                self.store.save(self.session)
