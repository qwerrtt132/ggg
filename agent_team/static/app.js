// The session token arrives in the page URL once, then leaves the address bar.
const token = new URLSearchParams(location.search).get("token") || "";
history.replaceState(null, "", "/");

const FINISHED = new Set(["done", "idle", "max_turns", "timeout", "token_budget", "stalled", "stopped", "failed", "interrupted", "no_progress"]);
const EVENT_KINDS = ["roster", "agent", "message", "turn", "human", "final", "error", "status", "rewind", "branched", "approval", "approval_result"];
const NAME_RULE = /^[a-z][a-z0-9_-]{0,31}$/;
const DRAFT_KEY = "agent-team-draft";
const TOOL_GROUPS = [
  ["files", "Files in the run's workspace"],
  ["fetch", "Fetch web pages (public addresses only)"],
];
const $ = (id) => document.getElementById(id);

const state = {
  keys: {},
  envKeys: {},
  defaultConfig: null,
  team: null,
  runId: null,
  goal: "",
  finished: false,
  status: "",
  connection: "",
  stream: null,
  maxTokens: null,
  tokensUsed: 0,
  selectedTurn: null,
  rewindArmed: false,
};
let cards = new Map();

// ---------- plumbing ----------

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

function showNotice(text, kind = "error") {
  const notice = $("notice");
  notice.textContent = text;
  notice.className = `notice ${kind}`;
}

async function attempt(action) {
  showNotice("");
  try {
    await action();
  } catch (error) {
    showNotice(error.message);
  }
}

