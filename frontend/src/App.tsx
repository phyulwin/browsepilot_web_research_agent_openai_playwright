// App.tsx — BrowsePilot dashboard, plan approval, live execution, and cited reports.
import { useCallback, useEffect, useRef, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import {
  Activity,
  ArrowDownToLine,
  ArrowLeft,
  ArrowRight,
  ArrowUpRight,
  BookOpen,
  Check,
  CheckCheck,
  ChevronRight,
  Circle,
  Clock3,
  Compass,
  Copy,
  FileText,
  Globe2,
  Layers3,
  ListChecks,
  LoaderCircle,
  LockKeyhole,
  LogOut,
  Menu,
  Plus,
  RefreshCw,
  Settings2,
  ShieldCheck,
  Sparkles,
  Square,
  Terminal,
  TriangleAlert,
  X,
  Zap,
} from "lucide-react";
import { api } from "./api";
import type { Claim, Config, Session, Status } from "./types";

// Labels translate persistence states into clear user-facing language.
const labels: Record<Status, string> = {
  planning: "Planning",
  awaiting_approval: "Ready for review",
  running: "Researching",
  reporting: "Writing report",
  completed: "Completed",
  partial: "Partial report",
  failed: "Needs attention",
  cancelled: "Stopped",
};
const activeStates: Status[] = ["planning", "running", "reporting"];
function StatusBadge({ status }: { status: Status }) {
  // Show both a written status and a color indicator for accessible recognition.
  return (
    <span className={`status-badge ${status}`}>
      <span className={activeStates.includes(status) ? "pulse-dot" : ""} />
      {labels[status]}
    </span>
  );
}

function Elapsed({ session }: { session: Session }) {
  // Keep the elapsed display moving during execution and freeze it at the recorded finish time.
  const [current, setCurrent] = useState(Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setCurrent(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  const seconds = session.started_at
    ? Math.max(
        0,
        Math.floor(
          ((session.completed_at ? Date.parse(session.completed_at) : current) -
            Date.parse(session.started_at)) /
            1000,
        ),
      )
    : 0;
  return (
    <span>
      {Math.floor(seconds / 60)}:{String(seconds % 60).padStart(2, "0")}
    </span>
  );
}

function Checklist({ session }: { session: Session }) {
  // High-level objectives stay distinct from the chronological browser command log.
  const done = session.steps.filter(
    (step) => step.status === "completed",
  ).length;
  return (
    <section className="panel checklist">
      <div className="panel-heading">
        <span>
          <ListChecks size={17} />
          Research plan
        </span>
        <span className="mono muted">
          {done}/{session.steps.length}
        </span>
      </div>
      <div className="progress-track">
        <div
          style={{
            width: `${session.steps.length ? (done / session.steps.length) * 100 : 0}%`,
          }}
        />
      </div>
      <ol>
        {session.steps.map((step) => (
          <li key={step.number} className={`step ${step.status}`}>
            <div className="step-number">
              {step.status === "completed" ? (
                <Check size={15} />
              ) : step.status === "in_progress" ? (
                <LoaderCircle size={15} className="spin" />
              ) : step.status === "failed" ? (
                <X size={15} />
              ) : (
                String(step.number).padStart(2, "0")
              )}
            </div>
            <div>
              <h3>{step.task}</h3>
              <p>
                {session.status === "awaiting_approval"
                  ? step.details
                  : step.detail}
              </p>
              <span className="step-status">
                {step.status.split("_").join(" ")}
              </span>
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}

function ClaimText({ claim, session }: { claim: Claim; session: Session }) {
  // Citations resolve only to original URLs in the backend's verified evidence inventory.
  return (
    <>
      {claim.text}{" "}
      <span className="citations">
        {claim.evidence_ids.map((id) => {
          const source = session.evidence.find(
            (evidence) => evidence.id === id,
          );
          return source ? (
            <a
              key={id}
              href={`#source-${id}`}
              onClick={(event) => {
                event.preventDefault();
                document
                  .getElementById(`source-${id}`)
                  ?.scrollIntoView({ block: "start", behavior: "smooth" });
              }}
              title={source.title}
            >
              [{id}]
            </a>
          ) : null;
        })}
      </span>
    </>
  );
}

function ResearchReport({
  session,
  notify,
}: {
  session: Session;
  notify: (message: string) => void;
}) {
  // Copy and download use the server-generated Markdown, keeping exports consistent with saved reports.
  const report = session.report!;
  const [copied, setCopied] = useState(false);
  async function copyReport() {
    try {
      const response = await fetch(`/api/sessions/${session.id}/report.md`);
      if (!response.ok) throw new Error("The report could not be downloaded.");
      await navigator.clipboard.writeText(await response.text());
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2200);
    } catch (error) {
      notify(
        error instanceof Error
          ? error.message
          : "Copy is unavailable; use Download Markdown.",
      );
    }
  }
  return (
    <article className="panel report">
      <div className="report-top">
        <span className="eyebrow">
          <FileText size={15} />
          RESEARCH BRIEF
        </span>
        <StatusBadge status={report.completion_status} />
      </div>
      <h2>{report.title}</h2>
      <div className="report-actions">
        <button className="button secondary small" onClick={copyReport}>
          {copied ? <CheckCheck size={15} /> : <Copy size={15} />}
          {copied ? "Copied" : "Copy report"}
        </button>
        <a
          className="button secondary small"
          href={`/api/sessions/${session.id}/report.md`}
          download
        >
          <ArrowDownToLine size={15} />
          Download Markdown
        </a>
      </div>
      {report.summary.length > 0 && (
        <section>
          <h3>Executive summary</h3>
          {report.summary.map((claim, index) => (
            <p key={index}>
              <ClaimText claim={claim} session={session} />
            </p>
          ))}
        </section>
      )}
      {report.findings.length > 0 && (
        <section>
          <h3>Key findings</h3>
          <ul>
            {report.findings.map((claim, index) => (
              <li key={index}>
                <ClaimText claim={claim} session={session} />
              </li>
            ))}
          </ul>
        </section>
      )}
      {report.comparisons.length > 0 && (
        <section>
          <h3>Comparisons</h3>
          <div className="comparison-table">
            {report.comparisons.map((comparison, index) => (
              <div key={index}>
                <h4>{comparison.criterion}</h4>
                <ul>
                  {comparison.observations.map((claim, i) => (
                    <li key={i}>
                      <ClaimText claim={claim} session={session} />
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </section>
      )}
      <section className="report-gaps">
        <h3>
          <TriangleAlert size={17} />
          Missing or unverified information
        </h3>
        {report.missing_information.length ? (
          <ul>
            {report.missing_information.map((gap, index) => (
              <li key={index}>{gap}</li>
            ))}
          </ul>
        ) : (
          <p>
            No additional gaps identified by the agent; review the cited
            sources.
          </p>
        )}
      </section>
      <section>
        <h3>
          Sources & verified passages{" "}
          <span className="count">{session.evidence.length}</span>
        </h3>
        <div className="sources">
          {session.evidence.map((source) => (
            <div className="source" id={`source-${source.id}`} key={source.id}>
              <span className="source-index">{source.id}</span>
              <div>
                <a href={source.url} target="_blank" rel="noreferrer">
                  {source.title || new URL(source.url).hostname}
                  <ArrowUpRight size={14} />
                </a>
                <p className="source-url">{source.url}</p>
                <blockquote>{source.quote}</blockquote>
                <small>
                  Retrieved {new Date(source.retrieved_at).toLocaleString()}
                </small>
              </div>
            </div>
          ))}
        </div>
      </section>
      <p className="report-footnote">
        Passages are matched against retrieved page text; the synthesis is
        AI-generated and should be reviewed against its sources.
      </p>
    </article>
  );
}

function LiveWorkspace({
  session,
  config,
  perform,
  pending,
  notify,
  edit,
}: {
  session: Session;
  config: Config | null;
  perform: (operation: string) => void;
  pending: boolean;
  notify: (message: string) => void;
  edit: () => void;
}) {
  // Keep browser activity accessible after completion for transparent review of the execution.
  const [tab, setTab] = useState<"report" | "activity">("report");
  const active = activeStates.includes(session.status);
  const logs = session.events.filter((event) => event.kind !== "model");
  const bottom = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (active) bottom.current?.scrollIntoView({ block: "nearest" });
  }, [logs.length, active]);
  if (session.status === "planning")
    return (
      <section className="planning-state panel">
        <div className="planning-orbit">
          <Compass size={34} />
          <span />
        </div>
        <span className="eyebrow">CHARTING A COURSE</span>
        <h2>
          A little direction.
          <br />A better discovery.
        </h2>
        <p>BrowsePilot is turning your request into a focused research plan.</p>
        <div className="skeleton-stack">
          <div />
          <div />
          <div />
        </div>
        <p className="fine">
          <ShieldCheck size={14} />
          Your browser stays idle until you approve the plan.
        </p>
        <button
          className="button secondary small"
          disabled={pending}
          onClick={() => perform("stop")}
        >
          Cancel planning
        </button>
      </section>
    );
  // A failed or cancelled planner has no browser session; show its specific recovery path.
  if (!active && !session.started_at && session.steps.length === 0)
    return (
      <section className="planning-state panel">
        <div className="icon-tile">
          <TriangleAlert size={25} />
        </div>
        <span className="eyebrow">PLAN NOT CREATED</span>
        <h2>
          {session.status === "cancelled"
            ? "Planning stopped."
            : "Planning needs attention."}
        </h2>
        <p role={session.status === "failed" ? "alert" : "status"}>
          {session.error || session.message}
        </p>
        <p className="fine">
          <ShieldCheck size={14} />
          No research browser was launched.
        </p>
        <button className="button primary" onClick={edit}>
          <ArrowLeft size={16} />
          Edit & try again
        </button>
        <details className="planning-error-details">
          <summary>View recorded activity</summary>
          {logs.map((event) => (
            <p key={event.id}>
              {new Date(event.created_at).toLocaleTimeString()} ·{" "}
              {event.message}
            </p>
          ))}
        </details>
      </section>
    );
  if (session.status === "awaiting_approval")
    return (
      <div className="approval-layout">
        <Checklist session={session} />
        <aside className="panel approval-card">
          <div className="icon-tile">
            <ShieldCheck size={25} />
          </div>
          <span className="eyebrow">YOU SET THE COURSE</span>
          <h2>Ready to explore?</h2>
          <p>{session.objective}</p>
          <div className="approval-facts">
            <span>
              <Globe2 size={16} />
              Public websites only
            </span>
            <span>
              <Clock3 size={16} />
              Up to {Math.ceil((config?.max_runtime_seconds || 240) / 60)}{" "}
              minutes
            </span>
            <span>
              <ListChecks size={16} />
              Up to {config?.max_steps || 18} browser actions
            </span>
          </div>
          <button
            className="button primary"
            disabled={pending}
            onClick={() => perform("approve")}
          >
            {pending ? (
              <LoaderCircle size={17} className="spin" />
            ) : (
              <Compass size={17} />
            )}
            Start research
            <ArrowRight size={17} />
          </button>
          <button
            className="button secondary"
            disabled={pending}
            onClick={() => perform("regenerate")}
          >
            <RefreshCw size={15} />
            Regenerate plan
          </button>
          <button className="text-button" onClick={edit}>
            <ArrowLeft size={14} />
            Edit request
          </button>
          <p className="fine">
            Approval starts a real browser and uses your configured OpenAI API
            account.
          </p>
        </aside>
      </div>
    );
  return (
    <>
      <div className="execution-toolbar">
        <div className="execution-metrics">
          <span>
            <Clock3 size={15} />
            <Elapsed session={session} />
          </span>
          <span>
            <MousePointerIcon />
            {session.action_count} actions
          </span>
          <span>
            <BookOpen size={15} />
            {session.evidence.length} passages
          </span>
        </div>
        {active && (
          <button
            className="button danger small"
            onClick={() => perform("stop")}
            disabled={pending}
          >
            <Square size={13} />
            Stop research
          </button>
        )}
      </div>
      {session.report && (
        <div className="tabs" role="tablist" aria-label="Research results">
          <button
            role="tab"
            aria-selected={tab === "report"}
            onClick={() => setTab("report")}
            className={tab === "report" ? "selected" : ""}
          >
            <FileText size={16} />
            Research report
          </button>
          <button
            role="tab"
            aria-selected={tab === "activity"}
            onClick={() => setTab("activity")}
            className={tab === "activity" ? "selected" : ""}
          >
            <Activity size={16} />
            Browser activity
          </button>
        </div>
      )}
      {session.report && tab === "report" ? (
        <ResearchReport session={session} notify={notify} />
      ) : (
        <div className="workspace-grid">
          <div className="workspace-main">
            <section className="panel browser-panel">
              <div className="browser-chrome">
                <div className="browser-dots">
                  <i />
                  <i />
                  <i />
                </div>
                <div className="browser-address">
                  <LockKeyhole size={12} />
                  <span>{session.active_url || "Browser is starting"}</span>
                </div>
                <span className="live-label">
                  <span className={active ? "pulse-dot" : ""} />
                  {active ? "LIVE" : "CAPTURED"}
                </span>
              </div>
              <div className="browser-canvas">
                {session.screenshot_version > 0 ? (
                  <img
                    src={`/api/sessions/${session.id}/screenshot?v=${session.screenshot_version}`}
                    alt={`Actual browser capture: ${session.page_title || session.active_url}`}
                  />
                ) : (
                  <div className="browser-empty">
                    <Globe2 size={40} />
                    <h3>
                      {active
                        ? "Opening a window to the web"
                        : "No browser capture available"}
                    </h3>
                    <p>
                      {active
                        ? "Your isolated browser is getting ready."
                        : "This session ended before a screenshot was captured."}
                    </p>
                  </div>
                )}
              </div>
              <div className="browser-caption">
                {active ? (
                  <LoaderCircle size={14} className="spin" />
                ) : (
                  <Circle size={12} />
                )}
                <span aria-live="polite">{session.message}</span>
              </div>
            </section>
            <section className="panel activity-panel">
              <div className="panel-heading">
                <span>
                  <Terminal size={16} />
                  Action log
                </span>
                <span className="mono muted">OBSERVE → DECIDE → ACT</span>
              </div>
              <div
                className="action-log"
                role="log"
                aria-label="Browser action log"
              >
                {logs.map((event) => (
                  <div key={event.id} className={`log-entry ${event.kind}`}>
                    <time>
                      {new Date(event.created_at).toLocaleTimeString(
                        undefined,
                        { hour12: false },
                      )}
                    </time>
                    <span className="log-marker">
                      {event.kind === "error" ? (
                        <X size={12} />
                      ) : event.kind === "evidence" ? (
                        <BookOpen size={12} />
                      ) : (
                        <ChevronRight size={12} />
                      )}
                    </span>
                    <p>
                      {event.action && <b>{event.action.command}</b>}
                      {event.message}
                    </p>
                  </div>
                ))}
                <div ref={bottom} />
              </div>
            </section>
          </div>
          <div className="workspace-side">
            <Checklist session={session} />
            <div className="panel evidence-card">
              <BookOpen size={18} />
              <div>
                <strong>{session.evidence.length} verified passages</strong>
                <p>
                  Every passage is checked against text read from the browser.
                </p>
              </div>
            </div>
          </div>
        </div>
      )}
      {!active && !session.report && (
        <div className="notice">
          <TriangleAlert size={18} />
          <div>
            <strong>{labels[session.status]}</strong>
            <p>{session.error || session.message}</p>
            {session.evidence.length > 0 && (
              <details>
                <summary>Review preserved evidence</summary>
                {session.evidence.map((evidence) => (
                  <blockquote key={evidence.id}>
                    {evidence.quote}{" "}
                    <a href={evidence.url} target="_blank" rel="noreferrer">
                      Source {evidence.id}
                    </a>
                  </blockquote>
                ))}
              </details>
            )}
          </div>
          <button className="button secondary small" onClick={edit}>
            Edit & try again
          </button>
        </div>
      )}
      <p className="usage-line">
        Model usage: {session.usage.calls} requests ·{" "}
        {(
          session.usage.input_tokens + session.usage.output_tokens
        ).toLocaleString()}{" "}
        reported tokens
      </p>
    </>
  );
}

function MousePointerIcon() {
  // A small reusable action icon keeps metric spacing consistent.
  return <Zap size={15} />;
}

export default function App() {
  // The URL hash makes research workspaces reopenable without introducing a routing service.
  const [page, setPage] = useState("dashboard");
  const [session, setSession] = useState<Session | null>(null);
  const [config, setConfig] = useState<Config | null>(null);
  const [auth, setAuth] = useState<boolean | null>(null);
  const [prompt, setPrompt] = useState("");
  const [password, setPassword] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [toast, setToast] = useState("");
  const [menu, setMenu] = useState(false);
  const [connected, setConnected] = useState(true);
  const promptRef = useRef<HTMLTextAreaElement>(null);
  const reduceMotion = useReducedMotion();
  const selectedId = useRef("");

  const route = useCallback(async () => {
    // Ignore stale fetch responses when the user changes workspace during a request.
    const value = window.location.hash.slice(1);
    if (value === "main-content" || value.startsWith("source-")) return;
    setMenu(false);
    if (value.startsWith("research/")) {
      const id = value.slice(9);
      selectedId.current = id;
      setPage("research");
      setSession((previous) => (previous?.id === id ? previous : null));
      try {
        const data = await api<Session>(`/sessions/${encodeURIComponent(id)}`);
        if (selectedId.current === id) setSession(data);
      } catch (error) {
        if (selectedId.current === id)
          setError(
            error instanceof Error ? error.message : "Session unavailable.",
          );
      }
    } else {
      selectedId.current = "";
      setSession(null);
      setPage(
        ["guide", "settings"].includes(value) ? value : "dashboard",
      );
    }
  }, []);

  useEffect(() => {
    // Load access state before protected endpoints, including direct session links.
    api<{ authenticated: boolean }>("/auth")
      .then((value) => setAuth(value.authenticated))
      .catch(() => setError("Cannot reach the BrowsePilot server."));
  }, []);
  useEffect(() => {
    if (!auth) return;
    api<Config>("/config")
      .then(setConfig)
      .catch((error) => setError(error.message));
    void route();
    window.addEventListener("hashchange", route);
    return () => window.removeEventListener("hashchange", route);
  }, [auth, route]);
  useEffect(() => {
    // Native EventSource reconnects automatically; each event is a complete persisted snapshot.
    if (!session || !activeStates.includes(session.status)) return;
    const stream = new EventSource(`/api/sessions/${session.id}/events`);
    stream.addEventListener("snapshot", (event) => {
      const data: Session = JSON.parse((event as MessageEvent).data);
      setConnected(true);
      if (selectedId.current === data.id) setSession(data);
      if (!activeStates.includes(data.status)) {
        stream.close();
          }
    });
    stream.onopen = () => setConnected(true);
    stream.onerror = () => setConnected(false);
    return () => stream.close();
  }, [session?.id, session?.status]);
  useEffect(() => {
    if (toast) {
      const timer = window.setTimeout(() => setToast(""), 3500);
      return () => clearTimeout(timer);
    }
  }, [toast]);
  useEffect(() => {
    // Honor the displayed N shortcut only when the user is not entering text or using modifiers.
    const shortcut = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement;
      if (
        event.key.toLowerCase() === "n" &&
        !event.ctrlKey &&
        !event.metaKey &&
        !event.altKey &&
        !["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName) &&
        !target.isContentEditable
      ) {
        event.preventDefault();
        setPrompt("");
        window.location.hash = "dashboard";
        window.setTimeout(() => promptRef.current?.focus(), 50);
      }
    };
    window.addEventListener("keydown", shortcut);
    return () => window.removeEventListener("keydown", shortcut);
  }, []);

  function navigate(destination: string) {
    // Clear transient errors while keeping the original task in the prompt composer.
    setError("");
    setMenu(false);
    window.location.hash = destination;
  }
  async function startPlanning(event: FormEvent) {
    // User submission creates only a plan; explicit approval is a separate server request.
    event.preventDefault();
    setPending(true);
    setError("");
    try {
      const data = await api<Session>("/sessions", { prompt: prompt.trim() });
      selectedId.current = data.id;
      setSession(data);
      navigate(`research/${data.id}`);
      } catch (error) {
      setError(
        error instanceof Error ? error.message : "Planning could not start.",
      );
    } finally {
      setPending(false);
    }
  }
  async function perform(operation: string) {
    // Approve only the displayed revision, preventing a stale page from authorizing a revised plan.
    if (!session) return;
    setPending(true);
    setError("");
    try {
      setSession(
        await api<Session>(
          `/sessions/${session.id}/${operation}`,
          operation === "approve" ? { revision: session.revision } : {},
        ),
      );
      } catch (error) {
      setError(
        error instanceof Error
          ? error.message
          : "The operation could not complete.",
      );
    } finally {
      setPending(false);
    }
  }
  function chooseExample(value: string) {
    // Example cards populate an editable prompt without consuming API credits.
    setPrompt(value);
    navigate("dashboard");
    window.setTimeout(() => promptRef.current?.focus(), 50);
  }
  async function signIn(event: FormEvent) {
    // Keep the workspace password only in component memory until the server exchanges it for a cookie.
    event.preventDefault();
    setPending(true);
    setError("");
    try {
      await api("/login", { password });
      setPassword("");
      setAuth(true);
    } catch (error) {
      setError(error instanceof Error ? error.message : "Sign-in failed.");
    } finally {
      setPending(false);
    }
  }
  async function signOut() {
    // Explicit sign-out leaves the research history on the server and removes access in this browser.
    try {
      await api("/logout", {});
      setAuth(false);
      setSession(null);
    } catch {
      setError("Sign-out failed; please try again.");
    }
  }
  if (auth === false)
    return (
      <main className="login-page">
        <form className="panel login-card" onSubmit={signIn}>
          <div className="brand">
            <span className="brand-icon">
              <Compass />
            </span>
            BrowsePilot
          </div>
          <span className="eyebrow">YOUR RESEARCH WORKSPACE</span>
          <h1>Welcome back.</h1>
          <p>Enter the workspace password to continue your research.</p>
          <label htmlFor="password">Workspace password</label>
          <input
            id="password"
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            autoComplete="current-password"
            required
          />
          {error && (
            <p role="alert" className="error-text">
              {error}
            </p>
          )}
          <button className="button primary" disabled={pending}>
            {pending ? (
              <LoaderCircle className="spin" size={17} />
            ) : (
              <LockKeyhole size={17} />
            )}
            Open workspace
            <ArrowRight size={17} />
          </button>
        </form>
      </main>
    );

  let content: ReactNode;
  if (page === "research")
    content = session ? (
      <>
        <div className="workspace-title">
          <button className="text-button" onClick={() => navigate("dashboard")}>
            <ArrowLeft size={14} />
            All research
          </button>
          <div>
            <h1>{session.title}</h1>
            <StatusBadge status={session.status} />
          </div>
          <p>{session.prompt}</p>
        </div>
        {!connected && activeStates.includes(session.status) && (
          <div className="notice">
            <RefreshCw size={17} />
            <p>
              Reconnecting to live updates; research continues on the server.
            </p>
          </div>
        )}
        <LiveWorkspace
          session={session}
          config={config}
          perform={perform}
          pending={pending}
          notify={setToast}
          edit={() => chooseExample(session.prompt)}
        />
      </>
    ) : (
      <div className="panel loading-panel">
        <LoaderCircle className="spin" />
        Loading your research workspace
      </div>
    );
  else if (page === "guide")
    content = (
      <>
        <div className="page-title">
          <span className="eyebrow">FROM QUESTION TO CLARITY</span>
          <h1>A thoughtful way to research.</h1>
          <p>You set the destination. BrowsePilot follows the evidence.</p>
        </div>
        <div className="guide-grid">
          {[
            {
              icon: Sparkles,
              title: "01 / Define your question",
              text: "Give your research a clear scope. Name the subjects, the details that matter, and official URLs when you have them.",
            },
            {
              icon: ListChecks,
              title: "02 / Review the route",
              text: "Read the proposed checklist, refine your request, or regenerate it. No browser opens until you select Start research.",
            },
            {
              icon: Globe2,
              title: "03 / Follow the exploration",
              text: "Watch actual browser captures, actions, and checklist updates. Stop at any time; collected evidence stays with the research session.",
            },
            {
              icon: BookOpen,
              title: "04 / Take the findings with you",
              text: "Review the cited report and its uncertainties. Inspect original passages, copy the report, or download Markdown.",
            },
          ].map((item) => (
            <section className="panel guide-card" key={item.title}>
              <item.icon size={24} />
              <h2>{item.title}</h2>
              <p>{item.text}</p>
            </section>
          ))}
        </div>
        <div className="panel guide-note">
          <ShieldCheck size={25} />
          <div>
            <h2>Built for public-web research</h2>
            <p>
              BrowsePilot does not log in, submit forms, make payments, or
              bypass access challenges; unavailable pages are reported as gaps.
            </p>
          </div>
        </div>
        <button
          className="button primary"
          onClick={() =>
            chooseExample(
              "Visit https://www.python.org/about/ and summarize what Python is and three uses described on that page.",
            )
          }
        >
          <Compass size={17} />
          Try a focused research request
          <ArrowRight size={17} />
        </button>
      </>
    );
  else if (page === "settings")
    content = (
      <>
        <div className="page-title">
          <span className="eyebrow">WORKSPACE CONFIGURATION</span>
          <h1>Mission parameters</h1>
          <p>Server-managed settings keep research focused and bounded.</p>
        </div>
        <section className="panel settings-panel">
          <div className="panel-heading">
            <span>
              <Settings2 size={18} />
              Current configuration
            </span>
          </div>
          {config ? (
            <dl>
              {Object.entries({
                "OpenAI connection": config.configured
                  ? "API key configured"
                  : "API key required",
                "Research model": config.model,
                "Maximum browser actions": config.max_steps,
                "Maximum runtime": `${config.max_runtime_seconds} seconds`,
                "Concurrent sessions": config.max_concurrent_sessions,
                "Token allowance per session":
                  config.max_session_tokens.toLocaleString(),
                "Workspace access": config.access_protected
                  ? "Password protected"
                  : "Local development access",
              }).map(([key, value]) => (
                <div key={key}>
                  <dt>{key}</dt>
                  <dd>{value}</dd>
                </div>
              ))}
            </dl>
          ) : (
            <p>Loading configuration…</p>
          )}
          <p className="fine">
            To change these settings, update the server environment and restart
            BrowsePilot; API credentials are never sent to your browser.
          </p>
        </section>
      </>
    );
  else
    content = (
      <>
        <section className="hero">
          <img
            className="hero-image"
            src="/assets/glacier.webp"
            alt="Blue ice of Perito Moreno Glacier meeting a mountain lake in Patagonia"
          />
          <div className="hero-shade" />
          <div className="hero-content">
            <span className="hero-kicker">
              <span />
              AN AI RESEARCH COMPANION
            </span>
            <h1>
              Go beyond the search.
              <br />
              <span>Find your clarity.</span>
            </h1>
            <p>
              Give your curiosity a direction.
              <br />
              Turn the open web into research you can trace.
            </p>
            <div className="hero-chips">
              <span>
                <ListChecks size={13} />
                You approve the plan
              </span>
              <i />
              <span>
                <Globe2 size={13} />
                AI explores the web
              </span>
              <i />
              <span>
                <BookOpen size={13} />
                Sources stay attached
              </span>
            </div>
          </div>
          <a
            className="photo-credit"
            href="https://unsplash.com/photos/aov9QBaN7l8"
            target="_blank"
            rel="noreferrer"
          >
            PERITO MORENO, PATAGONIA <ArrowUpRight size={11} />
          </a>
        </section>
        <section className="composer panel">
          <div className="composer-heading">
            <div className="mini-compass">
              <Sparkles size={19} />
            </div>
            <label htmlFor="research-prompt">
              Where will your curiosity take you?
            </label>
            <span className="mono">NEW RESEARCH</span>
          </div>
          <form onSubmit={startPlanning}>
            <textarea
              id="research-prompt"
              ref={promptRef}
              placeholder="Ask a question, compare your options, or explore something new…"
              value={prompt}
              onChange={(event) => setPrompt(event.target.value)}
              minLength={10}
              maxLength={3000}
              required
              rows={3}
            />
            <div className="composer-bottom">
              <span>
                <ShieldCheck size={14} />A clear plan first. You're always in
                control.
              </span>
              <button
                className="button primary"
                disabled={
                  pending ||
                  auth !== true ||
                  !config?.configured ||
                  prompt.trim().length < 10
                }
              >
                {pending ? (
                  <LoaderCircle className="spin" size={17} />
                ) : (
                  <Sparkles size={17} />
                )}
                Start planning
                <ArrowRight size={17} />
              </button>
            </div>
          </form>
          {config && !config.configured && (
            <p className="configuration-warning">
              <TriangleAlert size={15} />
              Set OPENAI_API_KEY in the server environment to start research.
            </p>
          )}
        </section>
      </>
    );

  return (
    <div className="app-shell">
      <a
        className="skip-link"
        href="#main-content"
        onClick={(event) => {
          event.preventDefault();
          document.getElementById("main-content")?.focus();
        }}
      >
        Skip to main content
      </a>
      {menu && (
        <button
          className="menu-scrim"
          aria-label="Close navigation"
          onClick={() => setMenu(false)}
        />
      )}
      <aside className={`sidebar ${menu ? "open" : ""}`}>
        <button
          className="brand"
          onClick={() => navigate("dashboard")}
          aria-label="BrowsePilot home"
        >
          <span className="brand-icon">
            <Compass size={25} />
          </span>
          <span>
            Browse<span className="brand-light">Pilot</span>
            <small>RESEARCH, WITH DIRECTION.</small>
          </span>
        </button>
        <button
          className="new-research button"
          onClick={() => {
            setPrompt("");
            navigate("dashboard");
            window.setTimeout(() => promptRef.current?.focus(), 50);
          }}
        >
          <Plus size={17} />
          New research<span>N</span>
        </button>
        <span className="nav-label">WORKSPACE</span>
        <nav aria-label="Main navigation">
          {[
            { id: "dashboard", label: "Overview", icon: Layers3 },
            { id: "guide", label: "How it works", icon: Compass },
          ].map((item) => (
            <button
              key={item.id}
              onClick={() => navigate(item.id)}
              className={
                page === item.id
                  ? "active"
                  : ""
              }
            >
              <item.icon size={18} />
              {item.label}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="research-note">
            <div>
              <span className="tiny-glow" />
              PURPOSEFUL EXPLORATION
            </div>
            <p>
              Less searching.
              <br />
              More understanding.
            </p>
            <div className="contour-lines" />
          </div>
          <button
            className={
              page === "settings" ? "settings-link active" : "settings-link"
            }
            onClick={() => navigate("settings")}
          >
            <Settings2 size={17} />
            Workspace settings
          </button>
          {config?.access_protected && <button className="settings-link" onClick={signOut}><LogOut size={16} />Sign out</button>}
        </div>
      </aside>
      <div className="main-shell">
        <header className="topbar">
          <div>
            <button
              className="mobile-menu icon-button"
              onClick={() => setMenu(!menu)}
              aria-label="Open navigation"
            >
              <Menu size={20} />
            </button>
            <span className="breadcrumb">
              Workspace <ChevronRight size={13} />
              <strong>
                {page === "dashboard"
                  ? "Overview"
                  : page === "research"
                    ? "Research session"
                      : page === "guide"
                        ? "How it works"
                        : "Settings"}
              </strong>
            </span>
          </div>
        </header>
        <main id="main-content" className="main-content" tabIndex={-1}>
          {error && (
            <div className="error-banner" role="alert">
              <TriangleAlert size={18} />
              <span>{error}</span>
              <button
                className="icon-button"
                onClick={() => setError("")}
                aria-label="Dismiss error"
              >
                <X size={16} />
              </button>
            </div>
          )}
          <AnimatePresence mode="wait">
            <motion.div
              key={page === "research" ? session?.id || page : page}
              initial={reduceMotion ? false : { opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.2 }}
            >
              {content}
            </motion.div>
          </AnimatePresence>
        </main>
      </div>
      {toast && (
        <div className="toast" role="status">
          <Copy size={17} />
          {toast}
        </div>
      )}
    </div>
  );
}
