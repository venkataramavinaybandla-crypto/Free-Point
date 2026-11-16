import { api } from "./api.js";
import { renderNav } from "./nav.js";

renderNav({ authed: false });

const form = document.getElementById("auth-form");
const message = document.getElementById("auth-message");
const submit = document.getElementById("auth-submit");

if (form) {
  const mode = form.dataset.mode;
  const endpoint = mode === "signup" ? "/auth/signup" : "/auth/login";

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    message.hidden = true;
    submit.disabled = true;
    submit.textContent = mode === "signup" ? "Creating account..." : "Signing in...";

    const data = Object.fromEntries(new FormData(form).entries());
    try {
      await api(endpoint, { method: "POST", body: JSON.stringify(data) });
      window.location.assign("/dashboard");
    } catch {
      message.textContent =
        mode === "signup"
          ? "Could not create your account. Please check your details and try again."
          : "Could not sign in. Check your email and password.";
      message.classList.add("error");
      message.hidden = false;
      submit.disabled = false;
      submit.textContent = mode === "signup" ? "Create account" : "Sign in";
    }
  });
}
