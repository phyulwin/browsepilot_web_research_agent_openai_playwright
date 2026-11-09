# backend/agent.py — approval-gated observe/decide/act loop and evidence-bound synthesis.
import asyncio
import json
import logging
import time
from urllib.parse import quote_plus

from backend import prompts
from backend.browser import BrowserStartupError, NavigationError, ResearchBrowser
from backend.config import settings
from backend.llm import BudgetExceeded, ModelClient, ModelUnavailable
from backend.models import Action, Extraction, Plan, Report
from backend.research import SEARCH_URLS, ResearchMemory, fingerprint, is_search, url_key
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
                attempted = step["status"] == "in_progress"
                step.update(status="failed" if attempted and status in {"failed", "partial"} else "skipped",
                            detail="Objective remains unverified at the end of research" if attempted else "Not reached before execution ended")
        self.store.save(session)
        self.store.event(session["id"], "status", {"message": session["message"]})

    async def plan(self, session):
        # Planning has no browser object and therefore cannot browse before approval.
        client = None
        try:
            client = ModelClient(session, self.store)
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
            if client:
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

    def checklist(self, session):
        # Send criteria and evidence links without replaying verbose operational status details.
        return [{"number": step["number"], "task": step["task"], "criterion": step["details"],
                 "status": step["status"], "evidence_ids": step.get("evidence_ids", [])} for step in session["steps"]]

    def evidence_limit(self):
        # Keep a bounded, multilingual synthesis context within the existing session allowance.
        return min(8000, settings.max_session_tokens // 5)

    def report_evidence(self, session):
        # Round-robin sources so a long first page cannot crowd every other source out of synthesis.
        sources = {}
        for item in session["evidence"]:
            sources.setdefault(item["url"], []).append(item)
        selected, size = [], 0
        for row in range(max((len(items) for items in sources.values()), default=0)):
            for items in sources.values():
                if row >= len(items):
                    continue
                item = {key: items[row][key] for key in ("id", "url", "title", "quote")}
                cost = len(json.dumps(item, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) + 1
                if size + cost <= self.evidence_limit():
                    selected.append(item)
                    size += cost
        return selected

    def report_payload(self, session):
        # The same bounded payload drives both synthesis and its advance budget reservation.
        evidence = self.report_evidence(session)
        return {"request": session["prompt"], "checklist": self.checklist(session), "evidence": evidence,
                "omitted_passages": len(session["evidence"]) - len(evidence),
                "requested_sources": session.get("requested_sources", [])}

    def report_reserve(self, session, observation=None):
        # Include room for newly extracted quotes before approving another paid extraction.
        payload = self.report_payload(session)
        reserve = ModelClient.allowance(Report, prompts.REPORT, payload, "Report") + 1024
        if observation:
            current = len(json.dumps(payload["evidence"], ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
            reserve += max(0, self.evidence_limit() - current)
        return reserve

    async def extract(self, client, session, observation):
        # Search results and error documents never qualify as original-source evidence.
        if is_search(observation["url"]) or not observation.get("usable") or observation.get("access_issue"):
            raise ValueError("Open an accessible original source before extracting evidence")
        payload = {"request": session["prompt"], "criteria": self.checklist(session),
                   "existing_evidence": self.report_evidence(session), "url": observation["url"],
                   "untrusted_page_text": observation["text"]}
        result = await client.ask(Extraction, prompts.EXTRACTION, payload, "Extraction",
                                  report_reserve=self.report_reserve(session, observation))
        page_text = normalize(observation["text"])
        accepted, matched_ids = 0, []
        valid = True
        for passage in result.passages:
            if len(session["evidence"]) >= 48:
                break
            quote = normalize(passage.quote)
            if quote not in page_text:
                valid = False
                self.store.event(session["id"], "warning", {"message": "Rejected a quote that did not match the observed page"})
                continue
            existing = next((item for item in session["evidence"] if item["quote"] == quote and item["url"] == observation["url"]), None)
            if existing:
                matched_ids.append(existing["id"])
                continue
            session["evidence"].append({"id": len(session["evidence"]) + 1, "url": observation["url"], "title": observation["title"], "quote": quote, "retrieved_at": now()})
            matched_ids.append(session["evidence"][-1]["id"])
            accepted += 1
        # Objective completion requires matching source text, not merely a successful navigation.
        session["goal_satisfied"] = False
        if valid and matched_ids:
            for number in result.completed_steps:
                if 1 <= number < len(session["steps"]):
                    session["steps"][number - 1].update(status="completed", detail="Objective supported by verified source passages", evidence_ids=matched_ids)
            session["goal_satisfied"] = result.goal_satisfied and not result.missing_information
        # Keep failed coverage assessments visible even when a page has no relevant passages.
        session["coverage_gaps"] = result.missing_information
        self.store.event(session["id"], "evidence", {"message": f"Collected {accepted} verified passages; {len(session['evidence'])} total",
                         "url": observation["url"], "goal_satisfied": session.get("goal_satisfied", False)})
        self.store.save(session)
        return accepted

    async def make_report(self, session, client):
        # Bind every model-supplied reference to a passage validated against actual browser text.
        session.update(status="reporting", message="Generating your source-linked research report")
        self.store.save(session)
        if not session["evidence"]:
            self.fallback_report(session, "No source passages could be verified during this research.")
            return
        payload = self.report_payload(session)
        try:
            report = await client.ask(Report, prompts.REPORT, payload, "Report")
        except (BudgetExceeded, ModelUnavailable):
            self.fallback_report(session, "AI synthesis was unavailable within the remaining budget or API allowance; verified original-language passages are preserved below.")
            return
        known_ids = {item["id"] for item in payload["evidence"]}
        claims = report.summary + report.findings + [claim for comparison in report.comparisons for claim in comparison.observations]
        if any(not set(claim.evidence_ids).issubset(known_ids) for claim in claims):
            self.fallback_report(session, "The generated synthesis contained an unknown source reference; verified quotations are shown instead.")
            return
        # A browsing deadline is not a factual gap when the requested answer is already supported.
        missing_sources = [item["url"] for item in session.get("requested_sources", []) if not item["verified"]]
        if missing_sources:
            report.missing_information.append("Could not verify the requested source content: " + ", ".join(missing_sources))
        if not claims or report.missing_information:
            report.completion_status = "partial"
        if report.completion_status == "partial" and not report.missing_information:
            report.missing_information.append("The collected evidence does not fully answer the approved request.")
        for coverage in report.completed_steps:
            if coverage.step <= len(session["steps"]) and set(coverage.evidence_ids).issubset(known_ids):
                session["steps"][coverage.step - 1].update(status="completed", detail="Objective supported by cited report evidence", evidence_ids=coverage.evidence_ids)
        session["report"] = report.model_dump()
        if claims:
            session["steps"][-1].update(status="completed", detail="Cited report generated; remaining information gaps are listed")

    def fallback_report(self, session, reason):
        # A deterministic partial report preserves source quotes without fabricating a synthesis.
        gaps = [reason, *session.get("coverage_gaps", [])]
        if session.get("stop_reason") and session["stop_reason"] != reason:
            gaps.append(session["stop_reason"])
        missing_sources = [item["url"] for item in session.get("requested_sources", []) if not item["verified"]]
        if missing_sources:
            gaps.append("Requested sources without verified passages: " + ", ".join(missing_sources))
        session["report"] = {"title": session["title"], "summary": [], "findings": [{"text": item["quote"], "evidence_ids": [item["id"]]} for item in session["evidence"][:12]], "comparisons": [], "missing_information": gaps, "completion_status": "partial", "completed_steps": []}
        self.store.event(session["id"], "warning", {"message": reason})

    async def run(self, session):
        # The clock covers startup, browser actions, extraction, and final synthesis together.
        browser = ResearchBrowser()
        client = None
        memory = ResearchMemory(session["prompt"])
        browser.blocked_domains = memory.blocked_domains
        session["requested_sources"] = [{"url": url, "verified": False} for url in memory.direct_urls]
        session["goal_satisfied"] = False
        session["coverage_gaps"] = []
        history = []
        started = time.monotonic()
        try:
            client = ModelClient(session, self.store)
            async with asyncio.timeout(settings.max_runtime_seconds):
                await browser.start()
                observation = await browser.observe()
                await self.snapshot(session, browser, observation)
                for number in range(1, settings.max_steps + 1):
                    if len(session["evidence"]) >= 48:
                        session["stop_reason"] = "Evidence capacity reached; proceeding to synthesis"
                        break
                    if time.monotonic() - started > settings.max_runtime_seconds - min(45, settings.max_runtime_seconds / 4):
                        session["stop_reason"] = "Reserved remaining runtime for the final report"
                        break
                    if memory.rejections >= 3 or memory.no_progress >= 4:
                        session["stop_reason"] = "Stopped repeated actions without new content or evidence"
                        break
                    # Direct links, read/extract, and evidence-based stopping require no action-model call.
                    action = memory.next_action(session, observation)
                    try:
                        if action is None:
                            action = await client.ask(Action, prompts.ACTION_SELECTION, {
                                "approved_request": session["prompt"], "approved_plan": self.checklist(session),
                                "untrusted_observation": memory.compact_observation(observation),
                                "evidence": [{"id": item["id"], "url": item["url"], "quote": item["quote"][:160]}
                                             for item in self.report_evidence(session)],
                                "memory": memory.summary(), "remaining_gaps": session.get("coverage_gaps", []),
                                "recent_actions": history[-5:], "remaining_actions": settings.max_steps - number + 1},
                                "Browser action", report_reserve=self.report_reserve(session))
                    except (BudgetExceeded, ModelUnavailable) as exc:
                        session["stop_reason"] = self.safe_error(exc)
                        break
                    if action.step > len(session["steps"]) or any(update.step > len(session["steps"]) for update in action.updates):
                        memory.rejections += 1
                        history.append({"command": action.command, "result": "Invalid checklist index; choose an existing objective"})
                        self.store.event(session["id"], "warning", {"message": history[-1]["result"]})
                        continue
                    target, provider = None, None
                    try:
                        target, provider = memory.prepare(action, observation)
                    except ValueError as exc:
                        # Tell the next decision why an equivalent action was rejected without executing it.
                        memory.rejections += 1
                        history.append({"command": action.command, "result": str(exc)})
                        self.store.event(session["id"], "strategy", {"message": str(exc)})
                        continue
                    memory.rejections = 0
                    session.update(action_count=number, message=action.reason)
                    step = session["steps"][action.step - 1]
                    if step["status"] == "pending":
                        step.update(status="in_progress", detail=action.reason)
                    self.store.event(session["id"], "action", {"message": action.reason, "action": action.model_dump(), "sequence": number, "search_provider": provider})
                    self.store.save(session)
                    before, evidence_count = fingerprint(observation), len(session["evidence"])
                    try:
                        if action.command == "DONE":
                            session["stop_reason"] = action.reason
                            break
                        observation = await browser.execute(action, search_provider=provider)
                        if observation["usable"]:
                            memory.visited.add(url_key(observation["url"]))
                            if target:
                                memory.aliases[url_key(target)] = url_key(observation["url"])
                        if action.command == "READ_PAGE":
                            memory.read.add(fingerprint(observation))
                        if action.command == "EXTRACT":
                            memory.extracted.add((url_key(observation["url"]), fingerprint(observation)))
                            await self.extract(client, session, observation)
                        # Action suggestions cannot assert completion or fail a high-level objective.
                        for update in action.updates:
                            item = session["steps"][update.step - 1]
                            if update.status == "in_progress" and item["status"] != "completed":
                                item.update(status="in_progress", detail=update.detail)
                        if action.command == "SEARCH":
                            memory.pending_search = None if observation["links"] else action.query
                            if observation["links"]:
                                memory.successful_searches.add(memory.query_key(action.query))
                            if not observation["links"]:
                                self.store.event(session["id"], "strategy", {"message": f"{provider} returned no usable source links; try the next provider or an original source"})
                        history.append({"command": action.command, "url": observation["url"], "result": "succeeded"})
                        self.store.event(session["id"], "result", {"message": f"{action.command} completed", "sequence": number, "url": observation["url"]})
                    except (BudgetExceeded, ModelUnavailable) as exc:
                        # Preserve the reserved synthesis attempt rather than jumping directly to fallback.
                        session["stop_reason"] = self.safe_error(exc)
                        break
                    except Exception as exc:
                        # An inaccessible source is an action failure, not proof that its objective failed.
                        error = f"{action.command} failed ({type(exc).__name__}): " + (str(exc)[:180] if isinstance(exc, (ValueError, NavigationError)) else "Page unavailable or operation timed out")
                        if step["status"] != "completed":
                            step.update(status="in_progress", detail="Source unavailable; seeking another way to meet this objective")
                        failed_url = exc.url if isinstance(exc, NavigationError) else target
                        if provider:
                            failed_url = SEARCH_URLS[provider] + quote_plus(action.query)
                            memory.pending_search = action.query
                        memory.failure(failed_url, restricted=isinstance(exc, NavigationError) and exc.restricted)
                        history.append({"command": action.command, "result": error})
                        self.store.event(session["id"], "error", {"message": error, "sequence": number})
                        self.store.event(session["id"], "strategy", {"message": "Avoid this failed URL; use an unvisited source or the next search provider",
                                         "blocked_domains": sorted(memory.blocked_domains)})
                        try:
                            observation = await browser.observe()
                        except Exception:
                            observation = {**observation, "text": "", "links": [], "usable": False, "access_issue": "Page observation unavailable"}
                    # Record source redirects and progress without re-sending page bodies to the model.
                    memory.no_progress = memory.no_progress + 1 if before == fingerprint(observation) and evidence_count == len(session["evidence"]) else 0
                    verified_urls = {url_key(item["url"]) for item in session["evidence"]}
                    for source in session["requested_sources"]:
                        key = url_key(source["url"])
                        source["verified"] = key in verified_urls or memory.aliases.get(key) in verified_urls
                    session["research_memory"] = memory.summary()
                    await self.snapshot(session, browser, observation)
                else:
                    session["stop_reason"] = "The maximum number of browser actions was reached"
                self.store.event(session["id"], "status", {"message": session.get("stop_reason", "Browser research ended"),
                                 "evidence_count": len(session["evidence"]), "usage": session["usage"]})
                await self.make_report(session, client)
                status = session["report"]["completion_status"] if session["evidence"] else "failed"
                self.finish(session, status, "Research report ready" if status != "failed" else "No source evidence could be verified; inspect the action log and report limitations")
        except asyncio.CancelledError:
            self.finish(session, "cancelled", "Research stopped; collected evidence has been preserved")
        except Exception as exc:
            logger.error("Research failed for %s (%s)", session["id"], type(exc).__name__)
            message = self.safe_error(exc)
            session["stop_reason"] = message
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
            if client:
                await client.close()


def report_markdown(session):
    # Export plain Markdown using only source URLs stored by the browser executor.
    report = session["report"]
    sources = {item["id"]: item["url"] for item in session["evidence"]}
    lines = [f"# {report['title']}", "", f"Status: {report['completion_status']}", "", f"Request: {session['prompt']}", ""]
    for heading, claims in (("Executive summary", report["summary"]), ("Key findings", report["findings"])):
        lines += [f"## {heading}", ""]
        for claim in claims:
            lines += [f"- {claim['text']} " + " ".join(f"[{ref}](<{sources[ref]}>)" for ref in claim["evidence_ids"] if ref in sources)]
        lines.append("")
    if report["comparisons"]:
        lines += ["## Comparisons", ""]
        for comparison in report["comparisons"]:
            lines += [f"### {comparison['criterion']}", ""]
            lines += [f"- {claim['text']} " + " ".join(f"[{ref}](<{sources[ref]}>)" for ref in claim["evidence_ids"] if ref in sources) for claim in comparison["observations"]]
            lines.append("")
    lines += ["## Missing or unverified information", ""]
    lines += [f"- {item}" for item in report["missing_information"]] or ["No additional gaps identified by the agent; review the cited sources."]
    lines += ["", "## Sources and verified passages", ""]
    for item in session["evidence"]:
        lines += [f"### Source {item['id']}", f"{item['title']} — <{item['url']}>", f"Retrieved: {item['retrieved_at']}", "", f"> {item['quote']}", ""]
    return "\n".join(lines)
