const form = document.querySelector("#inbound-form");
const submit = document.querySelector("#inbound-submit");
const errorBox = document.querySelector("#inbound-error");
const channel = document.querySelector("#channel");
const messages = document.querySelector("#sms-messages");
const SUBMIT_LABEL = "Find deal and open Account Manager";
const IN_PROGRESS = new Set(["queued", "resolving", "agent_running", "validating"]);
const STATUS_LABELS = {
  queued: "Queued",
  resolving: "Finding deal by email or phone",
  agent_running: "Deal Manager is reading the message",
  validating: "Checking the deal",
  run_started: "Opening Account Manager",
  needs_review: "Needs a person",
  no_match: "No matching deal",
  failed: "Failed",
};
let redirectedToRun = false;
const MESSAGE_LABELS = {
  held: "Held: goes to Account Manager with the first message",
  included: "Sent with the first message",
  pending: "Waiting for Account Manager to accept messages",
  not_delivered: "Not delivered",
};
const additionalForm = document.querySelector("#additional-message-form");
const additionalText = document.querySelector("#additional-message-text");
const additionalButton = document.querySelector("#additional-message-button");
const additionalError = document.querySelector("#additional-message-error");
let timer = null;
let currentRecord = null;
let sendingAdditional = false;

additionalForm.addEventListener("submit", sendAdditionalMessage);

channel.addEventListener("change", updateChannel);
form.addEventListener("input", showPayload);
form.addEventListener("submit", send);
document.querySelector("#add-message").addEventListener("click", () => {
  addMessage();
  showPayload();
});

async function request(url, options) {
  const response = await fetch(url, options);
  const payload = await response.json();
  if (!response.ok) {
    const detail = payload.detail;
    const message = Array.isArray(detail) ? detail.map((item) => item.msg).join("; ") : detail;
    throw new Error(message || "The request could not be completed.");
  }
  return payload;
}

function showError(message) {
  errorBox.textContent = message;
  errorBox.classList.remove("hidden");
}

function setText(id, value) {
  document.getElementById(id).textContent = value || "";
}

function field(id) {
  return document.getElementById(id).value.trim();
}

function addMessage(value = "") {
  const box = document.createElement("textarea");
  box.rows = 2;
  box.maxLength = 20000;
  box.placeholder = `Message ${messages.children.length + 1}`;
  box.value = value;
  messages.append(box);
}

function updateChannel() {
  const sms = channel.value === "SMS";
  document.querySelector("#sms-fields").classList.toggle("hidden", !sms);
  document.querySelector("#email-fields").classList.toggle("hidden", sms);
  if (sms && !messages.children.length) addMessage();
  showPayload();
}

function buildPayload() {
  const payload = channel.value === "SMS"
    ? {phone: field("phone"), body: [...messages.children].map((box) => box.value.trim()).filter(Boolean)}
    : {email: field("email"), subject: field("subject"), body: field("email-body")};
  if (field("thread-id")) payload.thread_id = field("thread-id");
  if (field("message-id")) payload.message_id = field("message-id");
  return payload;
}

function showPayload() {
  setText("payload-preview", JSON.stringify(buildPayload(), null, 2));
}

async function send(event) {
  event.preventDefault();
  clearTimeout(timer);
  errorBox.classList.add("hidden");
  submit.disabled = true;
  submit.textContent = "Starting…";
  try {
    const record = await request("/api/inbound", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(buildPayload()),
    });
    history.replaceState({}, "", `/client-response/${record.inbound_id}`);
    render(record);
    poll(record);
    loadHistory();
  } catch (error) {
    showError(error.message);
    submit.disabled = false;
    submit.textContent = SUBMIT_LABEL;
  }
}

function link(href, text) {
  const anchor = document.createElement("a");
  anchor.href = href;
  anchor.textContent = text;
  return anchor;
}

function step(title, state, detail, extra) {
  const item = document.createElement("li");
  item.dataset.state = state;
  const heading = document.createElement("strong");
  heading.textContent = title;
  const text = document.createElement("p");
  text.className = "step-detail";
  text.textContent = detail;
  item.append(heading, text);
  if (extra) item.append(extra);
  return item;
}

function lookupStep(record) {
  const search = record.deal_search;
  if (record.source === "internal") {
    return step("1. Find deal", "info", "Fresh Prints team mail: the sender is not the client, so Deal Manager reads the message.");
  }
  if (!search) return step("1. Find deal", record.status === "failed" ? "stopped" : "active", "Looking up the sender…");
  if (search.outcome === "found") return step("1. Find deal", "done", `Found deal ${search.deal_id}. ${search.reason}.`);
  const names = search.candidates.map((deal) => `${deal.deal_id} (${deal.stage})`).join(", ");
  return step("1. Find deal", "info", `No single deal yet: ${search.reason}.${names ? ` Candidates: ${names}.` : ""}`);
}

