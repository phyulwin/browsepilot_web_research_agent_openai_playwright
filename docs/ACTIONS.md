# Browser action contract

All actions are parsed into `backend.models.Action`; extra fields and missing command arguments are rejected before explicit interpreter dispatch.

Every action includes a public `reason`, a one-based checklist `step`, nullable argument fields, and optional progress `updates`. Checklist objectives are distinct from individual browser commands.

| Command | Argument | Behavior |
| --- | --- | --- |
| `SEARCH` | `query`, 1–400 characters | Open a URL-encoded Google search without solving access challenges |
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

Unused arguments are null; nonexistent links, tabs, and checklist indexes are rejected. Failures feed into the next decision and remain audited. Completed progress updates require collected evidence and apply after successful actions, but progress is model-assessed rather than formal proof of coverage. Unresolved objectives keep reports partial, and pending work becomes skipped when execution ends.

There are no typing, form-submission, uploading, downloading, cookie-editing, arbitrary JavaScript, shell, credential, or local-file tools. Fixed application-authored JavaScript only observes DOM content.
