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
<svg width="30" height="30" viewBox="0 0 32 32" fill="none" xmlns="http:                                          
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

/* ---------------- Shared modal (prompt / confirm replacements) ----------------
   Native prompt()/confirm() render as unstyled OS dialogs that clash with the
   app's design, block the whole tab, and can't be keyboard-dismissed
   consistently across browsers. These promise-based helpers render the same
   .modal-overlay/.modal-card markup used elsewhere in the app instead. */

function closeModal() {
  document.getElementById("sharedModalOverlay")?.remove();
  document.removeEventListener("keydown", modalKeyHandler, true);
}

function modalKeyHandler(e) {
  if (e.key === "Escape") closeModal();
}

function openModal({ title, body, danger = false, confirmLabel = "Save", showInput = true, inputValue = "" }) {
  return new Promise((resolve) => {
    closeModal();
    const overlay = document.createElement("div");
    overlay.id = "sharedModalOverlay";
    overlay.className = "modal-overlay";
    overlay.innerHTML = `
      <div class="modal-card" role="dialog" aria-modal="true">
        <button class="modal-close" type="button" aria-label="Close">&times;</button>
        <h2>${esc(title)}</h2>
        ${body ? `<p class="hint" style="margin-top:8px;">${body}</p>` : ""}
        <form id="sharedModalForm">
          ${showInput ? `<div class="field" style="margin-top:${body ? '2px' : '16px'};">
            <input id="sharedModalInput" autocomplete="off" value="${esc(inputValue)}" />
          </div>` : ""}
          <div class="modal-actions">
            <button type="button" class="ghost" id="sharedModalCancel">Cancel</button>
            <button type="submit" class="${danger ? 'danger-btn' : ''}">${esc(confirmLabel)}</button>
          </div>
        </form>
      </div>`;
    document.body.appendChild(overlay);
    document.addEventListener("keydown", modalKeyHandler, true);

    const input = document.getElementById("sharedModalInput");
    input?.focus();
    input?.select();

    const finish = (value) => { closeModal(); resolve(value); };
    overlay.addEventListener("mousedown", (e) => { if (e.target === overlay) finish(null); });
    document.getElementById("sharedModalCancel").onclick = () => finish(null);
    overlay.querySelector(".modal-close").onclick = () => finish(null);
    document.getElementById("sharedModalForm").onsubmit = (e) => {
      e.preventDefault();
      finish(showInput ? input.value.trim() : true);
    };
  });
}

/** Styled drop-in replacement for prompt(title, defaultValue). Resolves to
    the trimmed string, or null if cancelled / left empty. */
async function modalPrompt(title, inputValue = "", body = "") {
  const value = await openModal({ title, body, showInput: true, inputValue, confirmLabel: "Save" });
  return value ? value : null;
}

/** Styled drop-in replacement for confirm(message). Resolves to true/false. */
async function modalConfirm(title, body, confirmLabel = "Confirm") {
  const result = await openModal({ title, body, showInput: false, danger: true, confirmLabel });
  return result === true;
}
