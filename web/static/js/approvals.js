import { api } from "./api.js";
import { clear, el } from "./dom.js";
import { renderNav } from "./nav.js";

renderNav({ authed: true });

const listHost = document.getElementById("approvals-list");
const listNotice = document.getElementById("approvals-notice");
const detailState = document.getElementById("detail-state");
const detailBody = document.getElementById("detail-body");
const detailTitle = document.getElementById("detail-title");
const detailRepo = document.getElementById("detail-repo");
const detailBase = document.getElementById("detail-base");
const detailHead = document.getElementById("detail-head");
const detailJob = document.getElementById("detail-job");
const detailCreated = document.getElementById("detail-created");
const detailExpires = document.getElementById("detail-expires");
const detailHash = document.getElementById("detail-hash");
const detailPrBody = document.getElementById("detail-body-text");
const detailDiff = document.getElementById("detail-diff");
const approveButton = document.getElementById("approve-button");
const rejectButton = document.getElementById("reject-button");
const decisionMessage = document.getElementById("decision-message");

let selectedId = null;
let selectedHash = null;

function setListNotice(text, isError = false) {
  listNotice.textContent = text;
  listNotice.classList.toggle("error", isError);
  listNotice.hidden = false;
}

function setDecisionMessage(text, isError = false) {
  decisionMessage.textContent = text;
  decisionMessage.classList.toggle("error", isError);
  decisionMessage.hidden = false;
}

function resetDetail(text) {
  selectedId = null;
  selectedHash = null;
  detailBody.hidden = true;
  detailState.textContent = text;
  detailState.hidden = false;
  decisionMessage.hidden = true;
  approveButton.disabled = true;
  rejectButton.disabled = true;
  for (const row of listHost.querySelectorAll(".approval-row")) {
    row.setAttribute("aria-pressed", "false");
  }
}

function formatTime(seconds) {
  if (typeof seconds !== "number" || !Number.isFinite(seconds) || seconds <= 0) {
    return null;
  }
  const date = new Date(seconds * 1000);
  if (Number.isNaN(date.getTime())) {
    return null;
  }
  return date.toLocaleString();
}

function renderRow(approval) {
  const children = [];

  const hasRepo = Boolean(approval.repo && String(approval.repo).trim());
  const hasBase = Boolean(approval.base && String(approval.base).trim());
  const hasHead = Boolean(approval.head && String(approval.head).trim());

  if (hasRepo && hasBase && hasHead) {
    children.push(el("span", { class: "repo", text: `${approval.repo}  ${approval.base} <- ${approval.head}` }));
  } else if (hasRepo) {
    children.push(el("span", { class: "repo", text: approval.repo }));
    if (!hasBase || !hasHead) {
      console.warn("Approval row missing base or head branch:", approval);
    }
  } else {
    console.warn("Approval row missing repo:", approval);
  }

  if (approval.title && String(approval.title).trim()) {
    children.push(el("span", { class: "title", text: approval.title }));
  } else {
    console.warn("Approval row missing title:", approval);
  }

  const metaParts = [];
  if (approval.job_id && String(approval.job_id).trim()) {
    metaParts.push(approval.job_id);
  } else {
    console.warn("Approval row missing job_id:", approval);
  }

  const createdFormatted = formatTime(approval.created_at);
  if (createdFormatted) {
    metaParts.push(`created ${createdFormatted}`);
  } else {
    console.warn("Approval row missing or invalid created_at:", approval);
  }

  const expiryFormatted = formatTime(approval.expires_at);
  if (expiryFormatted) {
    metaParts.push(`expires ${expiryFormatted}`);
  } else {
    console.warn("Approval row missing or invalid expires_at:", approval);
  }

  if (metaParts.length > 0) {
    children.push(el("span", { class: "meta", text: metaParts.join(" · ") }));
  }

  return el(
    "button",
    {
      class: "approval-row",
      type: "button",
      "aria-pressed": "false",
      dataset: { approvalId: approval.id || "" },
      onclick: () => openDetail(approval.id),
    },
    children
  );
}

