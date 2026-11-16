import { api } from "./api.js";
import { clear, el, formatTs } from "./dom.js";
import { renderNav } from "./nav.js";

renderNav({ authed: true });

const form = document.getElementById("model-form");
const saveButton = document.getElementById("save-model");
const testButton = document.getElementById("test-model");
const testResult = document.getElementById("test-result");
const message = document.getElementById("settings-message");
const secretList = document.getElementById("secret-list");
const secretNotice = document.getElementById("secret-notice");
const urlField = document.getElementById("model-url");
const tokenField = document.getElementById("model-token");

function showMessage(text, isError = false) {
  message.textContent = text;
  message.classList.toggle("error", isError);
  message.hidden = false;
}

function showTestResult(resultText, isError = false) {
  if (testResult) {
    testResult.textContent = resultText;
    testResult.className = `test-result ${isError ? "error" : "success"}`;
    testResult.hidden = false;
  }
}

async function deleteSecret(name) {
  try {
    await api(`/secrets/${encodeURIComponent(name)}`, {
      method: "DELETE",
    });
    showMessage(`Deleted ${name}.`);
    await loadSecrets();
  } catch {
    showMessage("Could not delete secret.", true);
  }
}

async function loadSecrets() {
  secretNotice.textContent = "Loading stored secrets...";
  secretNotice.classList.remove("error");
  secretNotice.hidden = false;
  clear(secretList);
  try {
    const secrets = await api("/secrets");
    if (!secrets.length) {
      secretNotice.textContent = "No secrets stored yet.";
      return;
    }
    secretNotice.hidden = true;
    for (const secret of secrets) {
      const item = el("li", { class: "secret-item", dataset: { secretName: secret.name } }, [
        el("div", { class: "secret-meta" }, [
          el("span", { class: "secret-name", text: secret.name }),
          el("span", { class: "status-pill", text: "Saved" }),
          el("span", { class: "masked", text: "••••••••" }),
          el("span", { class: "when", text: `Updated ${formatTs(secret.updated_at)}` }),
        ]),
        el("div", { class: "secret-actions" }, [
          el("button", {
            class: "btn btn-ghost btn-small",
            type: "button",
            text: "Replace",
            onClick: () => {
              if (secret.name === "model_url") {
                urlField.focus();
                urlField.select?.();
              } else if (secret.name === "model_bearer_token") {
                tokenField.focus();
                tokenField.select?.();
              }
            },
          }),
          el("button", {
            class: "btn btn-ghost btn-small btn-danger",
            type: "button",
            text: "Delete",
            onClick: async () => {
              await deleteSecret(secret.name);
            },
          }),
        ]),
      ]);
      secretList.append(item);
    }
  } catch {
    secretNotice.textContent = "Could not load your stored secret names.";
    secretNotice.classList.add("error");
  }
}

async function saveSecrets() {
  const modelUrl = urlField.value.trim();
  const token = tokenField.value.trim();
  if (!modelUrl && !token) {
    showMessage("Enter a model URL or token to store.", true);
    return;
  }

  saveButton.disabled = true;
  let saveFailed = false;
  let savedAny = false;

  if (modelUrl) {
    try {
      await api("/secrets/model_url", {
        method: "PUT",
        body: JSON.stringify({ value: modelUrl }),
      });
      urlField.value = "";
      savedAny = true;
    } catch {
      saveFailed = true;
    }
  }

  if (token) {
    try {
      await api("/secrets/model_bearer_token", {
        method: "PUT",
        body: JSON.stringify({ value: token }),
      });
      tokenField.value = "";
      savedAny = true;
    } catch {
      saveFailed = true;
    }
  }

  if (saveFailed) {
    showMessage("Could not save the model settings.", true);
  } else if (savedAny) {
    const timeStr = new Date().toLocaleTimeString();
    showMessage(`Saved at ${timeStr}`);
  }

  if (savedAny) {
    await loadSecrets();
  }

  saveButton.disabled = false;
}

async function testConnection() {
  testButton.disabled = true;
  if (testResult) {
    testResult.textContent = "Testing...";
    testResult.className = "test-result";
    testResult.hidden = false;
  }
  showMessage("Testing the stored model endpoint...");
  try {
    const result = await api("/web/model/test", { method: "POST" });
    const isError = result.status !== "ok" || result.result !== "passed";
    // Mapped reason classes: passed, unreachable, unauthorized, invalid URL blocked by SSRF guard, timeout
    let resultClass = result.result || result.message;
    if (!resultClass) {
      resultClass = isError ? "unreachable" : "passed";
    }
    showTestResult(resultClass, isError);
    showMessage(resultClass, isError);
  } catch {
    showTestResult("unreachable", true);
    showMessage("unreachable", true);
  } finally {
    testButton.disabled = false;
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  saveSecrets();
});
testButton.addEventListener("click", testConnection);

loadSecrets();