function agentStep(record) {
  const title = "2. Deal Manager (if needed)";
  if (record.deal_search?.outcome === "found") return step(title, "skipped", "Skipped — email/phone already found the deal.");
  const lookup = record.lookup_id ? link(`/deal-manager/lookups/${record.lookup_id}`, "Open Deal Manager details") : null;
  if (record.status === "agent_running") return step(title, "active", "Reading the message to find the deal…", lookup);
  if (record.resolved_by === "agent") return step(title, "done", `Found deal ${record.found_deal_id}.`, lookup);
  if (record.lookup_id || ["needs_review", "no_match", "failed"].includes(record.status)) {
    return step(title, "stopped", record.error || record.reason || "No deal found.", lookup);
  }
  return step(title, "pending", "Only runs if step 1 did not find one deal.");
}

function validationStep(record) {
  const title = "2b. Check the deal";
  const resolution = record.resolution;
  if (!resolution) {
    if (record.status === "validating") return step(title, "active", `Checking deal ${record.found_deal_id}…`);
    if (record.deal_search?.outcome === "found" || record.resolved_by === "agent") {
      return step(title, "pending", "About to check the deal.");
    }
    return step(title, "pending", "Waits until a deal is found.");
  }
  const label = resolution.summary || resolution.reason;
  const plan = resolution.consolidation;
  let detail = resolution.status === "proceed" ? `Deal ${resolution.deal_id}: ${label}` : `Stopped: ${label}`;
  if (plan) {
    detail += ` Keep deal ${plan.canonical_deal_id}; mark lost: ${plan.merged_deal_ids.join(", ")}`;
    detail += plan.proof_ids_to_link.length ? `; link proofs ${plan.proof_ids_to_link.join(", ")}.` : "; no proofs to link.";
    const failed = plan.actions.filter((action) => !action.ok);
    if (failed.length) detail += ` CRM said: ${failed.map((action) => `deal ${action.deal_id}: ${action.message}`).join("; ")}.`;
  }
  return step(title, resolution.status === "proceed" ? "done" : "stopped", detail);
}

function accountManagerStep(record) {
  const title = "3. Account Manager";
  if (record.run_id) {
    return step(
      title,
      "done",
      `Ready on deal ${record.deal_id}. Opening the normal run page…`,
      link(`/runs/${record.run_id}`, "Open Account Manager run"),
    );
  }
  if (IN_PROGRESS.has(record.status)) return step(title, "pending", "Opens once the deal is ready.");
  return step(title, "stopped", "Not started.");
}

function sourceKey(runId) {
  return `am-source-${runId}`;
}

function saveSource(record) {
  const input = record.input || {};
  const from = input.phone || input.email || "";
  const subject = input.subject || "";
  const how = record.resolved_by === "agent"
    ? "Deal Manager read the message"
    : record.deal_search?.outcome === "found"
      ? "Found by email or phone"
      : "Deal resolved";
  sessionStorage.setItem(sourceKey(record.run_id), JSON.stringify({
    deal_id: record.deal_id,
    from,
    subject,
    how,
    reason: record.reason || record.deal_search?.reason || "",
    inbound_id: record.inbound_id,
    channel: input.phone ? "SMS" : "Email",
  }));
}

function openAccountManagerRun(record) {
  if (redirectedToRun || !record.run_id) return;
  redirectedToRun = true;
  saveSource(record);
  const banner = document.querySelector("#inbound-source");
  banner.textContent = `Deal ${record.deal_id} found. Opening Account Manager…`;
  banner.classList.remove("hidden");
  window.location.href = `/runs/${record.run_id}`;
}

async function sendAdditionalMessage(event) {
  event.preventDefault();
  if (!currentRecord || !additionalText.value.trim()) return;
  sendingAdditional = true;
  additionalButton.disabled = true;
  additionalError.classList.add("hidden");
  try {
    await request(`/api/inbound/${currentRecord.inbound_id}/messages`, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({text: additionalText.value}),
    });
    additionalText.value = "";
    const next = await request(`/api/inbound/${currentRecord.inbound_id}`);
    render(next);
    clearTimeout(timer);
    poll(next);
  } catch (error) {
    additionalError.textContent = error.message;
    additionalError.classList.remove("hidden");
  } finally {
    sendingAdditional = false;
    additionalButton.disabled = !currentRecord?.accepting_messages;
  }
}

function messageLabel(message) {
  if (message.status === "forwarded") {
    const delivery = message.delivery || "sent";
    return `Sent to the running Account Manager: ${delivery}`;
  }
  return MESSAGE_LABELS[message.status] || message.status;
}

function renderAdditionalMessages(record) {
  additionalButton.disabled = !record.accepting_messages || sendingAdditional;
  setText("additional-message-state", !record.accepting_messages
    ? "This client response has ended. Send a new client response for another message."
    : record.run_id ? "New messages go to the running Account Manager as additional messages."
    : "New messages are held and sent to Account Manager together with the first message.");
  const list = document.querySelector("#additional-message-list");
  list.replaceChildren();
  for (const message of record.messages || []) {
    const item = document.createElement("li");
    item.textContent = `${message.text}\n${messageLabel(message)}${message.error ? ` — ${message.error}` : ""}`;
    list.append(item);
  }
}

