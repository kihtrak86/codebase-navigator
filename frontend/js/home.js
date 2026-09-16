renderLogo(document.getElementById("navLogo"));

(async () => {
  try {
    const user = await api("/auth/me");
    if (user) {
      document.getElementById("navActions").innerHTML =
        `<span class="hero-note" style="margin-right:4px;">${esc(user.email)}</span>` +
        `<button onclick="location.href='/app.html'">Open app</button>`;
    }
  } catch {
    // Session check failed -- leave the default signed-out nav in place. The
    // homepage is fully readable either way, so there's nothing to recover.
  }
})();
