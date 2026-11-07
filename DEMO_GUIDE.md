# BrowsePilot: 1–2 minute demonstration

## Preparation

1. Start the application, check `/api/health`, and confirm the OpenAI API project has credits.
2. Rehearse the focused request below and review its citations.
3. Keep a previously completed **real** session in the library as an explicitly labeled fallback if the live source is unavailable.

> Visit https://www.python.org/about/ and summarize what Python is and three uses described on that page.

A single official HTML page is more repeatable than broad comparisons or searches; model latency varies, and the default research deadline is four minutes plus planning time, not a promise of two-minute completion.

## Presentation script

| Time | Show | Say |
| --- | --- | --- |
| 0:00–0:15 | Dashboard and request | “Research repeats the work of opening pages, gathering facts, and tracking sources; BrowsePilot brings those steps together.” |
| 0:15–0:35 | Planning and checklist | “A planning prompt produces a structured checklist; browsing cannot begin until I approve this exact revision.” |
| 0:35–0:55 | Start research, screenshot, URL | “A second prompt selects a validated command from the actual page observation; Python interprets it and Playwright controls Chromium.” |
| 0:55–1:20 | Action log and progress | “The agent observes after every action, records errors, and updates its checklist; these captures come from the real browser.” |
| 1:20–1:45 | Report and citations | “Quotes must match retrieved text, findings cite evidence, and missing information stays visible.” |
| 1:45–2:00 | Markdown export and library | “Reports and audit history persist in SQLite; everything deploys as one container.” |

Optional highlights: regenerate a plan, cancel a running task, follow a citation to its verified passage, reopen a session after refresh, or explain the fixed action vocabulary and public-network boundaries.

## If a dependency fails live

Show the actual error; never replace inaccessible sources with invented claims or label an earlier run live. For exhausted API credits, fund the API project or configure a funded project key and restart the server. For inaccessible websites, choose a small public HTML source without bypassing access challenges. If the run exceeds the presentation window, openly switch to a previously completed real session.

The development-time AI attempt hit exhausted API credits; see [VERIFICATION.md](VERIFICATION.md) for verified components and limits.
