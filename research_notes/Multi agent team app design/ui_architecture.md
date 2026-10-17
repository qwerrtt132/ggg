# Live-Updating Multi-Agent UI: Server and Client Architecture

Research date: 2026-10-17. Scope: a self-hosted, single-user, one-command local app showing live agent messages, a shared task board, agent status, a human-in-the-loop input, and an API-key settings page.

**Verification caveat.** In this session, direct fetches of most primary documentation pages failed with DNS errors (developer.mozilla.org, fastapi.tiangolo.com, starlette.dev, flask.palletsprojects.com, docs.python.org, docs.langchain.com, microsoft.github.io). Findings below therefore come mostly from search-result excerpts, which I cite by the URL the search returned. Where a claim rests on a snippet rather than a fetched page, I say so. Anything I could not confirm is listed under Gaps. Before committing to versions, re-check the official pages.

**Summary recommendation.** Use one-way Server-Sent Events (SSE) from the server to the browser for all live state. Send human input as ordinary HTTP POST requests. Use a single Python backend process with async tasks for agent runs, and persist every state change as an append-only event row in SQLite (WAL mode). Each SSE event carries the row's sequence number as its `id`, so a reconnecting browser can replay what it missed. The frontend is plain HTML and ES modules with the native `EventSource` API and no build step. The backend can be FastAPI (pin FastAPI >= 0.135.0 for its built-in SSE support), or the standard library `ThreadingHTTPServer` if zero dependencies matters more than ergonomics.

---

## Key Question 1: SSE vs. WebSockets vs. polling for server-to-browser updates

### Takeaway
SSE is the simplest robust option for one-way server-to-browser streaming. It uses plain HTTP, the browser reconnects and resumes on its own, and it needs no protocol upgrade. WebSockets only earn their extra complexity when the browser must send high-rate or low-latency messages over the same channel. Human input in this app is infrequent, so HTTP POST covers it.