function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function loadDraft() {
  try {
    const raw = localStorage.getItem(DRAFT_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function saveDraft() {
  try {
    localStorage.setItem(DRAFT_KEY, JSON.stringify(state.team));
  } catch {
    // Storage can be blocked. The team still works without the draft.
  }
}

// ---------- keys ----------

function keyStatus(provider) {
  if (state.keys[provider]) return state.keys[provider].persisted ? "saved" : "this session";
  if (state.envKeys[provider]) return "environment";
  return "missing";
}

function modelFor(provider) {
  const members = [state.team.lead, ...state.team.teammates];
  return members.find((member) => member.provider === provider)?.model || "";
}

function renderKeys() {
  const list = $("key-list");
  list.replaceChildren();
  const names = [...new Set([...Object.keys(state.team.providers), ...Object.keys(state.keys)])].sort();
  if (names.length === 0) list.append(el("li", "No providers in the team yet.", "muted"));
  for (const name of names) {
    const status = keyStatus(name);
    const hint = state.keys[name]?.hint;
    const item = el("li");
    item.append(
      el("strong", name),
      el("span", hint ? `${status} · ${hint}` : status, status === "missing" ? "error" : "muted")
    );
    const actions = el("span", undefined, "actions");
    const test = el("button", "Test", "secondary");
    test.addEventListener("click", () => testKey(name));
    const add = el("button", status === "missing" ? "Add key" : "Replace", "secondary");
    add.addEventListener("click", () => {
      $("keys-details").open = true;
      $("key-provider").value = name;
      $("key-value").focus();
    });
    actions.append(test, add);
    if (state.keys[name]) {
      const remove = el("button", "Remove", "secondary");
      remove.addEventListener("click", () =>
        attempt(async () => {
          state.keys = (await api("DELETE", `/keys/${encodeURIComponent(name)}`)).keys;
          renderKeys();
        })
      );
      actions.append(remove);
    }
    item.append(actions);
    list.append(item);
  }
  renderReadiness();
}

function testKey(name) {
  const model = modelFor(name);
  if (!model) {
    showNotice(`Add a team member that uses ${name} first, so there is a model to test with.`);
    return;
  }
  attempt(async () => {
    const data = await api("POST", `/providers/${encodeURIComponent(name)}/test`, {
      model,
      spec: state.team.providers[name],
    });
    showNotice(`${name} works with ${model}. Reply: "${data.reply}"`, "ok");
  });
}

// ---------- team editor ----------

function labelled(text, control) {
  const wrapper = el("label");
  wrapper.append(el("span", text, "field-label"), control);
  return wrapper;
}

function textField(value, onInput, placeholder = "") {
  const input = document.createElement("input");
  input.type = "text";
  input.value = value ?? "";
  input.placeholder = placeholder;
  input.addEventListener("input", () => onInput(input.value));
  return input;
}

function memberRow(member, title, index) {
  const row = el("div", undefined, "member");
  const heading = el("div", undefined, "member-title");
  heading.append(el("strong", title));
  if (index !== null) {
    const remove = el("button", "Remove", "secondary");
    remove.addEventListener("click", () => {
      const [gone] = state.team.teammates.splice(index, 1);
      if (state.team.verifier === gone.name) delete state.team.verifier;
      renderTeam();
    });
    heading.append(remove);
  }
  row.append(heading);

  row.append(
    labelled(
      "Name",
      textField(member.name, (value) => {
        member.name = value.trim();
        renderVerifierOptions();
        changed();
      }, "researcher")
    )
  );

  const role = document.createElement("textarea");
  role.rows = 2;
  role.value = member.role ?? "";
  role.placeholder = "What this agent is responsible for";
  role.addEventListener("input", () => {
    member.role = role.value;
    changed();
  });
  row.append(labelled("Role", role));

  const provider = document.createElement("select");
  for (const name of Object.keys(state.team.providers)) provider.append(new Option(name, name));
  provider.value = member.provider;
  provider.addEventListener("change", () => {
    member.provider = provider.value;
    changed();
  });
  row.append(labelled("Provider", provider));

  row.append(
    labelled(
      "Model",
      textField(member.model, (value) => {
        member.model = value.trim();
        changed();
      }, "model name")
    )
  );

  const tools = el("fieldset", undefined, "tools");
  tools.append(el("legend", "Tools", "field-label"));
  for (const [group, description] of TOOL_GROUPS) {
    const box = document.createElement("input");
    box.type = "checkbox";
    box.checked = (member.tools || []).includes(group);
    box.addEventListener("change", () => {
      const chosen = new Set(member.tools || []);
      if (box.checked) chosen.add(group);
      else chosen.delete(group);
      member.tools = [...chosen];
      if (!member.tools.includes("files")) member.approve = [];
      changed();
    });
    const option = el("label", undefined, "inline");
    option.append(box, document.createTextNode(` ${description}`));
    tools.append(option);
  }
  const askFirst = document.createElement("input");
  askFirst.type = "checkbox";
  askFirst.checked = (member.approve || []).includes("write_file");
  askFirst.addEventListener("change", () => {
    member.approve = askFirst.checked ? ["write_file"] : [];
    changed();
  });
  const askLabel = el("label", undefined, "inline");
  askLabel.append(askFirst, document.createTextNode(" Ask me before a file is written"));
  tools.append(askLabel);
  row.append(tools);
  return row;
}

function renderVerifierOptions() {
  const select = $("verifier");
  const current = state.team.verifier || "";
  select.replaceChildren(new Option("No verifier", ""));
  for (const member of state.team.teammates) {
    if (member.name) select.append(new Option(member.name, member.name));
  }
  select.value = [...select.options].some((option) => option.value === current) ? current : "";
}

function renderTeam() {
  const team = state.team;
  const members = $("members");
  members.replaceChildren(memberRow(team.lead, "Lead", null));
  team.teammates.forEach((member, index) => members.append(memberRow(member, `Teammate ${index + 1}`, index)));
  renderVerifierOptions();
  $("max-turns").value = team.max_turns ?? "";
  $("max-seconds").value = team.max_seconds ?? "";
  $("max-tokens").value = team.max_tokens ?? "";
  if (document.activeElement !== $("config")) {
    $("config").value = JSON.stringify(team, null, 2);
    $("config-error").textContent = "";
  }
  changed();
}

function changed() {
  saveDraft();
  renderKeys();
  renderReadiness();
  if (document.activeElement !== $("config")) $("config").value = JSON.stringify(state.team, null, 2);
}

function problems() {
  const team = state.team;
  const members = [team.lead, ...team.teammates];
  const issues = [];
  for (const member of members) {
    if (!NAME_RULE.test(member.name || "")) {
      issues.push(`“${member.name || ""}” is not a valid name. Use lowercase letters, digits, - or _, starting with a letter.`);
    }
    if (!member.role?.trim()) issues.push(`${member.name || "A member"} needs a role.`);
    if (!member.model?.trim()) issues.push(`${member.name || "A member"} needs a model.`);
  }
  const names = members.map((member) => member.name);
  if (new Set(names).size !== names.length) issues.push("Team member names must be unique.");
  for (const provider of new Set(members.map((member) => member.provider))) {
    if (keyStatus(provider) === "missing") issues.push(`No API key for ${provider} yet. Add one in API keys.`);
  }
  if (!$("goal").value.trim()) issues.push("Write a goal for the team.");
  return issues;
}

function renderReadiness() {
  const issues = problems();
  const box = $("readiness");
  box.replaceChildren(...issues.map((issue) => el("p", issue, "error")));
  $("start").disabled = issues.length > 0;
}

function setLimit(key, value) {
  const number = parseInt(value, 10);
  if (Number.isFinite(number)) state.team[key] = number;
  else if (key === "max_tokens") state.team[key] = null; // an empty budget means no budget, not the default
  else delete state.team[key];
  changed();
}

// ---------- runs ----------

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

function openRun(id, goal, status) {
  const sameRun = state.runId === id;
  if (state.stream) state.stream.close();
  $("export-md").href = `/api/runs/${id}/export?format=md&token=${encodeURIComponent(token)}`;
  $("export-json").href = `/api/runs/${id}/export?format=json&token=${encodeURIComponent(token)}`;
  state.runId = id;
  state.goal = goal;
  state.status = status;
  state.finished = FINISHED.has(status);
  state.connection = "connecting";
  state.tokensUsed = 0;
  if (!sameRun) {
    // A different run: forget the selected turn and any approvals from the last run.
    state.selectedTurn = null;
    state.rewindArmed = false;
    $("time-actions").hidden = true;
    $("timeline").replaceChildren();
    pendingApprovals.clear();
    renderApprovals();
  }
  scheduleTimeline();
  $("run-title").textContent = goal;
  $("feed").replaceChildren();
  $("cards").replaceChildren();
  cards = new Map();
  $("meter").hidden = true;
  refreshRunStatus();
  updateControls();

  const stream = new EventSource(`/api/runs/${id}/events?token=${encodeURIComponent(token)}`);
  state.stream = stream;
  stream.onopen = () => {
    if (state.stream !== stream) return;
    state.connection = "live";
    refreshRunStatus();
  };
  stream.onerror = () => {
    if (state.stream !== stream || stream.readyState === EventSource.CLOSED) return;
    state.connection = "reconnecting";
    refreshRunStatus();
  };
  for (const kind of EVENT_KINDS) {
    stream.addEventListener(kind, (event) => {
      if (state.stream === stream) render(kind, JSON.parse(event.data));
    });
  }
  stream.addEventListener("live", (event) => {
    if (state.stream === stream) updateLive(JSON.parse(event.data));
  });
  stream.addEventListener("end", () => {
    if (state.stream !== stream) return;
    stream.close();
    state.connection = "closed";
    refreshRunStatus();
  });
}

function refreshRunStatus() {
  const connection = state.connection === "live" ? "" : ` · ${state.connection}`;
  $("run-status").textContent = `Status: ${state.status}${connection}`;
}

function updateControls() {
  $("stop").disabled = !state.runId || state.finished;
  $("human-text").placeholder = state.finished
    ? "This run has finished. Send a message to continue it with the same agents."
    : "Message the lead during the run";
  $("rewind").disabled = !state.finished || state.selectedTurn === null;
  $("branch").disabled = state.selectedTurn === null;
}

// ---------- turns: timeline, rewind, and branch ----------

let timelineTimer = null;

function scheduleTimeline() {
  clearTimeout(timelineTimer);
  timelineTimer = setTimeout(() => attempt(async () => {
    await loadTimeline();
    await loadChanges();
  }), 300);
}

// ---------- approvals and file review ----------

const pendingApprovals = new Map();

function renderApprovals() {
  const box = $("approvals");
  box.replaceChildren();
  for (const approval of pendingApprovals.values()) {
    const card = el("div", undefined, "approval");
    card.append(el("div", `${approval.agent} wants to run ${approval.tool}`, "who"));
    const shown = el("pre", JSON.stringify(approval.arguments, null, 2), "args");
    card.append(shown);

    const decide = (body) => attempt(async () => {
      await api("POST", `/runs/${state.runId}/approvals/${encodeURIComponent(approval.id)}`, body);
      pendingApprovals.delete(approval.id);
      renderApprovals();
    });

    const actions = el("div", undefined, "inline-form");
    const approve = el("button", "Approve", "approve");
    approve.addEventListener("click", () => decide({ action: "approve" }));

    const edit = el("button", "Edit, then approve", "secondary edit");
    const editor = document.createElement("textarea");
    editor.className = "mono";
    editor.rows = 6;
    editor.value = JSON.stringify(approval.arguments, null, 2);
    editor.hidden = true;
    const editApprove = el("button", "Approve with these changes", "approve edited");
    editApprove.hidden = true;
    editApprove.addEventListener("click", () => {
      let arguments_;
      try {
        arguments_ = JSON.parse(editor.value);
      } catch (error) {
        showNotice(`The changed arguments are not valid JSON: ${error.message}`);
        return;
      }
      decide({ action: "approve", arguments: arguments_ });
    });
    edit.addEventListener("click", () => {
      editor.hidden = false;
      editApprove.hidden = false;
      editor.focus();
    });

    const reason = document.createElement("input");
    reason.className = "reason";
    reason.placeholder = "Why not? (sent to the agent)";
    const reject = el("button", "Reject", "secondary reject");
    reject.addEventListener("click", () => decide({ action: "reject", reason: reason.value.trim() }));

    actions.append(approve, edit, reject);
    card.append(actions, editor, editApprove, reason);
    box.append(card);
  }
  $("approvals-section").hidden = pendingApprovals.size === 0;
}

async function loadChanges() {
  if (!state.runId) return;
  const runId = state.runId;
  const { changes } = await api("GET", `/runs/${runId}/changes`);
  if (state.runId !== runId) return;
  $("export-accepted").href = `/api/runs/${runId}/workspace.zip?token=${encodeURIComponent(token)}`;
  const list = $("changes");
  list.replaceChildren();
  if (changes.length === 0) {
    list.append(el("li", "Nothing waiting for review.", "muted"));
    return;
  }
  for (const change of changes) {
    const item = el("li", undefined, "change");
    item.append(el("strong", change.path), el("span", ` ${change.status}`, "muted"));
    if (change.diff) item.append(el("pre", change.diff, "diff"));
    else item.append(el("p", "This file cannot be shown as text. Accept or reject it by name.", "muted"));
    const decide = (action) => attempt(async () => {
      await api("POST", `/runs/${runId}/changes`, { path: change.path, action });
      await loadChanges();
    });
    const accept = el("button", "Accept", "accept");
    accept.disabled = !state.finished;
    accept.addEventListener("click", () => decide("accept"));
    const reject = el("button", "Reject", "secondary reject");
    reject.disabled = !state.finished;
    reject.addEventListener("click", () => decide("reject"));
    item.append(accept, reject);
    list.append(item);
  }
}

async function loadTimeline() {
  if (!state.runId) return;
  const runId = state.runId;
  const { checkpoints } = await api("GET", `/runs/${runId}/checkpoints`);
  if (state.runId !== runId) return;
  const list = $("timeline");
  list.replaceChildren();
  if (checkpoints.length === 0) list.append(el("li", "No turns yet.", "muted"));
  for (const point of checkpoints) {
    const item = el("li");
    const button = el("button", `Turn ${point.turn} · ${point.agent || "?"}`, "turn-dot secondary");
    button.setAttribute("aria-pressed", String(state.selectedTurn === point.turn));
    button.addEventListener("click", () => selectTurn(point.turn, point.agent, point.tokens_used));
    item.append(button);
    list.append(item);
  }
  updateControls();
}

function selectTurn(turn, agent, tokens) {
  state.selectedTurn = turn;
  state.rewindArmed = false;
  $("rewind").textContent = "Rewind here";
  $("time-actions").hidden = false;
  $("time-selected").textContent = `Turn ${turn} · ${agent || "?"} · ${(tokens || 0).toLocaleString()} tokens used up to here`;
  $("time-note").textContent = "";
  for (const button of $("timeline").querySelectorAll("button")) {
    button.setAttribute("aria-pressed", String(button.textContent.startsWith(`Turn ${turn} `)));
  }
  updateControls();
}

$("rewind").addEventListener("click", () => {
  const message = $("time-message").value.trim();
  if (!message) {
    $("time-note").textContent = "Write the message to continue with first.";
    return;
  }
  if (!state.rewindArmed) {
    state.rewindArmed = true;
    $("rewind").textContent = `Confirm: rewind to turn ${state.selectedTurn}`;
    $("time-note").textContent = "Turns after this one are dropped from the checkpoints. Their events stay in the history.";
    return;
  }
  attempt(async () => {
    await api("POST", `/runs/${state.runId}/rewind`, { turn: state.selectedTurn, message });
    state.rewindArmed = false;
    $("rewind").textContent = "Rewind here";
    $("time-message").value = "";
    openRun(state.runId, state.goal, "running");
  });
});

$("branch").addEventListener("click", () => {
  const message = $("time-message").value.trim();
  if (!message) {
    $("time-note").textContent = "Write the message to continue with first.";
    return;
  }
  attempt(async () => {
    const { id } = await api("POST", `/runs/${state.runId}/branch`, { turn: state.selectedTurn, message });
    $("time-message").value = "";
    await loadRuns();
    openRun(id, state.goal, "running");
    $("time-note").textContent = "Started a branch on the right. The original run is unchanged.";
  });
});

function render(kind, data) {
  if (kind === "roster") return renderRoster(data);
  if (kind === "agent") return updateAgent(data);
  if (kind === "status") return finishRun(data.status);
  if (kind === "approval") {
    pendingApprovals.set(data.id, data);
    return renderApprovals();
  }
  if (kind === "approval_result") {
    pendingApprovals.delete(data.id);
    renderApprovals();
    return scheduleTimeline();
  }
  if (kind === "turn") {
    scheduleTimeline();
    return updateTurn(data);
  }

  const feed = $("feed");
  const nearBottom = feed.scrollHeight - feed.scrollTop - feed.clientHeight < 80;
  const item = el("li", undefined, `event ${kind}`);
  if (kind === "message") {
    item.style.setProperty("--hue", hue(data.sender));
    item.append(el("div", `${data.sender} → ${data.recipient}`, "who"), el("div", data.text, "text"));
  } else if (kind === "human") {
    item.append(el("div", "You", "who"), el("div", data.text, "text"));
  } else if (kind === "final") {
    item.append(el("div", "Final answer", "who"), el("div", data.answer, "text"));
  } else if (kind === "error") {
    item.append(el("div", data.message, "error"));
  } else if (kind === "rewind") {
    item.append(el("div", `Rewound to turn ${data.to_turn}. Later turns were dropped from the checkpoints.`, "who"));
  } else if (kind === "branched") {
    item.append(el("div", `Branched from another run at turn ${data.turn}.`, "who"));
  }
  feed.append(item);
  if (nearBottom || kind === "human") item.scrollIntoView({ block: "end" });
}

function finishRun(status) {
  state.status = status;
  state.finished = FINISHED.has(status);
  refreshRunStatus();
  updateControls();
  attempt(loadRuns);
  scheduleTimeline();
  if (state.finished) {
    pendingApprovals.clear();
    renderApprovals();
  }
}

function renderRoster(roster) {
  state.maxTokens = roster.max_tokens ?? null;
  cards = new Map();
  $("cards").replaceChildren();
  for (const member of roster.members) {
    const role = member.name === roster.lead ? "lead" : member.name === roster.verifier ? "verifier" : "teammate";
    const card = el("li", undefined, "card");
    card.dataset.state = "idle";
    card.style.setProperty("--hue", hue(member.name));
    const stateLine = el("div", "idle", "card-state");
    const tokens = el("div", "0 tokens", "muted");
    const preview = el("div", "", "preview");
    card.append(
      el("div", member.name, "card-title"),
      el("div", `${role} · ${member.model} · ${member.provider}`, "muted"),
      stateLine,
      preview,
      tokens
    );
    $("cards").append(card);
    cards.set(member.name, { card, stateLine, tokens, preview, used: 0 });
  }
  $("meter").hidden = false;
  updateMeter();
}

function updateAgent({ name, state: agentState }) {
  const entry = cards.get(name);
  if (!entry) return;
  entry.card.dataset.state = agentState;
  entry.stateLine.textContent = agentState;
  if (agentState === "idle") entry.preview.textContent = "";
}

// Live text is not part of the saved feed. It shows the end of the reply as it streams.
function updateLive({ agent, text }) {
  const entry = cards.get(agent);
  if (entry) entry.preview.textContent = text.length > 280 ? `…${text.slice(-280)}` : text;
}

function updateTurn(data) {
  const entry = cards.get(data.agent);
  if (entry) {
    entry.used += data.tokens;
    entry.tokens.textContent = `${entry.used.toLocaleString()} tokens${data.truncated ? " · last reply cut off" : ""}`;
  }
  state.tokensUsed = data.total_tokens;
  updateMeter();
}

function updateMeter() {
  const meter = $("token-meter");
  const used = state.tokensUsed;
  if (state.maxTokens) {
    meter.max = state.maxTokens;
    meter.value = Math.min(used, state.maxTokens);
    $("token-text").textContent = `${used.toLocaleString()} of ${state.maxTokens.toLocaleString()} tokens`;
  } else {
    meter.max = Math.max(used, 1);
    meter.value = used;
    $("token-text").textContent = `${used.toLocaleString()} tokens used (no budget set)`;
  }
}

function hue(name) {
  let total = 0;
  for (const character of name) total = (total * 31 + character.charCodeAt(0)) % 360;
  return total;
}

// ---------- forms and buttons ----------

async function loadTemplates() {
  const { templates } = await api("GET", "/templates");
  state.templates = templates;
  const select = $("template-select");
  select.replaceChildren(new Option("Choose a template…", ""));
  for (const template of templates) select.append(new Option(template.name, template.id));
}

async function loadPresets() {
  const { presets } = await api("GET", "/presets");
  const select = $("preset-select");
  select.replaceChildren(new Option(presets.length ? "Choose a saved team…" : "No saved teams yet", ""));
  for (const preset of presets) select.append(new Option(preset.name, preset.name));
}

async function loadState() {
  const data = await api("GET", "/providers");
  state.keys = data.keys;
  state.envKeys = data.env_keys;
  state.defaultConfig = data.default_config;
  state.team = loadDraft() || structuredClone(data.default_config);
  renderTeam();
  await Promise.all([loadRuns(), loadTemplates(), loadPresets()]);
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
    state.keys = data.keys;
    renderKeys();
    showNotice("Key saved.", "ok");
  });
});

