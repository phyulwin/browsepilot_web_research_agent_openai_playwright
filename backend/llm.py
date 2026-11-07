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
        self.client = AsyncOpenAI(api_key=settings.openai_api_key, timeout=settings.api_timeout_seconds, max_retries=0)

    async def close(self):
        # Release the underlying HTTP pool after each planning or execution operation.
        await self.client.close()

    async def ask(self, schema, prompt: str, payload: dict, phase: str):
        # Count requests before dispatch, including failures, and reserve a conservative token bound.
        content = json.dumps(payload, ensure_ascii=False)
        for attempt in range(3):
            usage = self.session["usage"]
            estimate = len((prompt + content + json.dumps(schema.model_json_schema())).encode("utf-8")) + 4000
            if usage["calls"] >= settings.max_model_calls or usage["budget_tokens"] + estimate > settings.max_session_tokens:
                raise BudgetExceeded("Session model budget reached; collected evidence has been preserved")
            usage["calls"] += 1
            usage["budget_tokens"] += estimate
            self.store.save(self.session)
            try:
                response = await self.client.responses.parse(
                    model=settings.openai_model,
                    input=[{"role": "system", "content": prompt}, {"role": "user", "content": content}],
                    text_format=schema,
                    max_output_tokens=3000,
                    store=False,
                )
                if response.usage:
                    actual = response.usage.input_tokens + response.usage.output_tokens
                    usage["input_tokens"] += response.usage.input_tokens
                    usage["output_tokens"] += response.usage.output_tokens
                    usage["budget_tokens"] += actual - estimate
                if response.output_parsed is None:
                    raise ValueError("The model refused or returned an incomplete structured response")
                self.store.event(self.session["id"], "model", {"message": f"{phase} response validated", "phase": phase, "input_tokens": response.usage.input_tokens if response.usage else None, "output_tokens": response.usage.output_tokens if response.usage else None})
                self.store.save(self.session)
                return response.output_parsed
            except (AuthenticationError, PermissionDeniedError) as exc:
                raise ModelUnavailable("OpenAI rejected the server API credentials; check OPENAI_API_KEY and project permissions.") from exc
            except (NotFoundError, BadRequestError) as exc:
                raise ModelUnavailable("The configured OpenAI model or structured-output request is unavailable; check OPENAI_MODEL and model access.") from exc
            except (RateLimitError, APIConnectionError, APITimeoutError, InternalServerError, ValidationError, ValueError) as exc:
                # Billing exhaustion cannot recover through retries, unlike a temporary rate limit.
                if isinstance(exc, RateLimitError) and (exc.code in {"insufficient_quota", "credit_balance_exhausted"} or exc.type == "insufficient_quota"):
                    self.store.event(self.session["id"], "error", {"message": "OpenAI API credit balance exhausted"})
                    raise ModelUnavailable("OpenAI API credits are exhausted; add API billing credits or configure a funded project key, then start a new plan.") from exc
                # Keep unknown failed-call usage reserved, and retry invalid JSON with the same schema.
                self.store.event(self.session["id"], "retry", {"message": f"{phase}: {type(exc).__name__}; attempt {attempt + 1}/3"})
                self.store.save(self.session)
                if attempt == 2:
                    raise RuntimeError(f"{phase} could not complete after three attempts ({type(exc).__name__})") from exc
                await asyncio.sleep(min(2 ** attempt + random.random(), 5))