function stillChanging(record) {
  return IN_PROGRESS.has(record.status) || record.accepting_messages || (record.messages || []).some(
    (message) => ["held", "pending"].includes(message.status)
      || (message.status === "forwarded" && ["pending", "sending"].includes(message.delivery)));
}

function render(record) {
  currentRecord = record;
  document.querySelector("#inbound-run").classList.remove("hidden");
  const pending = IN_PROGRESS.has(record.status);
  const opening = record.status === "run_started" && Boolean(record.run_id);
  const blocked = ["needs_review", "no_match", "failed"].includes(record.status);
  document.querySelector("#compose-card").classList.toggle("hidden", pending || opening);
  document.querySelector("#additional-message-section").classList.toggle(
    "hidden",
    !(record.accepting_messages || (record.messages || []).length),
  );
  renderAdditionalMessages(record);
  const status = document.querySelector("#inbound-status");
  status.textContent = STATUS_LABELS[record.status] || record.status.replace("_", " ");
  status.className = `status ${pending || opening ? "status-running" : record.status === "failed" ? "status-failed" : blocked ? "" : "status-completed"}`;
  setText("inbound-answer", record.deal_id ? `Deal ${record.deal_id}` : STATUS_LABELS[record.status]);
  setText(
    "inbound-reason",
    opening
      ? "Deal found and checked. Taking you to the Account Manager run…"
      : record.deal_id
        ? STATUS_LABELS[record.status]
        : record.error || record.reason,
  );
  const banner = document.querySelector("#inbound-source");
  if (opening) {
    const from = record.input?.phone || record.input?.email || "client";
    const kind = record.source === "internal" ? "team mail from" : "";
    banner.textContent = `Matched deal ${record.deal_id} for ${kind ? `${kind} ` : ""}${from}. Opening the normal Account Manager flow…`;
    banner.classList.remove("hidden");
  } else {
    banner.classList.add("hidden");
  }
  submit.disabled = pending || opening;
  submit.textContent = pending || opening ? `${STATUS_LABELS[record.status]}…` : SUBMIT_LABEL;
  document.querySelector("#inbound-steps").replaceChildren(
    lookupStep(record), agentStep(record), validationStep(record), accountManagerStep(record),
  );
  const actions = document.querySelector("#inbound-actions");
  actions.replaceChildren();
  if (record.run_id) {
    const open = link(`/runs/${record.run_id}`, "Open Account Manager run");
    open.className = "button-link";
    actions.append(open);
  }
  if (record.deal_id) actions.append(link(`/deals/${record.deal_id}`, `Open deal ${record.deal_id}`));
  actions.classList.toggle("hidden", !actions.children.length);
  setText("inbound-json", JSON.stringify(record, null, 2));
  if (opening) openAccountManagerRun(record);
}

function poll(record) {
  if (record.status === "run_started" && record.run_id) {
    openAccountManagerRun(record);
    loadHistory();
    return;
  }
  if (!stillChanging(record)) {
    loadHistory();
    return;
  }
  timer = setTimeout(async () => {
    try {
      const next = await request(`/api/inbound/${record.inbound_id}`);
      render(next);
      poll(next);
    } catch (error) {
      showError(`${error.message} Retrying…`);
      poll(record);
    }
  }, 2000);
}

async function loadHistory() {
  try {
    const records = await request("/api/inbound");
    const list = document.querySelector("#inbound-history");
    list.replaceChildren();
    if (!records.length) list.textContent = "No client responses yet.";
    for (const record of records) {
      const item = document.createElement("li");
      const who = `${record.input.phone || record.input.email}${record.source === "internal" ? " (team)" : ""}`;
      item.append(link(`/client-response/${record.inbound_id}`,
        `${new Date(record.created_at).toLocaleString()} · ${who} · ${STATUS_LABELS[record.status] || record.status}${record.deal_id ? ` · Deal ${record.deal_id}` : ""}`));
      list.append(item);
    }
  } catch (error) {
    showError(error.message);
  }
}

async function restore() {
  const match = location.pathname.match(/^\/client-response\/([a-f0-9]{32})$/);
  if (!match) return;
  try {
    const record = await request(`/api/inbound/${match[1]}`);
    const input = record.input;
    channel.value = input.phone ? "SMS" : "EMAIL";
    updateChannel();
    document.getElementById("email").value = input.email || "";
    document.getElementById("subject").value = input.subject || "";
    document.getElementById("phone").value = input.phone || "";
    document.getElementById("thread-id").value = input.thread_id || "";
    document.getElementById("message-id").value = input.message_id || "";
    if (input.phone) {
      messages.replaceChildren();
      for (const text of input.body) addMessage(text);
    } else {
      document.getElementById("email-body").value = input.body;
    }
    showPayload();
    render(record);
    poll(record);
  } catch (error) {
    showError(error.message);
  }
}

updateChannel();
loadHistory();
restore();