### Cited Findings
- EventSource reconnects automatically. "By default, if the connection between the client and server closes, the connection is restarted," and it stops only when `.close()` is called. A server `id` field sets the EventSource's last event ID, exposed as `lastEventId`. A `retry` field sets the reconnect delay in integer milliseconds. — [MDN, Using server-sent events](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events) (from search excerpt, not fetched)
- Browsers cap HTTP/1.1 connections per host at about 6. Each open EventSource holds one of those for its lifetime, so several tabs or a page's ordinary API calls can stall. One write-up reports a stall of API calls caused by open SSE streams on HTTP/1.1, and says a Chrome bug about this was closed "By Design." — [textslashplain, "The Pitfalls of EventSource over HTTP/1.1" (2019-12-04)](https://textslashplain.com/2019/12/04/the-pitfalls-of-eventsource-over-http-1-1/) (search excerpt; blog post, dated 2019)
- Over HTTP/2, many streams share one connection, which removes most of that limit. — [enterno SSE glossary](https://enterno.io/en/s/glossary-sse) (secondary source; the exact stream limit varies by browser and server)
- FastAPI added built-in SSE support in version 0.135.0 via `fastapi.sse` (`EventSourceResponse`, `ServerSentEvent`). The class supports `id`, `event`, `retry`, and `comment` fields. It works with any HTTP method, and a plain object yielded as `data` is JSON-encoded. — [FastAPI SSE tutorial](https://fastapi.tiangolo.com/tutorial/server-sent-events/) and [FastAPI SSE reference](https://fastapi.tiangolo.com/reference/sse/) (version claim taken from the Korean tutorial mirror, https://fastapi.tiangolo.com/ko/tutorial/server-sent-events/; I did not find the official 0.135.0 release notes)
- A WebSocket is a full-duplex channel, and the browser API is `new WebSocket(...)` with `send` and `onmessage`. Note that it does not reconnect on its own, so the app must write its own reconnect and replay logic. — [MDN, WebSocket](https://developer.mozilla.org/en-US/docs/Web/API/WebSocket) (not fetched; this is my understanding of the MDN page, and it is unverified in this session)

### Inferences
- With SSE, the event `id` plus a server-side event log gives a replay mechanism on reconnect. The browser sends the last `id` it saw and the server resends everything after it. That replay covers the robustness needs of a live feed without any extra protocol. The browser sends `Last-Event-ID` on reconnect per the WHATWG HTML spec. I did not confirm that header behavior in this session, so verify it against the spec.
- WebSockets become worth it only if the browser must stream high-rate data back to the server (for example, live keystroke-level control of agents). A human-in-the-loop reply is a single request and does not need a persistent channel.
- Polling is simplest of all but adds latency and load. A poll every 1 to 2 seconds is acceptable for a board that changes slowly, but it does not fit streaming tokens. Use it only as a fallback.
- Keep the HTTP/1.1 connection limit in mind. Use one SSE stream per page (multiplexing all event types onto it) rather than one stream per agent, and serve the app over HTTP/2 if possible. With a local single-user app there is usually one tab, so this is minor.

### Gaps
- I could not fetch the MDN WebSocket page or the WHATWG SSE section, so the `Last-Event-ID` header behavior is cited from the spec and not checked here.
- No current data on whether the 6-connection limit still applies to recent Chromium and Firefox releases. Treat the number as approximate.

---

## Key Question 2: Simplest reliable Python backend, and what the docs say about SSE

### Takeaway
Python has three realistic choices. The standard library's `ThreadingHTTPServer` works with zero dependencies, but you write SSE framing yourself. Starlette/FastAPI has async streaming and (in FastAPI 0.135.0+) a built-in SSE response class. Flask supports streaming generators, but its SSE story depends on threaded workers and it has less direct async support. For this app, FastAPI pinned to 0.135.0 or later is the most straightforward choice. Choose stdlib if you refuse any dependency.

### Cited Findings
- `http.server.ThreadingHTTPServer` was added in Python 3.7. It handles each request in a thread. The docs state the module is "not recommended for production" and implements only basic security checks. — [Python docs, http.server](https://docs.python.org/3/library/http.server.html) (search excerpt); version note from [What's New in Python 3.7](https://docs.python.org/3.12/whatsnew/3.7.html)
- Starlette's `StreamingResponse` accepts an async generator or a normal iterator and streams the body. The documented example is an async generator that yields fragments with `asyncio.sleep` between them. — [Starlette, Responses](https://www.starlette.dev/responses) (search excerpt; not fetched). Starlette does not ship a dedicated SSE class in these excerpts. The third-party `sse-starlette` package provides `EventSourceResponse`. Its version is unclear: a PyPI page appeared in search for 1.1.4, and an aggregator listed v3.4.8. Its README reportedly warns that SSE does not work with `GZipMiddleware`. — [sse-starlette on PyPI](https://pypi.org/project/sse-starlette/1.1.4/) (conflicting version data; verify on PyPI)
- Flask 3.1.2 was released 2025-08-19. Its changelog includes a fix to `stream_with_context` for async views (issue #5774). Flask 3.1.0 was released 2024-11-13 and dropped Python 3.8. Flask 3.1.3 was released 2026-02-18 as a security release (session access tracking). — [Flask changelog](https://flask.palletsprojects.com/changes/) (search excerpt; secondary mirrors also list 3.1.3)
- FastAPI SSE docs describe `EventSourceResponse` as usable with any HTTP method, which matters for MCP-style POST streams. — [FastAPI SSE reference](https://fastapi.tiangolo.com/reference/sse/) (search excerpt)
- Third-party guides suggest yielding bytes from FastAPI streaming generators and calling `.encode()`. I did not confirm this in an official source, so I am not relying on it. — [leyaa.ai FastAPI streaming guide](https://leyaa.ai/codefly/learn/fastapi/qna/how-to-stream-response-in-fastapi) (unverified)

### Inferences
- **Recommended: FastAPI + uvicorn, pinned to >= 0.135.0.** It gives async handlers, which fit agent runs as asyncio tasks, plus `EventSourceResponse` for SSE framing, and automatic Pydantic models for the JSON API. Pin the exact version in the lockfile because the SSE class is new.
- **Acceptable alternative: stdlib.** A `ThreadingHTTPServer` handler can write `text/event-stream` frames directly (`id:`, `event:`, `data:` lines, blank line terminator). Each SSE client gets a `queue.Queue`, and worker threads publish to it. This is about 100 lines and needs no installs. Its weaknesses are the thread-per-connection model, no built-in async, and that the docs themselves advise against production use. For a single-user local tool those weaknesses are tolerable.
- **Flask works** for SSE with a generator response and the threaded server. It is the least aligned with async agent runs, so it is my last choice for this app.
- For an SSE endpoint, disable response buffering in any proxy. Local single-process use does not need a proxy at all.

### Gaps
- No direct check of the FastAPI 0.135.0 release date or changelog. The date is unknown in this session.
- Starlette's current SSE guidance (beyond `StreamingResponse`) was not verified against its docs.
- `sse-starlette` current version and maintenance status are unresolved.

---

## Key Question 3: Frontend, plain HTML/JS vs. framework

### Takeaway
A static HTML page with ES modules and the native `EventSource` API covers the requirements (live feed, board, status badges, input, settings) with zero build step. Adopt React or Svelte only if the board or the feed needs complex client-side state.

### Cited Findings
- The `EventSource` API is built into browsers and reconnects automatically (see Key Question 1). — [MDN, Using server-sent events](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events) (search excerpt)
- No source in this session compared framework vs. no-build approaches with cited data. The comparison below is my engineering judgment, not a sourced finding.

### Inferences
- **Plain HTML/JS** is the best fit for "easy to run." The page is served by the same backend, so there is one command and no Node toolchain. The UI pieces are simple: an append-only list (feed), a few columns (board), status dots, a form, and a settings form. Use native `<dialog>`, CSS grid, and `textContent` (never `innerHTML` for agent output, which is untrusted text).
- **React or Svelte** add a build step (Node, bundler, lockfile) that the user must run. That cost is worth paying only if the app grows client-side routing, complex drag-and-drop on the board, or heavy shared state.
- A middle path is Preact or Alpine.js loaded from a CDN pinned to an exact version, which keeps the no-build property with less boilerplate. This adds a network dependency at runtime, so vendor the file if the app must work offline.

### Gaps
- No sourced benchmark or survey on framework trade-offs for small local tools.

---

## Key Question 4: Persistence (SQLite vs. JSON files vs. in-memory)

### Takeaway
Use SQLite with WAL mode. It gives durable, inspectable, resumable state with the standard library (`sqlite3`), and one local file holds everything. An append-only events table doubles as the SSE replay log.

### Cited Findings
- WAL mode lets readers and writers overlap. Readers see a consistent snapshot, but there is still only one writer at a time. Checkpoints run automatically at 1000 pages by default, and WAL does not work across a network filesystem. — [SQLite, Write-Ahead Logging](https://www.sqlite.org/wal.html) (search excerpt; not fetched)
- `synchronous=NORMAL` with WAL keeps the database consistent but can lose the last transactions on power failure. — [SQLite WAL page](https://www.sqlite.org/wal.html) (search excerpt)

### Inferences
- Model state as: `runs`, `agents`, `messages` (append-only), `tasks` (board), `events` (append-only, `seq INTEGER PRIMARY KEY` used as the SSE `id`), and `settings`. Every mutation writes its row and an event row in one transaction, then notifies the SSE subscribers.
- JSON files are fine for config but poor for concurrent writes and for querying history. In-memory state is lost on restart, which conflicts with "resumed or inspected later."
- Store API keys carefully. A settings page that writes plaintext keys into SQLite is a risk even locally. Prefer environment variables or the OS keyring, or at least a file with restrictive permissions, and never echo the full key back to the browser.
- Use one writer connection per process or a single writer thread, to respect SQLite's single-writer model.

### Gaps
- No sourced evidence on the practical write throughput limits for this workload. A few writes per second is far below SQLite's limits, but I have not measured it.
- Key-storage best practice was not sourced in this session.

---

## Key Question 5: How existing agent-UI projects structure their event stream

### Takeaway
LangGraph and similar frameworks expose a typed stream of events (state updates, message tokens, custom progress) that a server forwards to the UI. AutoGen-based projects have commonly used a WebSocket plus a queue. Both patterns map onto the same design: the agent runtime emits typed events, and the server fans them out to the browser.

### Cited Findings
- LangGraph stream modes are `updates` (state changes after each step, labeled by node), `values`, `messages` (LLM tokens with node metadata), `custom` (arbitrary data emitted through a stream writer), `checkpoints`, `tasks`, and `debug`. Multiple modes can be requested together. — [LangGraph streaming docs](https://docs.langchain.com/oss/python/langgraph/streaming) (search excerpt; not fetched)
- LangGraph's v2 output format, where each chunk carries a `type` field, requires LangGraph 1.1 or later. A newer event streaming API arrived in LangGraph v1.2. The docs recommend it for new applications. — [LangGraph streaming docs](https://docs.langchain.com/oss/python/langgraph/streaming) (search excerpt; exact versions as reported in the excerpt)
- A Victor Dibia walkthrough describes FastAPI plus WebSockets plus queues for streaming AutoGen agent messages to a UI. Agent messages are recorded through a reply hook, then pushed to the client. — [Victor Dibia newsletter, "Integrating AutoGen Agents into Your Web Application"](https://newsletter.victordibia.com/p/integrating-autogen-agents-into-your) (search excerpt; undated; a personal newsletter, not official docs)
- The AG2 "Classic" docs include a WebSocket and IOStream tutorial dated 2025-01-10 (per the URL), and state that the package is in maintenance mode with AG2 v1.0+ as the active line. — [AG2 Classic docs, WebSockets](https://classic.docs.ag2.ai/latest/docs/blog/2025/01/10/WebSockets/) (search excerpt)
- The `autogen-ui` repository includes a WebSocketHandler that streams agent log events to a connected UI session, and notes that AutoGen Studio is being rewritten on the AgentChat API. — [victordibia/autogen-ui](https://github.com/victordibia/autogen-ui) (search excerpt; unverified current state)

### Inferences
- The common pattern is: typed event objects (`message`, `status`, `task_update`, `input_request`), a sequence number, and a fan-out from one producer to many subscribers. SSE can carry the same typed events using the `event:` field, so the browser can register one listener per type.
- A "human-in-the-loop" request is an event the UI renders as a form. The reply is a POST that resolves a pending future in the agent run. This is the same pattern LangGraph's interrupts use, though I did not verify LangGraph's interrupt API in this session.
- Mirroring a framework's event schema (for example LangGraph's `updates`/`messages`/`custom`) keeps the door open to plugging in a real framework later.

### Gaps
- Could not verify AutoGen Studio's current UI or wire format from official docs. The sources above are secondary or dated, and the rewrite may have changed the stream design.
- Did not verify LangGraph's interrupt or human-in-the-loop API.
- No primary source for how LangGraph Studio structures its own UI stream.

---

## Key Question 6: Running long-running agent work (threads vs. asyncio, cancellation, timeouts)

### Takeaway
Run each agent as an asyncio task in one event loop, with cooperative cancellation through `Task.cancel()` and deadlines through `asyncio.timeout()` (Python 3.11+). Wrap blocking LLM SDK calls in a worker thread so they do not block the loop. If you choose the stdlib HTTP server, use threads with a `threading.Event` for cancellation instead.

### Cited Findings
- `Task.cancel()` schedules `CancelledError` inside the coroutine. The coroutine may catch it and refuse to cancel, so cancellation is not guaranteed. The docs advise against suppressing it, and if you must, call `uncancel()`. Use `try/finally` for cleanup and re-raise `CancelledError`. — [Python docs, Coroutines and tasks](https://docs.python.org/3/library/asyncio-task.html) (search excerpt; 3.13 and 3.15 pages)
- `asyncio.timeout()` was added in Python 3.11. It cancels the block at the deadline and converts the cancellation into `TimeoutError`, which must be caught outside the `async with` block. `asyncio.wait_for()` has been implemented on top of `asyncio.timeout()` since 3.12. — [Python docs, Coroutines and tasks](https://docs.python.org/3/library/asyncio-task.html) (search excerpt)
- The docs warn that `asyncio.timeout()` and `TaskGroup` can misbehave if code swallows `CancelledError`. — [Python docs, Coroutines and tasks](https://docs.python.org/3/library/asyncio-task.html) (search excerpt)

### Inferences
- Use a supervisor pattern: a `RunManager` holds `{run_id: asyncio.Task}`. `POST /runs/{id}/cancel` calls `task.cancel()`, and the run writes a `cancelled` event in its `finally` block.
- Each agent step checks the cancel state at its await points. Keep tool calls and LLM calls as short awaits so cancellation takes effect quickly.
- Blocking SDK calls must not run on the event loop. `asyncio.to_thread` (Python 3.9+) is the standard approach. Note that a thread cannot be force-killed, so cancellation of a blocking call only takes effect when that call returns. Use the SDK's own timeout or request timeout parameter for that.
- Apply a per-run and per-step deadline with `asyncio.timeout()`. Record timeouts as events so the UI can show them.
- Keep the agent run state in SQLite as it advances, so a crash leaves a resumable record. On startup, mark runs that were `running` as `interrupted`.

### Gaps
- Did not verify the threading and blocking-call behavior of any specific LLM SDK in this session.
- Did not check any AutoGen or LangGraph documentation on timeouts and cancellation.

---

## Versions and dates relied on

| Item | Version or date | Source / status |
|---|---|---|
| Python `asyncio.timeout` | added 3.11 | Python docs (search excerpt) |
| Python `ThreadingHTTPServer` | added 3.7 | Python docs (search excerpt) |
| Python doc versions seen | 3.12.13, 3.13.12, 3.14.8, 3.15.0 (latest listed) | Search results; the local interpreter version was not checked |
| FastAPI built-in SSE (`fastapi.sse`) | 0.135.0 | FastAPI tutorial (Korean mirror); release date unknown |
| FastAPI patch releases seen | 0.135.2, 0.135.3 | Aggregator only (releasebot), unverified |
| Flask | 3.1.2 (2025-08-19); 3.1.3 (2026-02-18) | Flask changelog and mirrors (search excerpt) |
| sse-starlette | version conflict: 1.1.4 (PyPI page) vs. v3.4.8 (aggregator) | Unresolved |
| LangGraph v2 stream format | LangGraph 1.1+; new streaming API in v1.2 | LangGraph docs (search excerpt) |
| AG2 Classic WebSocket tutorial | 2025-01-10 (per URL) | AG2 docs (search excerpt) |
| SQLite WAL | auto-checkpoint default 1000 pages | SQLite docs (search excerpt) |

Recommended pins once verified: Python 3.12 or later, FastAPI >= 0.135.0 (exact version to be pinned), uvicorn (version not checked), stdlib `sqlite3`.

---

## Consolidated gaps

- Most primary doc pages (MDN, FastAPI, Starlette, Flask, Python, LangGraph, AutoGen) were not fetched successfully. Re-check claims against those pages before implementation.
- FastAPI 0.135.0 release date and changelog not confirmed.
- `sse-starlette` version and maintenance status unresolved.
- AutoGen Studio's current UI stream format not verified.
- Browser HTTP/1.1 connection limits are approximate and dated (2019 blog).
- Python interpreter and dependency versions installed locally were not checked.
- No measured throughput data for SQLite or SSE at this app's scale.
