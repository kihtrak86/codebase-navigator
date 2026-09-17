renderLogo(document.getElementById("navLogo"));
renderLogo(document.getElementById("footerLogo"), { withWordmark: false });
document.getElementById("footerYear").textContent = new Date().getFullYear();

(async () => {
  try {
    const user = await api("/auth/me");
    if (user) {
      document.getElementById("navActions").innerHTML =
        `<span class="nav-email">${esc(user.email)}</span>` +
        `<button onclick="location.href='/app.html'">Open app</button>`;
    }
  } catch {
                                                                             
                                                                            
  }
})();