$("add-member").addEventListener("click", () => {
  const taken = new Set([state.team.lead.name, ...state.team.teammates.map((member) => member.name)]);
  let number = state.team.teammates.length + 2;
  while (taken.has(`agent_${number}`)) number += 1;
  state.team.teammates.push({
    name: `agent_${number}`,
    role: "",
    provider: Object.keys(state.team.providers)[0],
    model: state.team.lead.model,
  });
  renderTeam();
});

$("reset-team").addEventListener("click", () => {
  state.team = structuredClone(state.defaultConfig);
  renderTeam();
});

$("template-select").addEventListener("change", (event) => {
  const template = state.templates.find((item) => item.id === event.target.value);
  event.target.value = "";
  if (!template) return;
  const taken = new Set([state.team.lead.name, ...state.team.teammates.map((member) => member.name)]);
  let name = template.id;
  let number = 2;
  while (taken.has(name)) name = `${template.id}_${number++}`;
  state.team.teammates.push({
    name,
    role: template.role,
    provider: Object.keys(state.team.providers)[0],
    model: state.team.lead.model,
    tools: template.tools,
    approve: template.approve || [],
  });
  renderTeam();
});

$("preset-load").addEventListener("click", () => {
  const name = $("preset-select").value;
  if (!name) return;
  attempt(async () => {
    state.team = (await api("GET", `/presets/${encodeURIComponent(name)}`)).config;
    renderTeam();
    showNotice(`Loaded “${name}”.`, "ok");
  });
});

