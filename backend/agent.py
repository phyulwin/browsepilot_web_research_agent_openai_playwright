# backend/agent.py — approval-gated observe/decide/act loop and evidence-bound synthesis.
import asyncio
import logging
import time
from urllib.parse import urlsplit

from backend import prompts
from backend.browser import BrowserStartupError, ResearchBrowser
from backend.config import settings
from backend.llm import BudgetExceeded, ModelClient, ModelUnavailable
from backend.models import Action, Extraction, Plan, Report
from backend.storage import now

logger = logging.getLogger(__name__)


def normalize(text):
    # Normalize only whitespace when matching exact quotes to observed page content.
    return " ".join(text.split())


class AgentManager:
    """Own in-process jobs; the deployment intentionally uses a single Uvicorn worker."""

    def __init__(self, store):
        # Planning and browser jobs share one bounded concurrency pool.
        self.store = store
        self.tasks = {}

    def busy(self):
        # Ignore completed task callbacks when checking the active execution limit.
        return sum(not task.done() for task in self.tasks.values()) >= settings.max_concurrent_sessions

    def launch(self, session, planning=False):
        # Register before yielding to prevent approval races from starting two jobs.
        task = asyncio.create_task(self.plan(session) if planning else self.run(session))
        self.tasks[session["id"]] = task
        task.add_done_callback(lambda finished: self.tasks.pop(session["id"], None) if self.tasks.get(session["id"]) is finished else None)

    async def stop(self, session_id):
        # Cancellation interrupts model calls and browser waits, then runs their cleanup.
        task = self.tasks.get(session_id)
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        session = self.store.get(session_id)
        if session and session["status"] in {"planning", "running", "reporting"}:
            self.finish(session, "cancelled", "Research stopped by the user")

    async def shutdown(self):
        # Let every owned job close its browser and record a terminal state on shutdown.
        for session_id in list(self.tasks):
            await self.stop(session_id)

    def finish(self, session, status, message=None):
        # Never present unfinished checklist items as completed merely because execution ended.
        session.update(status=status, completed_at=now(), message=message or status.replace("_", " "))
        if status == "failed":
            session["error"] = message
        for step in session["steps"]:
            if step["status"] in {"pending", "in_progress"}:
                step.update(status="skipped", detail="Not verified before execution ended")
        self.store.save(session)
        self.store.event(session["id"], "status", {"message": session["message"]})

    async def plan(self, session):
        # Planning has no browser object and therefore cannot browse before approval.
        client = ModelClient(session, self.store)
        try:
            async with asyncio.timeout(100):
                plan = await client.ask(Plan, prompts.PLANNING, {"request": session["prompt"]}, "Planning")
            session.update(title=plan.title, objective=plan.objective, steps=[{"number": index + 1, **step.model_dump(), "status": "pending", "detail": "Awaiting your approval"} for index, step in enumerate(plan.steps)], status="awaiting_approval", message="Your research plan is ready for review", error=None)
            self.store.save(session)
            self.store.event(session["id"], "status", {"message": "Plan generated; no browser has been launched"})
        except asyncio.CancelledError:
            self.finish(session, "cancelled", "Planning stopped")
        except Exception as exc:
            logger.error("Planning failed for %s (%s)", session["id"], type(exc).__name__)
            self.finish(session, "failed", self.safe_error(exc))
        finally:
            await client.close()

    def safe_error(self, exc):
        # Prevent upstream exception bodies or URLs containing credentials from reaching the UI.
        if isinstance(exc, TimeoutError):
            return "The configured time limit was reached; collected evidence is saved."
        if isinstance(exc, (BudgetExceeded, ModelUnavailable, BrowserStartupError)):
            return str(exc)
        return f"Research could not continue ({type(exc).__name__}); check configuration, API access, and the action log."

    async def snapshot(self, session, browser, observation):
        # Save a fresh screenshot atomically and publish only the server-observed active URL.
        session["active_url"] = observation["url"]
        session["page_title"] = observation["title"]
        session["blocked_requests"] = observation.get("blocked_requests", 0)
        try:
            image = await browser.screenshot()
            destination = settings.data_dir / f"{session['id']}.jpg"
            temporary = destination.with_suffix(".tmp")
            temporary.write_bytes(image)
            temporary.replace(destination)
            session["screenshot_version"] += 1
        except Exception as exc:
            self.store.event(session["id"], "warning", {"message": f"Screenshot unavailable ({type(exc).__name__})"})
        self.store.save(session)

    async def extract(self, client, session, observation):
        # Search results provide leads, not original-source evidence.
        host = urlsplit(observation["url"]).hostname or ""
        if host in {"www.google.com", "google.com", "www.bing.com", "duckduckgo.com", "html.duckduckgo.com"}:
            raise ValueError("Open the original source before extracting evidence")
        result = await client.ask(Extraction, prompts.EXTRACTION, {"request": session["prompt"], "url": observation["url"], "untrusted_page_text": observation["text"]}, "Extraction")
        page_text = normalize(observation["text"])
        accepted = 0
        for passage in result.passages:
            if len(session["evidence"]) >= 48:
                break
            quote = normalize(passage.quote)
            if quote not in page_text:
                self.store.event(session["id"], "warning", {"message": "Rejected a quote that did not match the observed page"})
                continue
            if any(item["quote"] == quote and item["url"] == observation["url"] for item in session["evidence"]):
                continue
            session["evidence"].append({"id": len(session["evidence"]) + 1, "url": observation["url"], "title": observation["title"], "quote": quote, "retrieved_at": now()})
            accepted += 1
        self.store.event(session["id"], "evidence", {"message": f"Collected {accepted} verified passages", "url": observation["url"]})
        self.store.save(session)

    async def make_report(self, session, client):
        # Bind every model-supplied reference to a passage validated against actual browser text.
        session.update(status="reporting", message="Generating your source-linked research report")
        self.store.save(session)
        if not session["evidence"]:
            self.fallback_report(session, "No source passages could be verified during this research.")
            return
        report = await client.ask(Report, prompts.REPORT, {"request": session["prompt"], "checklist": session["steps"], "evidence": session["evidence"], "execution_limit": session.get("limit_reason")}, "Report")
        known_ids = {item["id"] for item in session["evidence"]}
        claims = report.summary + report.findings + [claim for comparison in report.comparisons for claim in comparison.observations]
        if any(not set(claim.evidence_ids).issubset(known_ids) for claim in claims):
            self.fallback_report(session, "The generated synthesis contained an unknown source reference; verified quotations are shown instead.")
            return
        if session.get("limit_reason"):
            report.completion_status = "partial"
            report.missing_information.append(session["limit_reason"])
        if not claims:
            report.completion_status = "partial"
        session["report"] = report.model_dump()
        if report.completion_status == "completed":
            # Synthesis completion is explicit; unresolved retrieval steps remain visible as gaps.
            for step in session["steps"]:
                if step["number"] == len(session["steps"]) and step["status"] in {"pending", "in_progress"}:
                    step.update(status="completed", detail="Cited report generated from collected evidence")
            if any(step["status"] != "completed" for step in session["steps"]):
                session["report"]["completion_status"] = "partial"
                session["report"]["missing_information"].append("Some checklist objectives were not confirmed complete.")

    def fallback_report(self, session, reason):
        # A deterministic partial report preserves source quotes without fabricating a synthesis.
        session["report"] = {"title": session["title"], "summary": [], "findings": [{"text": item["quote"], "evidence_ids": [item["id"]]} for item in session["evidence"][:12]], "comparisons": [], "missing_information": [reason], "completion_status": "partial"}

    async def run(self, session):
        # The clock covers startup, browser actions, extraction, and final synthesis together.
        browser = ResearchBrowser()
        client = ModelClient(session, self.store)
        history = []
        started = time.monotonic()
        try:
            async with asyncio.timeout(settings.max_runtime_seconds):
                await browser.start()
                observation = await browser.observe()
                await self.snapshot(session, browser, observation)
                for number in range(1, settings.max_steps + 1):
                    if time.monotonic() - started > settings.max_runtime_seconds - min(45, settings.max_runtime_seconds / 4):
                        session["limit_reason"] = "Browser research stopped to reserve time for the final report."
                        break
                    action = await client.ask(Action, prompts.ACTION_SELECTION, {"approved_request": session["prompt"], "approved_plan": session["steps"], "untrusted_observation": observation, "evidence": [{"id": e["id"], "url": e["url"], "quote": e["quote"][:220]} for e in session["evidence"]], "recent_actions": history[-7:], "remaining_actions": settings.max_steps - number + 1}, "Browser action")
                    if action.step > len(session["steps"]) or any(update.step > len(session["steps"]) for update in action.updates):
                        raise ValueError("The model referenced a nonexistent checklist item")
                    session.update(action_count=number, message=action.reason)
                    step = session["steps"][action.step - 1]
                    if step["status"] == "pending":
                        step.update(status="in_progress", detail=action.reason)
                    self.store.event(session["id"], "action", {"message": action.reason, "action": action.model_dump(), "sequence": number})
                    self.store.save(session)
                    try:
                        observation = await browser.execute(action)
                        if action.command == "EXTRACT":
                            await self.extract(client, session, observation)
                        # Apply reported progress only after the associated action succeeds.
                        for update in action.updates:
                            if update.status == "completed" and not session["evidence"]:
                                continue
                            session["steps"][update.step - 1].update(status=update.status, detail=update.detail)
                        history.append({"command": action.command, "url": observation["url"], "result": "succeeded"})
                        self.store.event(session["id"], "result", {"message": f"{action.command} completed", "sequence": number, "url": observation["url"]})
                    except (BudgetExceeded, ModelUnavailable):
                        # Stop paid work immediately when the account or per-session budget cannot continue.
                        raise
                    except Exception as exc:
                        # Return operational errors to the next decision so the agent can choose another source.
                        error = f"{action.command} failed ({type(exc).__name__}): " + (str(exc)[:180] if isinstance(exc, (ValueError, RuntimeError)) else "Page unavailable or operation timed out")
                        step.update(status="failed", detail=error)
                        history.append({"command": action.command, "result": error})
                        self.store.event(session["id"], "error", {"message": error, "sequence": number})
                        observation = await browser.observe()
                    await self.snapshot(session, browser, observation)
                    if action.command == "DONE":
                        break
                else:
                    session["limit_reason"] = "The maximum number of browser actions was reached."
                await self.make_report(session, client)
                self.finish(session, session["report"]["completion_status"], "Research report ready")
        except asyncio.CancelledError:
            self.finish(session, "cancelled", "Research stopped; collected evidence has been preserved")
        except Exception as exc:
            logger.error("Research failed for %s (%s)", session["id"], type(exc).__name__)
            message = self.safe_error(exc)
            self.store.event(session["id"], "error", {"message": message})
            if session["evidence"]:
                self.fallback_report(session, message)
                self.finish(session, "partial", message)
            else:
                self.finish(session, "failed", message)
        finally:
            try:
                await browser.close()
            except Exception as exc:
                logger.error("Browser cleanup failed (%s)", type(exc).__name__)
            await client.close()


