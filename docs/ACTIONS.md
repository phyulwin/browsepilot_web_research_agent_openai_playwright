# Browser action contract

All actions are parsed into `backend.models.Action`; extra fields and missing command arguments are rejected before explicit interpreter dispatch.

Every action includes a public `reason`, a one-based checklist `step`, nullable argument fields, and optional progress `updates`. Checklist objectives are distinct from individual browser commands.

| Command | Argument | Behavior |
| --- | --- | --- |
| `SEARCH` | `query`, 1–400 characters | Use the next configured public search provider; never solve access challenges |
| `NAVIGATE` | `url` | Validate a public HTTP(S) URL, navigate, check response, reobserve |
| `CLICK` | `link_id` | Follow an anchor from the latest observation; no event handlers or form submission |
| `READ_PAGE` | none | Refresh rendered primary content and bounded visible links |
| `EXTRACT` | none | Select quotes via OpenAI, then verify exact normalized substring matches |
| `SCROLL` | `direction`, up/down | Move 650 pixels and reobserve; long-page text windows shift with scrolling |
| `SWITCH_TAB` | `tab_id` | Select a zero-based observed tab ID |
| `NEW_TAB` | optional `url` | Open a blank or safely navigated tab, at most four |
| `BACK` | none | Return to the previous page and reobserve |
| `WAIT` | `seconds`, 1–3 | Bounded wait followed by observation |
| `DONE` | none | End browser decisions and synthesize collected evidence |

Unused arguments are null; nonexistent links, tabs, and checklist indexes are rejected. Explicit prompt URLs take priority over search; accessible source content automatically receives READ_PAGE and EXTRACT before further navigation. These deterministic choices use the same action contract and audit events as model-selected choices.

Equivalent URLs, queries, unchanged observations, and repeated actions are tracked per run. Searches try each configured provider once per normalized query; access challenges and HTTP 401/403/429 block that host for the run, while two other navigation failures exhaust that host. Empty or blocked searches trigger bounded provider fallback; three rejected actions or four actions without new content/evidence end browsing. No access challenge is bypassed.

Action updates may mark objectives in progress, but only exact-match extraction and source-referenced synthesis may mark them completed. A failed browser action leaves the objective in progress while alternatives remain. At termination, unresolved attempted objectives become failed for partial/failed research; untouched objectives become skipped, and cancellation does not mark them failed. Report completion depends on the requested information and source coverage, not stale action statuses; semantic coverage is still model-assessed.

Browsing reserves one model request and a conservative token allowance for synthesis. Report failure preserves original quotations with partial status; a run with no verified evidence has failed session status. Markdown citations use verified original source URLs directly.

There are no typing, form-submission, uploading, downloading, cookie-editing, arbitrary JavaScript, shell, credential, or local-file tools. Fixed application-authored JavaScript only observes DOM content.