$("preset-delete").addEventListener("click", () => {
  const name = $("preset-select").value;
  if (!name) return;
  attempt(async () => {
    await api("DELETE", `/presets/${encodeURIComponent(name)}`);
    await loadPresets();
    showNotice(`Deleted “${name}”.`, "ok");
  });
});

$("preset-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const name = $("preset-name").value.trim();
  attempt(async () => {
    await api("POST", "/presets", { name, config: state.team });
    $("preset-name").value = "";
    await loadPresets();
    $("preset-select").value = name;
    showNotice(`Saved “${name}”.`, "ok");
  });
});

$("verifier").addEventListener("change", (event) => {
  if (event.target.value) state.team.verifier = event.target.value;
  else delete state.team.verifier;
  changed();
});

$("max-turns").addEventListener("input", (event) => setLimit("max_turns", event.target.value));
$("max-seconds").addEventListener("input", (event) => setLimit("max_seconds", event.target.value));
$("max-tokens").addEventListener("input", (event) => setLimit("max_tokens", event.target.value));

$("config").addEventListener("input", (event) => {
  try {
    const parsed = JSON.parse(event.target.value);
    if (!parsed || !parsed.lead || !Array.isArray(parsed.teammates) || !parsed.providers) {
      throw new Error("it needs lead, teammates, and providers");
    }
    state.team = parsed;
    $("config-error").textContent = "";
    saveDraft();
    renderKeys();
    renderVerifierOptions();
    renderReadiness();
  } catch (error) {
    $("config-error").textContent = `Not a valid team yet: ${error.message}`;
  }
});

$("goal").addEventListener("input", () => renderReadiness());
$("goal").addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key === "Enter" && !$("start").disabled) $("start").click();
});

$("start").addEventListener("click", () =>
  attempt(async () => {
    const goal = $("goal").value.trim();
    const { id } = await api("POST", "/runs", { config: state.team, goal });
    await loadRuns();
    openRun(id, goal, "running");
  })
);

$("human-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const text = $("human-text").value.trim();
  if (!text || !state.runId) return;
  attempt(async () => {
    const data = await api("POST", `/runs/${state.runId}/message`, { text });
    $("human-text").value = "";
    if (data.resumed) openRun(state.runId, state.goal, "running");
    await loadRuns();
  });
});

$("stop").addEventListener("click", () => {
  if (state.runId) attempt(() => api("POST", `/runs/${state.runId}/stop`, {}));
});

attempt(loadState);
