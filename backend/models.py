# backend/models.py — strict contracts between the model, interpreter, and UI.
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    """Reject fields outside the documented structured-output vocabulary."""

    model_config = ConfigDict(extra="forbid")


StepStatus = Literal["pending", "in_progress", "completed", "failed", "skipped"]


class PlanItem(StrictModel):
    """Describe a high-level research objective, independent of browser commands."""

    task: str = Field(min_length=5, max_length=350)
    details: str = Field(max_length=450)


class Plan(StrictModel):
    """Require a short, reviewable checklist before browser execution."""

    title: str = Field(min_length=3, max_length=120)
    objective: str = Field(max_length=500)
    steps: list[PlanItem] = Field(min_length=2, max_length=8)


class StepUpdate(StrictModel):
    """Associate progress with evidence collected in the current session."""

    step: int = Field(ge=1, le=8)
    status: StepStatus
    detail: str = Field(max_length=300)


class Action(StrictModel):
    """Constrain every model-selected browser operation and its arguments."""

    command: Literal["SEARCH", "NAVIGATE", "CLICK", "READ_PAGE", "EXTRACT", "SCROLL", "SWITCH_TAB", "NEW_TAB", "BACK", "WAIT", "DONE"]
    reason: str = Field(min_length=1, max_length=250)
    step: int = Field(ge=1, le=8)
    url: str | None
    query: str | None
    link_id: int | None
    tab_id: int | None
    direction: Literal["up", "down"] | None
    seconds: int | None = Field(ge=1, le=3)
    updates: list[StepUpdate] = Field(max_length=8)

    @model_validator(mode="after")
    def required_arguments(self):
        # Validate semantic requirements after JSON-schema parsing.
        required = {"SEARCH": "query", "NAVIGATE": "url", "CLICK": "link_id", "SWITCH_TAB": "tab_id", "SCROLL": "direction", "WAIT": "seconds"}
        field = required.get(self.command)
        if field and getattr(self, field) is None:
            raise ValueError(f"{self.command} requires {field}")
        if self.command == "SEARCH" and not (1 <= len(self.query or "") <= 400):
            raise ValueError("Search query must contain 1–400 characters")
        # Reject unused arguments so unrelated URLs or credentials cannot enter action audits.
        used = {required[self.command]} if self.command in required else set()
        if self.command == "NEW_TAB":
            used.add("url")
        for name in ("url", "query", "link_id", "tab_id", "direction", "seconds"):
            if name not in used and getattr(self, name) is not None:
                raise ValueError("Unused action arguments must be null")
        return self


class ExtractedQuote(StrictModel):
    """Capture a verbatim passage that can be checked against browser text."""

    quote: str = Field(min_length=15, max_length=600)


class Extraction(StrictModel):
    """Return a bounded set of useful passages, never invented observations."""

    passages: list[ExtractedQuote] = Field(max_length=6)
    completed_steps: list[int] = Field(max_length=8)
    goal_satisfied: bool
    missing_information: list[str] = Field(max_length=8)


class StepEvidence(StrictModel):
    """Connect a completed objective to actual evidence IDs, not an action outcome."""

    step: int = Field(ge=1, le=8)
    evidence_ids: list[int] = Field(min_length=1, max_length=8)


class Claim(StrictModel):
    """Require evidence references for each factual report statement."""

    text: str = Field(min_length=1, max_length=1000)
    evidence_ids: list[int] = Field(min_length=1, max_length=8)


class Comparison(StrictModel):
    """Represent a comparison as a criterion and individually cited observations."""

    criterion: str = Field(max_length=150)
    observations: list[Claim] = Field(min_length=1, max_length=6)


class Report(StrictModel):
    """Preserve uncertainties separately from evidence-grounded findings."""

    title: str = Field(max_length=150)
    summary: list[Claim] = Field(max_length=4)
    findings: list[Claim] = Field(max_length=12)
    comparisons: list[Comparison] = Field(max_length=6)
    missing_information: list[str] = Field(max_length=12)
    completion_status: Literal["completed", "partial"]
    completed_steps: list[StepEvidence] = Field(max_length=8)


class ResearchRequest(StrictModel):
    """Bound input size before persisting or forwarding a research request."""

    prompt: str = Field(min_length=10, max_length=3000)


class Approval(StrictModel):
    """Bind user approval to the exact revision displayed in the review screen."""

    revision: int = Field(ge=1)
