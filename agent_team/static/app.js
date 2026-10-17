// The session token arrives in the page URL once, then leaves the address bar.
const token = new URLSearchParams(location.search).get("token") || "";
history.replaceState(null, "", "/");

const FINISHED = new Set(["done", "idle", "max_turns", "timeout", "token_budget", "stalled", "stopped", "failed", "interrupted"]);
const EVENT_KINDS = ["message", "turn", "human", "final", "error", "status"];
const $ = (id) => document.getElementById(id);

let stream = null;
let currentRunId = null;

async function api(method, path, body) {
  const response = await fetch(`/api${path}`, {
    method,
    headers: { "X-Session-Token": token, "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || response.statusText);
  return data;
}

async function attempt(action) {
  $("notice").textContent = "";
  try {
    await action();
  } catch (error) {
    $("notice").textContent = error.message;
  }
}

function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function renderKeys(keys) {
  const list = $("key-list");
  list.replaceChildren();
  if (Object.keys(keys).length === 0) list.append(el("li", "No keys yet.", "muted"));
  for (const [name, info] of Object.entries(keys)) {
    const item = el("li");
    const where = info.persisted ? "saved" : "this session only";
    item.append(el("strong", name), el("span", `${info.hint} · ${where}`, "muted"));
    const remove = el("button", "Remove", "secondary");
    remove.addEventListener("click", () =>
      attempt(async () => renderKeys((await api("DELETE", `/keys/${encodeURIComponent(name)}`)).keys))
    );
    item.append(remove);
    list.append(item);
  }
}

async function loadRuns() {
  const { runs } = await api("GET", "/runs");
  const list = $("run-list");
  list.replaceChildren();
  if (runs.length === 0) list.append(el("li", "No runs yet.", "muted"));
  for (const run of runs) {
    const item = el("li");
    const open = el("button", `${run.goal.slice(0, 70)} (${run.status})`, "link");
    open.addEventListener("click", () => openRun(run.id, run.goal, run.status));
    item.append(open);
    list.append(item);
  }
}

async function loadState() {
  const state = await api("GET", "/providers");
  renderKeys(state.keys);
  if (!$("config").value) $("config").value = JSON.stringify(state.default_config, null, 2);
  await loadRuns();
}

function openRun(id, goal, status) {
  if (stream) stream.close();
  currentRunId = id;
  $("run-title").textContent = goal;
  $("run-status").textContent = `Status: ${status}`;
  $("feed").replaceChildren();
  stream = new EventSource(`/api/runs/${id}/events?token=${encodeURIComponent(token)}`);
  for (const kind of EVENT_KINDS) {
    stream.addEventListener(kind, (event) => render(kind, JSON.parse(event.data), id));
  }
}

function render(kind, data, runId) {
  if (runId !== currentRunId) return;
  if (kind === "status") {
    $("run-status").textContent = `Status: ${data.status}`;
    if (FINISHED.has(data.status)) {
      stream.close();
      attempt(loadRuns);
    }
    return;
  }
  const item = el("li", undefined, `event ${kind}`);
  if (kind === "message") {
    item.append(el("div", `${data.sender} → ${data.recipient}`, "who"), el("div", data.text, "text"));
  } else if (kind === "turn") {
    const cut = data.truncated ? " · reply cut off by the output limit" : "";
    item.append(el("div", `${data.agent} used ${data.tokens} tokens (run total ${data.total_tokens})${cut}`, "muted"));
  } else if (kind === "human") {
    item.append(el("div", "You", "who"), el("div", data.text, "text"));
  } else if (kind === "final") {
    item.append(el("div", "Final answer", "who"), el("div", data.answer, "text"));
  } else if (kind === "error") {
    item.append(el("div", data.message, "error"));
  }
  $("feed").append(item);
  item.scrollIntoView({ block: "end" });
}

$("key-form").addEventListener("submit", (event) => {
  event.preventDefault();
  attempt(async () => {
    const data = await api("POST", "/keys", {
      provider: $("key-provider").value.trim(),
      key: $("key-value").value,
      persist: $("key-persist").checked,
    });
    $("key-value").value = "";
    renderKeys(data.keys);
  });
});

$("start").addEventListener("click", () =>
  attempt(async () => {
    let config;
    try {
      config = JSON.parse($("config").value);
    } catch (error) {
      throw new Error(`The team config is not valid JSON: ${error.message}`);
    }
    const goal = $("goal").value.trim();
    const { id } = await api("POST", "/runs", { config, goal });
    await loadRuns();
    openRun(id, goal, "running");
  })
);

$("human-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const text = $("human-text").value.trim();
  if (!text || !currentRunId) return;
  attempt(async () => {
    await api("POST", `/runs/${currentRunId}/message`, { text });
    $("human-text").value = "";
  });
});

$("stop").addEventListener("click", () => {
  if (currentRunId) attempt(() => api("POST", `/runs/${currentRunId}/stop`, {}));
});

attempt(loadState);
