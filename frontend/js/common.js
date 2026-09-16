/* Shared across index.html / auth.html / app.html. */
const API = "/api";

async function api(path, opts) {
  const res = await fetch(API + path, opts);
  if (!res.ok) { const e = await res.json().catch(() => ({ detail: res.statusText })); throw new Error(e.detail || "Request failed"); }
  return res.json();
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function typeGlyph(type) {
  const letter = type === "class" ? "C" : type === "method" ? "M" : "\u0192";
  return `<span class="type-glyph ${type}">${letter}</span>`;
}

/* The site mark: three connected nodes drawn with the exact same glyph
   vocabulary used throughout the app (filled square = function, outlined
   square = class, dashed square = method) -- so the logo is a literal,
   tiny illustration of what the product shows you, not an arbitrary icon. */
const LOGO_SVG = `
<svg width="30" height="30" viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
  <line x1="7.5" y1="21.5" x2="21.5" y2="7.5" stroke="#5c5c5c" stroke-width="1.3"/>
  <line x1="7.5" y1="21.5" x2="23.5" y2="23.5" stroke="#5c5c5c" stroke-width="1.3"/>
  <line x1="21.5" y1="7.5" x2="23.5" y2="23.5" stroke="#5c5c5c" stroke-width="1.3"/>
  <rect x="15" y="1" width="13" height="13" rx="3" fill="#ffffff"/>
  <rect x="1" y="15" width="13" height="13" rx="3" fill="none" stroke="#ffffff" stroke-width="1.6"/>
  <rect x="17" y="17" width="13" height="13" rx="3" fill="none" stroke="#ffffff" stroke-width="1.4" stroke-dasharray="2.5 2.2"/>
</svg>`;

function renderLogo(el, { withWordmark = true, href = "/" } = {}) {
  el.innerHTML = `<a class="site-logo" href="${href}">${LOGO_SVG}${withWordmark ? "<span>Codebase Navigator</span>" : ""}</a>`;
}

/* Auth guard helpers -- each page calls exactly one of these on load. */
async function requireAuth() {
  const user = await api("/auth/me");
  if (!user) { window.location.href = "/auth.html"; return null; }
  return user;
}
async function redirectIfAuthed(destination = "/app.html") {
  const user = await api("/auth/me");
  if (user) window.location.href = destination;
  return user;
}