def report_markdown(session):
    # Export plain Markdown using only source URLs stored by the browser executor.
    report = session["report"]
    lines = [f"# {report['title']}", "", f"Status: {report['completion_status']}", "", f"Request: {session['prompt']}", ""]
    for heading, claims in (("Executive summary", report["summary"]), ("Key findings", report["findings"])):
        lines += [f"## {heading}", ""]
        for claim in claims:
            lines += [f"- {claim['text']} " + " ".join(f"[{ref}](#source-{ref})" for ref in claim["evidence_ids"])]
        lines.append("")
    if report["comparisons"]:
        lines += ["## Comparisons", ""]
        for comparison in report["comparisons"]:
            lines += [f"### {comparison['criterion']}", ""]
            lines += [f"- {claim['text']} " + " ".join(f"[{ref}](#source-{ref})" for ref in claim["evidence_ids"]) for claim in comparison["observations"]]
            lines.append("")
    lines += ["## Missing or unverified information", ""]
    lines += [f"- {item}" for item in report["missing_information"]] or ["No additional gaps identified by the agent; review the cited sources."]
    lines += ["", "## Sources and verified passages", ""]
    for item in session["evidence"]:
        lines += [f"### Source {item['id']}", f"{item['title']} — <{item['url']}>", f"Retrieved: {item['retrieved_at']}", "", f"> {item['quote']}", ""]
    return "\n".join(lines)