async function loadList() {
  setListNotice("Loading pending approvals...");
  clear(listHost);
  try {
    const approvals = await api("/approvals");
    if (!approvals || !approvals.length) {
      setListNotice("No pending approvals");
      return;
    }
    listNotice.hidden = true;
    for (const approval of approvals) {
      listHost.append(renderRow(approval));
    }
  } catch {
    setListNotice("Could not load pending approvals.", true);
  }
}

async function openDetail(approvalId) {
  if (!approvalId) {
    console.warn("openDetail called without approvalId");
    return;
  }
  resetDetail("Loading the approval payload...");
  approveButton.disabled = true;
  rejectButton.disabled = true;
  try {
    const detail = await api(`/approvals/${encodeURIComponent(approvalId)}`);
    selectedId = detail.id;
    selectedHash = detail.payload_hash;

    const setField = (elem, value, warnName) => {
      if (value !== undefined && value !== null && String(value).trim() !== "") {
        elem.textContent = String(value);
        elem.hidden = false;
        if (elem.previousElementSibling && elem.previousElementSibling.tagName === "DT") {
          elem.previousElementSibling.hidden = false;
        }
      } else {
        elem.hidden = true;
        if (elem.previousElementSibling && elem.previousElementSibling.tagName === "DT") {
          elem.previousElementSibling.hidden = true;
        }
        console.warn(`Detail missing ${warnName}:`, detail);
      }
    };

    setField(detailTitle, detail.title, "title");
    setField(detailRepo, detail.repo, "repo");
    setField(detailBase, detail.base, "base");
    setField(detailHead, detail.head, "head");
    setField(detailJob, detail.job_id, "job_id");

    const createdFormatted = formatTime(detail.created_at);
    if (createdFormatted) {
      detailCreated.textContent = createdFormatted;
      detailCreated.hidden = false;
      if (detailCreated.previousElementSibling) detailCreated.previousElementSibling.hidden = false;
    } else {
      detailCreated.hidden = true;
      if (detailCreated.previousElementSibling) detailCreated.previousElementSibling.hidden = true;
      console.warn("Detail missing created_at:", detail);
    }

    const expiresFormatted = formatTime(detail.expires_at);
    if (expiresFormatted) {
      detailExpires.textContent = expiresFormatted;
      detailExpires.hidden = false;
      if (detailExpires.previousElementSibling) detailExpires.previousElementSibling.hidden = false;
    } else {
      detailExpires.hidden = true;
      if (detailExpires.previousElementSibling) detailExpires.previousElementSibling.hidden = true;
      console.warn("Detail missing expires_at:", detail);
    }

    setField(detailHash, detail.payload_hash, "payload_hash");
    setField(detailPrBody, detail.body, "body");
    setField(detailDiff, detail.diff, "diff");

    detailState.hidden = true;
    detailBody.hidden = false;
    approveButton.disabled = false;
    rejectButton.disabled = false;
    for (const row of listHost.querySelectorAll(".approval-row")) {
      row.setAttribute("aria-pressed", row.dataset.approvalId === detail.id ? "true" : "false");
    }
  } catch {
    resetDetail("Could not load this approval.");
  }
}

async function decide(decision) {
  if (!selectedId) return;
  const approvalId = selectedId;
  const payloadHash = selectedHash;
  approveButton.disabled = true;
  rejectButton.disabled = true;
  decisionMessage.hidden = true;
  try {
    await api(`/approvals/${encodeURIComponent(approvalId)}/${decision}`, {
      method: "POST",
      body: JSON.stringify({ payload_hash: payloadHash }),
    });
    resetDetail(
      decision === "approve"
        ? "Approved. The execute message was queued."
        : "Rejected. The worker will not create this pull request."
    );
    await loadList();
  } catch (error) {
    if (error.status === 409 || error.status === 410) {
      setDecisionMessage("This approval can no longer be used.", true);
      await loadList();
    } else {
      setDecisionMessage("Could not complete this action.", true);
    }
    approveButton.disabled = false;
    rejectButton.disabled = false;
  }
}

approveButton.addEventListener("click", () => decide("approve"));
rejectButton.addEventListener("click", () => decide("reject"));

loadList();
