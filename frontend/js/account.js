renderLogo(document.getElementById("navLogo"), { href: "/app.html" });

(async () => {
  try {
    const user = await requireAuth();
    if (!user) return;
    await loadAccount();
  } catch {
    document.querySelector(".account-page").innerHTML =
      `<div class="empty-state">Couldn't reach the server. Check that the backend is running, then reload.</div>`;
  }
})();

async function loadAccount() {
  const acct = await api("/auth/account");
  document.getElementById("profileAvatar").textContent = acct.email.charAt(0).toUpperCase();
  document.getElementById("profileEmail").textContent = acct.email;
  document.getElementById("profileSince").textContent = acct.created_at
    ? `Member since ${formatDate(acct.created_at)}`
    : "";
  document.getElementById("usageGrid").innerHTML = `
    <div class="usage-tile"><div class="n">${acct.repo_count}</div><div class="l">Repositories</div></div>
    <div class="usage-tile"><div class="n">${acct.file_count}</div><div class="l">Files</div></div>
    <div class="usage-tile"><div class="n">${acct.symbol_count}</div><div class="l">Symbols</div></div>
    <div class="usage-tile"><div class="n">${acct.dependency_count}</div><div class="l">Dependencies</div></div>
  `;
}

function formatDate(raw) {
  const d = new Date(raw.includes("T") ? raw : raw.replace(" ", "T") + "Z");
  if (isNaN(d)) return raw;
  return d.toLocaleDateString(undefined, { year: "numeric", month: "long", day: "numeric" });
}

function show(id, message, isError) {
  const el = document.getElementById(id);
  el.textContent = message;
  el.classList.remove("hidden");
                                                                             
  document.getElementById(isError ? id.replace("Error", "Success") : id.replace("Success", "Error"))
    ?.classList.add("hidden");
}

async function submitChangeEmail(event) {
  event.preventDefault();
  document.getElementById("emailError").classList.add("hidden");
  document.getElementById("emailSuccess").classList.add("hidden");
  try {
    await api("/auth/change-email", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        new_email: document.getElementById("newEmail").value.trim(),
        current_password: document.getElementById("emailPassword").value,
      }),
    });
    show("emailSuccess", "Email updated. Use the new address next time you sign in.", false);
    event.target.reset();
    await loadAccount();
  } catch (e) {
    show("emailError", e.message, true);
  }
}

async function submitChangePassword(event) {
  event.preventDefault();
  document.getElementById("pwError").classList.add("hidden");
  document.getElementById("pwSuccess").classList.add("hidden");
  try {
    await api("/auth/change-password", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        current_password: document.getElementById("currentPassword").value,
        new_password: document.getElementById("newPassword").value,
      }),
    });
    show("pwSuccess", "Password updated.", false);
    event.target.reset();
  } catch (e) {
    show("pwError", e.message, true);
  }
}

async function submitDeleteAccount(event) {
  event.preventDefault();
  const errEl = document.getElementById("deleteError");
  errEl.classList.add("hidden");

  const confirmText = document.getElementById("deleteConfirm").value.trim();
  if (confirmText.toUpperCase() !== "DELETE") {
    errEl.textContent = "Type DELETE to confirm.";
    errEl.classList.remove("hidden");
    return;
  }
                                                                             
                                                       
  if (!window.confirm("This permanently deletes your account and every repository you've indexed. This cannot be undone.")) return;

  try {
    await api("/auth/delete-account", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        current_password: document.getElementById("deletePassword").value,
        confirm: confirmText,
      }),
    });
    window.location.href = "/";
  } catch (e) {
    errEl.textContent = e.message;
    errEl.classList.remove("hidden");
  }
}
