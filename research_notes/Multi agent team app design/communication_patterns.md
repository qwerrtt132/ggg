# Inter-Agent Communication Patterns and Message Protocols

Research date: 2026-10-17. Several primary doc sites (a2a-protocol.org, modelcontextprotocol.io, openai.github.io, docs.langchain.com, microsoft.github.io, docs.crewai.com) failed DNS resolution from this environment, and some GitHub raw paths returned 404. Sources below are the ones that were reachable. Content from fetched pages was summarized by a small model, so exact identifiers should be checked against the source before implementation. Pages with no visible date are marked as such. Anything not verified in this pass is labeled.

## Which communication topology fits when

### Takeaway
Two topologies have documented, reachable support: orchestrator-worker (a lead agent delegates to parallel subagents that return condensed results) and group chat (a shared thread with a speaker-selection step). Handoffs (one agent transfers the conversation to another) are documented as a separate pattern in the OpenAI Agents SDK. Peer-to-peer mailboxes and blackboards are not documented in any source I could reach.

### Cited Findings
- Anthropic's multi-agent research system uses an orchestrator-worker pattern. A lead agent plans and spawns subagents that search in parallel, and subagents return condensed findings rather than their full context. Published June 13, 2025. [Anthropic, "How we built our multi-agent research system"](https://www.anthropic.com/engineering/multi-agent-research-system)
- The same post reports that agents use roughly 4x the tokens of chat and multi-agent systems roughly 15x. It says this suits high-value, heavily parallelizable tasks, and not tasks needing shared context or many inter-agent dependencies, "such as most coding work." [Anthropic, multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system)
- Anthropic's "Building effective agents" post defines orchestrator-workers as "a central LLM dynamically breaks down tasks, delegates them to worker LLMs, and synthesizes their results." It fits when subtasks cannot be predicted in advance, unlike parallelization with predefined subtasks. Published December 19, 2024; the page notes much of the tooling has since changed. [Anthropic, Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)
- That post advises finding "the simplest solution possible" and says that for many applications "optimizing single LLM calls with retrieval and in-context examples is usually enough." [Anthropic, Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)
- AutoGen's group chat keeps a shared message thread. `update_message_thread` appends every incoming message to `_message_thread`, and only `BaseChatMessage` instances enter the model context used for speaker selection. Undated source file. [AutoGen SelectorGroupChat source](https://raw.githubusercontent.com/microsoft/autogen/main/python/packages/autogen-agentchat/src/autogen_agentchat/teams/_group_chat/_selector_group_chat.py)
- In AutoGen, the next speaker is chosen by an optional `selector_func` first, then by a model call using `selector_prompt` with `{roles}`, `{participants}`, and `{history}`. A single remaining candidate skips the model call. If all attempts fail, the previous speaker, or else the first participant, is used. [AutoGen SelectorGroupChat source](https://raw.githubusercontent.com/microsoft/autogen/main/python/packages/autogen-agentchat/src/autogen_agentchat/teams/_group_chat/_selector_group_chat.py)
- OpenAI Agents SDK handoffs are one-to-one transfers within a single run. Handoffs are exposed to the model as tools named `transfer_to_<agent_name>` by default. Input guardrails apply only to the first agent in the chain, and output guardrails apply only to the agent producing the final output. Page undated. [OpenAI Agents SDK, Handoffs](https://raw.githubusercontent.com/openai/openai-agents-python/main/docs/handoffs.md)
- The OpenAI handoff documentation says, "For multiple destinations, register one handoff per destination and let the model choose." A custom `Handoff` is needed only when code must pick the destination at invocation time. [OpenAI Agents SDK, Handoffs](https://raw.githubusercontent.com/openai/openai-agents-python/main/docs/handoffs.md)
- A2A's Task object supports interruption and resumption by the same `taskId` and `contextId`, which lets a client continue a conversation with a remote agent. [A2A specification, main branch](https://raw.githubusercontent.com/a2aproject/A2A/main/docs/specification.md)

### Inferences
- Orchestrator-worker fits independent, parallelizable subtasks with a clear result-merging step, such as research or search over many sources. It is expensive in tokens, and the sources warn against it when workers depend on each other.
- Handoffs fit routing a single conversation to a specialist that then owns the next turns. The receiving agent inherits history, so the cost is in context size.
- Group chat fits deliberation where several agents react to each other's output. The shared-thread model means every participant sees the full thread, so it scales poorly in context and needs a termination condition.
- The sources do not support a claim about blackboard or mailbox performance. A shared task object (A2A Task with `contextId`) is the closest documented analogue, but I did not verify it against those patterns.

### Gaps
- No reachable source documents peer-to-peer mailboxes or blackboard architectures as implemented in a framework. This is unverified.
- The AutoGen docs site (microsoft.github.io) failed DNS, so the group chat claims rest on source code, not the user guide. The source shows termination conditions exist, but the class list was not verified in this pass.
- No source here compares topologies on latency or reliability with measured data, other than Anthropic's token figures.

## Framework mechanics: OpenAI Agents SDK, LangGraph, AutoGen, CrewAI

### Takeaway
Each framework passes state differently. OpenAI passes the conversation history plus optional typed arguments through a handoff tool. LangGraph uses a shared state object updated by nodes, with `Send` for per-branch payloads and `Command` for combined routing and state update. AutoGen uses a shared message thread with an optional speaker selector. CrewAI's mechanics could not be verified, because its docs were unreachable.

### Cited Findings
- OpenAI handoffs: the receiving agent sees the entire conversation history by default. `input_filter` receives a `HandoffInputData` object with `input_history`, `pre_handoff_items`, `new_items`, `input_items`, and `run_context`, and must return a new `HandoffInputData`. [OpenAI Agents SDK, Handoffs](https://raw.githubusercontent.com/openai/openai-agents-python/main/docs/handoffs.md)
- OpenAI `nest_handoff_history` is an opt-in beta, disabled by default. When enabled, earlier history is compacted into assistant summary segments wrapped in `<CONVERSATION HISTORY>`. The docs state that nesting does not redact sensitive content, and tool arguments and outputs may remain in summaries. [OpenAI Agents SDK, Handoffs](https://raw.githubusercontent.com/openai/openai-agents-python/main/docs/handoffs.md)
- OpenAI server-managed conversations (`conversation_id`, `previous_response_id`, `auto_previous_response_id`) do not support handoff input filters. [OpenAI Agents SDK, Handoffs](https://raw.githubusercontent.com/openai/openai-agents-python/main/docs/handoffs.md)
- LangGraph `Send(node, arg, *, timeout=None)` runs a target node with `arg` as its input, and the docstring notes "the sent state can differ from the core graph's state," which enables map-reduce. `Send` objects are typically returned from conditional edges. [LangGraph types.py](https://raw.githubusercontent.com/langchain-ai/langgraph/main/libs/langgraph/langgraph/types.py)
- LangGraph `Command` is a frozen, keyword-only dataclass with fields `graph` (with `Command.PARENT` targeting the closest parent graph), `update` (applied to state), `resume` (value for the next interrupt), and `goto` (next node names or `Send` objects). The source does not state whether `update` is applied before `goto` runs. [LangGraph types.py](https://raw.githubusercontent.com/langchain-ai/langgraph/main/libs/langgraph/langgraph/types.py)
- LangGraph `interrupt(value, *, response_schema=None)` requires a checkpointer. On resume, the node "re-executes all logic" from its start, and the Nth `interrupt()` call receives the Nth resume value. [LangGraph types.py](https://raw.githubusercontent.com/langchain-ai/langgraph/main/libs/langgraph/langgraph/types.py)
- AutoGen `SelectorGroupChat` exposes `save_state` and `load_state`, which persist the thread, turn count, and previous speaker. `reset` clears the thread, model context, and previous speaker. [AutoGen SelectorGroupChat source](https://raw.githubusercontent.com/microsoft/autogen/main/python/packages/autogen-agentchat/src/autogen_agentchat/teams/_group_chat/_selector_group_chat.py)
- AutoGen source has three docstring/code mismatches: the `allow_repeated_speaker` docstring says a warning is logged, but the code logs at debug level; `candidate_func` is said to ignore `allow_repeated_speaker`, but the previous speaker is still rejected; and the class docstring promises a `ValueError` for an invalid `selector_prompt` that is not validated. [AutoGen SelectorGroupChat source](https://raw.githubusercontent.com/microsoft/autogen/main/python/packages/autogen-agentchat/src/autogen_agentchat/teams/_group_chat/_selector_group_chat.py)

### Inferences
- The frameworks differ mainly in where state lives. OpenAI and AutoGen carry state in the message list. LangGraph carries it in a typed state object with explicit channels, which is easier to inspect and checkpoint.
- Any system that needs resumable human input should use an interrupt-plus-checkpointer model (LangGraph) or a protocol-level input-required state (A2A), rather than relying on conversation replay.

### Gaps
- CrewAI: the docs site failed DNS, and the raw GitHub path returned 404. Its `Process.sequential` / `Process.hierarchical`, `context=[...]`, and `TaskOutput` claims are unverified in this pass.
- LangGraph: state schemas and reducer semantics (`Annotated` reducers, `add_messages`) were not verified in this pass. The `langgraph/docs` low-level page returned 404.
- OpenAI: the SDK run-loop termination rule (which agent's output is final) was not described on the handoff page. This is unverified.
- No source tested how these frameworks behave under concurrent writes to shared state.

## A2A and MCP: what each specifies

### Takeaway
A2A is the protocol for agent-to-agent communication and fits the case where agents from different providers share a goal. It defines Task lifecycle, Message and Part types, Artifacts, and discovery through AgentCards. MCP is the agent-to-tool protocol. The current MCP draft explicitly defines no agent-to-agent communication, and it is moving toward per-request statelessness.

### Cited Findings
A2A (latest visible version 1.0.0, with 0.3.0, 0.2.6, and 0.1.0 earlier; no release date shown) [A2A specification, main branch](https://raw.githubusercontent.com/a2aproject/A2A/main/docs/specification.md):
- Task: a server-generated `id`, `contextId`, `status` (with `state`, optional `message`, and `timestamp`), and `artifacts`. States are grouped as working (`TASK_STATE_SUBMITTED`, `TASK_STATE_WORKING`), interrupted (`TASK_STATE_INPUT_REQUIRED`, `TASK_STATE_AUTH_REQUIRED`), and terminal (`TASK_STATE_COMPLETED`, `TASK_STATE_FAILED`, `TASK_STATE_CANCELED`, `TASK_STATE_REJECTED`). Terminal tasks reject new messages with `UnsupportedOperationError`. The exact enum spellings are as reported by the summary and should be checked.
- Message: `role` is `ROLE_USER` or `ROLE_AGENT`, plus `parts`, `messageId`, `taskId`, `contextId`, `referenceTaskIds`, `extensions`, and `metadata`. Messages carry communication, not outputs, and not all messages are persisted in task history.
- Part: `text`, `raw` (inline bytes), or `url`, with `filename`, `mediaType`, and `metadata`. Structured data is JSON text with `mediaType` `application/json`, and the schema goes in `metadata`.
- Artifact: `artifactId`, `name`, `parts`, `extensions`, `metadata`.
- AgentCard: served at `/.well-known/agent-card.json`, and also available through registries or direct configuration. Fields include `supportedInterfaces` (each with `url`, `protocolBinding`, `protocolVersion`, ordered by preference), `capabilities` (`streaming`, `pushNotifications`, `extendedAgentCard`, `extensions`), `skills`, `securitySchemes` (API key, HTTP auth, OAuth 2.0 flows, OpenID Connect, mutual TLS), and JWS `signatures`.
- Bindings: JSON-RPC, gRPC, and HTTP+JSON/REST, which must expose equivalent operations and error mapping. Example: `TaskNotFoundError` maps to JSON-RPC `-32001`, gRPC `NOT_FOUND`, and HTTP `404`.
- Methods (HTTP+JSON mapping): `POST /message:send` (SendMessage), `POST /message:stream` (SendStreamingMessage), `GET /tasks/{id}`, `GET /tasks` (cursor pagination), `POST /tasks/{id}:cancel`, `POST /tasks/{id}:subscribe` (first event is the current Task), push-config CRUD under `/tasks/{id}/pushNotificationConfigs`, and `GET /extendedAgentCard`.
- Blocking vs. non-blocking is set by `return_immediately` in `SendMessageConfiguration`. Non-blocking calls return an in-progress task, and the client then polls, subscribes, or uses a webhook.
- Push notifications POST a `StreamResponse` (one of `task`, `message`, `statusUpdate`, `artifactUpdate`). Delivery is at least once, so receivers must be idempotent.
- Input-required: the client continues by sending a message with the same `taskId` and `contextId`.
- Auth-required: credentials should normally arrive out-of-band. The spec says, "Agents MUST NOT treat the TASK_STATE_AUTH_REQUIRED state transition, by itself, as authorization for any particular operation," and the scope of any resulting credential is left to the implementation.

MCP draft (the page shows `2026-07-28` only as an example `protocolVersion` value, so the spec version is not confirmed) [MCP specification draft, basic](https://raw.githubusercontent.com/modelcontextprotocol/modelcontextprotocol/main/docs/specification/draft/basic/index.mdx):
- JSON-RPC 2.0 with three message types. Requests carry a non-null, unique ID. Result responses include a required `resultType` of `"complete"` or `"input_required"`. Errors include an integer `code`, `message`, and optional `data`. Notifications have no ID and receive no response.
- The spec reserves error codes `-32020` to `-32099`. Examples: `-32020` HeaderMismatch, `-32021` MissingRequiredClientCapability, `-32022` UnsupportedProtocolVersion. A missing required field gives `-32602`.
- Each request must carry its own context in `_meta` (`protocolVersion`, `clientCapabilities`). Servers must not rely on earlier requests or connection identity, and any cross-request state needs an explicit identifier.
- Message patterns include request/response, multi round-trip requests (MRTR) for server-initiated input, and subscribe/notify.
- Server features are tools, resources, and prompts. Client features are elicitation, sampling, and roots.
- The page states: "The spec defines none" for agent-to-agent communication. It covers only messaging between MCP clients and servers.

Anthropic multi-agent post on the subagent contract [Anthropic, multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system): each subagent task needs an objective, an output format, guidance on tools and sources, and clear boundaries. Vague instructions caused duplicated work. The lead saves its plan to memory because context beyond 200,000 tokens gets truncated.

### Inferences
- For a cross-provider app, A2A supplies what is missing elsewhere: a Task with `contextId`/`taskId` for shared goal tracking, AgentCard capability discovery, and interruption states. MCP does not provide this, so using MCP for agent-to-agent calls would mean inventing the protocol.
- MCP is the right layer for each agent's tools (databases, search, files). Using A2A for agent-to-agent and MCP for agent-to-tool keeps the layers separate.
- The MCP draft's per-request statelessness and `resultType: "input_required"` resemble A2A's interruption model. If both are used, the app should map MCP's input-required results to A2A task states, but this is an inference, not a documented mapping.
- Output from remote agents should be carried as A2A Artifacts with `application/json` Parts and a schema in `metadata`, since no separate typed result object is defined.

### Gaps
- A2A: no release date for version 1.0.0 was visible. The fetch read about 100,000 of about 158,000 characters, so the rest of the spec (including the full Section 8.5 and Section 7.6 detail) was not read.
- A2A: the enum spellings and method names come from a summary of the page. Older A2A versions used different names, which I did not verify here.
- MCP: the page did not name stdio or Streamable HTTP as transports in the fetched text. The 2025-11-25 stable spec is not fetched here, so the handshake and initialization behavior of the stable version is unverified. The draft describes per-request capability negotiation, which may not match the current stable release.
- No source here shows A2A and MCP interoperating in a working multi-provider system.

## Structured message schemas used in practice

### Takeaway
The schemas that appear across these sources share a pattern: a message with role and typed parts, a task or handoff object with an ID and a status enum, and a result object that is either an Artifact or a compressed summary. The schemas differ in whether outputs are typed (OpenAI `input_type`, A2A `application/json` Parts) or free text (Anthropic subagent summaries).

### Cited Findings
- OpenAI `input_type` on a handoff becomes the handoff tool's `parameters`. The SDK validates the model's JSON locally and passes the parsed value to `on_handoff`. It does not change the receiving agent's input or destination. [OpenAI Agents SDK, Handoffs](https://raw.githubusercontent.com/openai/openai-agents-python/main/docs/handoffs.md)
- OpenAI `on_handoff` can raise an exception to block a transfer. `is_enabled` runs before the model returns arguments, so argument-based checks belong at the start of `on_handoff`. Tool input guardrails do not apply to handoffs. [OpenAI Agents SDK, Handoffs](https://raw.githubusercontent.com/openai/openai-agents-python/main/docs/handoffs.md)
- LangGraph `interrupt(value, *, response_schema=None)` converts a Pydantic model, `TypedDict`, or dataclass to JSON Schema for clients and validates the resume value. On validation failure, a `pydantic.ValidationError` is raised, and nothing is saved, so the interrupt can be answered again. [LangGraph types.py](https://raw.githubusercontent.com/langchain-ai/langgraph/main/libs/langgraph/langgraph/types.py)
- AutoGen message types referenced in source include `BaseChatMessage` and `HandoffMessage`, and a handoff message carries context that is added to the model context. [AutoGen SelectorGroupChat source](https://raw.githubusercontent.com/microsoft/autogen/main/python/packages/autogen-agentchat/src/autogen_agentchat/teams/_group_chat/_selector_group_chat.py)
- A2A Task, Message, Part, and Artifact structures are described above. [A2A specification, main branch](https://raw.githubusercontent.com/a2aproject/A2A/main/docs/specification.md)
- Anthropic subagents return compressed findings to the lead. For some outputs (code, reports, visualizations), subagents store work externally and return lightweight references, which avoids copying large outputs through the coordinator. [Anthropic, multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system)

### Inferences
- A minimal cross-provider task-object schema would combine A2A's Task fields (`id`, `contextId`, `status.state`) with typed Parts for input and Artifacts for outputs, plus a JSON Schema per skill. This is a design suggestion, not something a single source documents.
- Reference-passing for large outputs (Anthropic) pairs well with A2A Artifacts that carry `url` Parts rather than inline bytes.

### Gaps
- CrewAI's `output_pydantic` and `TaskOutput` could not be verified.
- No source specifies a standard "result object" for cross-provider agent output beyond A2A Artifacts.
- The LangGraph state and reducer schema were not verified in this pass (see the framework section).

## Human observation and intervention

### Takeaway
The sources document three intervention points: a protocol-level pause that waits for input (A2A `input-required` / MCP `input_required`), a graph-level interrupt with a checkpointer (LangGraph), and a gate that can block a transfer or speaker choice (OpenAI `on_handoff` / `is_enabled`, AutoGen `selector_func`). Observability is documented only at a high level.

### Cited Findings
- A2A: a task in `TASK_STATE_INPUT_REQUIRED` waits for a client message on the same `taskId` and `contextId`. The client can subscribe, register a webhook, or poll to avoid missing updates. [A2A specification, main branch](https://raw.githubusercontent.com/a2aproject/A2A/main/docs/specification.md)
- LangGraph: `interrupt()` pauses execution and surfaces a value to the client. The client resumes with `Command(resume=...)`, and the node re-executes from its start. [LangGraph types.py](https://raw.githubusercontent.com/langchain-ai/langgraph/main/libs/langgraph/langgraph/types.py)
- AutoGen: `selector_func` can return a fixed speaker or `None` to fall back to the model. `save_state` and `load_state` persist the thread for later resumption. [AutoGen SelectorGroupChat source](https://raw.githubusercontent.com/microsoft/autogen/main/python/packages/autogen-agentchat/src/autogen_agentchat/teams/_group_chat/_selector_group_chat.py)
- OpenAI: input guardrails run only on the first agent, and output guardrails only on the final-output agent. Handoff gating happens in `is_enabled` and `on_handoff`. [OpenAI Agents SDK, Handoffs](https://raw.githubusercontent.com/openai/openai-agents-python/main/docs/handoffs.md)
- Anthropic describes production tracing for diagnosing agent failures, with monitoring designed to avoid logging conversation contents for privacy. Agents resume from the point of failure rather than restarting. [Anthropic, multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system)
- MCP: the draft names elicitation as a client feature. The fetched text does not describe its semantics further. [MCP specification draft, basic](https://raw.githubusercontent.com/modelcontextprotocol/modelcontextprotocol/main/docs/specification/draft/basic/index.mdx)

### Inferences
- A human-observable multi-agent system needs an append-only log of messages with task IDs, plus a way to pause at a defined state. A2A's task states plus LangGraph-style checkpointing cover this better than free-form group chat.
- Intervention in group chat is harder to scope than in task-based protocols, because the human can only inject messages into a shared thread.

### Gaps
- AutoGen's `UserProxyAgent` and human-input flow were not verified in this pass.
- No source here describes a human-facing observation UI or the event schema for observing a running A2A task.
- OpenAI's run-loop termination and tracing UI were not verified.
