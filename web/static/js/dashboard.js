import { api } from "./api.js";
import { clear, el, formatTs } from "./dom.js";
import { renderNav } from "./nav.js";

renderNav({ authed: true });

const notice = document.getElementById("keys-notice");
const tableWrap = document.getElementById("keys-table");
const tableBody = document.getElementById("keys-body");
const createButton = document.getElementById("create-key");
const modal = document.getElementById("key-modal");
const modalValue = document.getElementById("key-modal-value");
const modalCopy = document.getElementById("key-modal-copy");
const modalClose = document.getElementById("key-modal-close");

let revealedKey = null;

function showNotice(text, isError = false) {
  notice.textContent = text;
  notice.classList.toggle("error", isError);
  notice.hidden = false;
  tableWrap.hidden = true;
}

function showTable() {
  notice.hidden = true;
  tableWrap.hidden = false;
}

function revealKey(rawKey) {
  revealedKey = rawKey;
  modalValue.value = rawKey;
  modal.showModal();
  modalValue.focus();
  modalValue.select();
}

function hideKey() {
  revealedKey = null;
  modalValue.value = "";
  modalCopy.textContent = "Copy";
  if (modal.open) modal.close();
}

function actionButton(label, className, onClick) {
  return el("button", {
    class: `btn ${className} btn-small`,
    type: "button",
    text: label,
    onclick: onClick,
  });
}

function usageCell(usage) {
  if (!usage) return el("span", { class: "num", text: "Unavailable" });
  return el("div", { class: "num" }, [
    el("div", { text: `${usage.quota_remaining} / ${usage.daily_quota} requests left today` }),
    el("div", { text: `${usage.rate_remaining} / ${usage.rate_limit} left this minute` }),
  ]);
}

function renderRow(key, usage) {
  const revoked = Boolean(key.revoked);
  const actions = el("div", { class: "actions" });
  if (!revoked) {
    actions.append(
      actionButton("Rotate", "btn-ghost", () => rotateKey(key.id)),
      actionButton("Revoke", "btn-danger", () => revokeKey(key.id))
    );
  }

  return el("tr", {}, [
    el("td", { class: "mono", text: `fp_${key.prefix}...` }),
    el("td", { class: "num", text: formatTs(key.created_at) }),
    el("td", { class: "num", text: key.last_used ? formatTs(key.last_used) : "Never" }),
    el("td", {}, usageCell(usage)),
    el("td", {}, el("span", {
      class: `badge ${revoked ? "badge-off" : "badge-ok"}`,
      text: revoked ? "Revoked" : "Active",
    })),
    el("td", {}, actions),
  ]);
}

async function load() {
  showNotice("Loading API keys...");
  createButton.disabled = true;
  try {
    const [keys, usage] = await Promise.all([api("/keys"), api("/web/usage")]);
    const usageById = new Map((usage.keys || []).map((item) => [item.id, item]));
    clear(tableBody);

    if (!keys.length) {
      showNotice("No API keys yet. Create one to call the harness API.");
      return;
    }

    for (const key of keys) {
      tableBody.append(renderRow(key, usageById.get(key.id)));
    }
    showTable();
  } catch {
    showNotice("Could not load your API keys.", true);
  } finally {
    createButton.disabled = false;
  }
}

async function createKey() {
  createButton.disabled = true;
  try {
    const created = await api("/keys", { method: "POST" });
    revealKey(created.key);
    await load();
  } catch {
    showNotice("Could not create an API key.", true);
  } finally {
    createButton.disabled = false;
  }
}

async function rotateKey(keyId) {
  if (!window.confirm("Rotate this key? The old key stops working immediately.")) return;
  try {
    const rotated = await api(`/keys/${encodeURIComponent(keyId)}/rotate`, { method: "POST" });
    revealKey(rotated.key);
    await load();
  } catch {
    showNotice("Could not rotate this key.", true);
  }
}

async function revokeKey(keyId) {
  if (!window.confirm("Revoke this key? This cannot be undone.")) return;
  try {
    await api(`/keys/${encodeURIComponent(keyId)}/revoke`, { method: "POST" });
    await load();
  } catch {
    showNotice("Could not revoke this key.", true);
  }
}

createButton.addEventListener("click", createKey);
modalClose.addEventListener("click", hideKey);
modal.addEventListener("close", () => {
  revealedKey = null;
  modalValue.value = "";
  modalCopy.textContent = "Copy";
});
modalCopy.addEventListener("click", async () => {
  if (!revealedKey) return;
  try {
    await navigator.clipboard.writeText(revealedKey);
    modalCopy.textContent = "Copied";
  } catch {
    modalCopy.textContent = "Copy failed";
  }
});

load();
