renderLogo(document.getElementById("navLogo"), { href: "/" });

let authMode = "login";
let lastResetToken = null; // carried from 'forgot' step into 'reset' step (dev mode, no email)

(async () => {
  const params = new URLSearchParams(window.location.search);
  const requested = params.get("mode");
  // Set up the form FIRST, so a failed/slow session check can never leave the
  // page in its default half-hidden state with no usable form.
  setAuthMode(["login", "signup", "forgot", "reset"].includes(requested) ? requested : "login");

  try {
    await redirectIfAuthed("/app.html"); // already signed in? skip the form entirely
  } catch {
    // Session check failed (offline, backend down). Staying on the form is the
    // right fallback -- signing in again is harmless if they had a session.
  }
})();

function setAuthMode(mode) {
  authMode = mode;
  document.getElementById("authError").classList.add("hidden");
  document.getElementById("authSuccess").classList.add("hidden");
  document.getElementById("devTokenNote").classList.add("hidden");
  document.getElementById("authForm").reset();

  const emailField = document.getElementById("emailField");
  const passwordField = document.getElementById("passwordField");
  const tokenField = document.getElementById("tokenField");
  const forgotRow = document.getElementById("forgotLinkRow");
  const switchRow = document.querySelector(".auth-switch");
  const title = document.getElementById("authTitle");
  const hint = document.getElementById("authHint");
  const submitBtn = document.getElementById("authSubmitBtn");

  emailField.classList.toggle("hidden", mode === "reset");
  passwordField.classList.toggle("hidden", mode === "forgot");
  tokenField.classList.toggle("hidden", mode !== "reset");
  forgotRow.classList.toggle("hidden", mode !== "login");
  switchRow.classList.toggle("hidden", mode === "forgot" || mode === "reset");

  document.getElementById("authEmail").required = mode !== "reset";
  document.getElementById("authPassword").required = mode !== "forgot";
  document.getElementById("authToken").required = mode === "reset";

  if (mode === "login") {
    title.textContent = "Sign in"; hint.textContent = "Welcome back.";
    submitBtn.textContent = "Sign in";
    document.getElementById("authSwitchText").textContent = "Don't have an account?";
    document.getElementById("authSwitchBtn").textContent = "Sign up";
    document.getElementById("authPassword").setAttribute("autocomplete", "current-password");
  } else if (mode === "signup") {
    title.textContent = "Create your account"; hint.textContent = "Free \u2014 index and explore your own repos.";
    submitBtn.textContent = "Create account";
    document.getElementById("authSwitchText").textContent = "Already have an account?";
    document.getElementById("authSwitchBtn").textContent = "Sign in";
    document.getElementById("authPassword").setAttribute("autocomplete", "new-password");
  } else if (mode === "forgot") {
    title.textContent = "Reset your password"; hint.textContent = "Enter the email on your account and we'll send a reset link.";
    submitBtn.textContent = "Send reset link";
  } else if (mode === "reset") {
    title.textContent = "Choose a new password"; hint.textContent = "Paste the token from the reset email below.";
    submitBtn.textContent = "Reset password";
    document.getElementById("authPassword").setAttribute("autocomplete", "new-password");
    if (lastResetToken) document.getElementById("authToken").value = lastResetToken;
  }

  // reflect the current mode in the URL so a refresh/share keeps the right form
  const url = new URL(window.location.href);
  url.searchParams.set("mode", mode);
  window.history.replaceState({}, "", url);
}

function toggleAuthMode() { setAuthMode(authMode === "login" ? "signup" : "login"); }

async function submitAuth(event) {
  event.preventDefault();
  const email = document.getElementById("authEmail").value.trim();
  const password = document.getElementById("authPassword").value;
  const token = document.getElementById("authToken").value.trim();
  const errEl = document.getElementById("authError");
  errEl.classList.add("hidden");

  try {
    if (authMode === "login" || authMode === "signup") {
      await api(`/auth/${authMode}`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      window.location.href = "/app.html";
    } else if (authMode === "forgot") {
      const result = await api("/auth/forgot-password", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email }),
      });
      const successEl = document.getElementById("authSuccess");
      successEl.textContent = result.message;
      successEl.classList.remove("hidden");
      if (result.dev_reset_token) {
        lastResetToken = result.dev_reset_token;
        document.getElementById("devTokenValue").textContent = result.dev_reset_token;
        document.getElementById("devTokenNote").classList.remove("hidden");
      }
      setTimeout(() => setAuthMode("reset"), 900);
    } else if (authMode === "reset") {
      await api("/auth/reset-password", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token, password }),
      });
      lastResetToken = null;
      setAuthMode("login");
      const successEl = document.getElementById("authSuccess");
      successEl.textContent = "Password updated \u2014 sign in with your new password.";
      successEl.classList.remove("hidden");
    }
  } catch (e) {
    errEl.textContent = e.message;
    errEl.classList.remove("hidden");
  }
}

function copyDevToken() {
  const text = document.getElementById("devTokenValue").textContent;
  navigator.clipboard?.writeText(text).catch(() => {});
}
