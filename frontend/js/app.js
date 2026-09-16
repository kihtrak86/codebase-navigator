let currentRepo = null;
let currentFile = null;
let currentSymbol = null;

(async () => {
  renderLogo(document.getElementById("navLogo"), { href: "/app.html" });

  let user;
  try {
    user = await requireAuth(); // redirects to /auth.html if not signed in
  } catch {
    // Couldn't reach the backend. Don't bounce to the login page (they may
    // well have a valid session) -- say what happened and let them retry.
    setStatus("Couldn't reach the server. Check that the backend is running, then reload.", { err: true });
    return;
  }
  if (!user) return;

  document.getElementById("userEmail").textContent = user.email;
  document.getElementById("accountDropdownEmail").textContent = user.email;
  document.getElementById("accountAvatar").textContent = user.email.charAt(0).toUpperCase();
  await loadRepos();
})();

/* ---------------- Account menu ---------------- */

async function doLogout() {
  document.getElementById("accountDropdown").classList.add("hidden");
  await api("/auth/logout", { method: "POST" });
  window.location.href = "/";
}

function toggleAccountMenu(event) {
  event.stopPropagation();
  document.getElementById("accountDropdown").classList.toggle("hidden");
}
document.addEventListener("click", () => document.getElementById("accountDropdown")?.classList.add("hidden"));

/* ---------------- Repo / file / symbol browsing ---------------- */

function setStatus(msg, opts) {
  opts = opts || {};
  const el = document.getElementById("statusStrip");
  if (!msg) { el.innerHTML = ""; return; }
  el.innerHTML = `${opts.live ? '<span class="pulse-dot live"></span>' : ''}<span class="status-text${opts.err ? ' err' : ''}">${esc(msg)}</span>`;
}

async function indexRepo() {
  const url = document.getElementById("repoUrl").value.trim();
  if (!url) return;
  await runIndex(url);
}

async function reindexRepo(url) {
  document.getElementById("repoUrl").value = url;
  await runIndex(url);
}

async function runIndex(url) {
  setStatus("Cloning and indexing \u2014 this can take a moment for larger repos.", { live: true });
  try {
    const result = await api("/repos/index", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ url }) });
    setStatus(`Indexed ${result.name}: ${result.file_count} files, ${result.symbol_count} symbols, ${result.dependency_count} dependencies.`);
    await loadRepos();
    await selectRepo(result.repository_id);
  } catch (e) {
    setStatus(e.message, { err: true });
  }
}

async function loadRepos() {
  const repos = await api("/repos");
  const panel = document.getElementById("repoPanel");
  panel.innerHTML = `<div class="section-label"><span class="dot"></span>Repositories</div>` +
    (repos.length ? repos.map(r => {
      const initial = (r.name || "?").charAt(0).toUpperCase();
      return `<div class="repo-card${r.id === currentRepo ? ' active' : ''}" data-id="${r.id}" onclick="selectRepo(${r.id})">
        <div class="repo-mark">${esc(initial)}</div>
        <div class="repo-main">
          <div class="repo-name" title="${esc(r.name)}">${esc(r.name)}</div>
          <div class="repo-sub">${r.symbol_count} symbols</div>
        </div>
        <button class="repo-del" title="Re-index" onclick="event.stopPropagation(); reindexRepo('${esc(r.url).replace(/'/g, "\\'")}')" style="font-size:13px;">&#8635;</button>
        <button class="repo-del" title="Delete repository" onclick="event.stopPropagation(); deleteRepo(${r.id})">&times;</button>
      </div>`;
    }).join("") : `<div class="empty-state">No repositories indexed yet.<br>Paste a GitHub URL above to get started.</div>`);
}

async function deleteRepo(repoId) {
  await api(`/repos/${repoId}`, { method: "DELETE" });
  if (currentRepo === repoId) {
    currentRepo = null; currentFile = null; currentSymbol = null;
    document.getElementById("filePanel").innerHTML = "";
    document.getElementById("detail").innerHTML = emptyDetailState();
    document.getElementById("statTop").classList.add("hidden");
  }
  await loadRepos();
}

function emptyDetailState() {
  return `<div class="empty-state" style="padding-top:80px;">Select a file, or search for a symbol, to see its details.</div>`;
}

async function selectRepo(repoId) {
  currentRepo = repoId;
  currentFile = null; currentSymbol = null;
  await loadRepos();

  const repos = await api("/repos");
  const repo = repos.find(r => r.id === repoId);
  if (repo) {
    const stat = document.getElementById("statTop");
    stat.classList.remove("hidden");
    stat.innerHTML = `
      <div class="stat-chip"><div class="n">${repo.file_count}</div><div class="l">Files</div></div>
      <div class="stat-chip"><div class="n">${repo.symbol_count}</div><div class="l">Symbols</div></div>
      <div class="stat-chip"><div class="n">${repo.dependency_count}</div><div class="l">Dependencies</div></div>
    `;
  }

  const tree = await api(`/repos/${repoId}/tree`);
  const panel = document.getElementById("filePanel");
  panel.innerHTML = `<input id="searchBox" placeholder="Search symbols by name" onkeydown="if(event.key==='Enter') doSearch()" />` +
    `<div class="section-label"><span class="dot"></span>Files</div>` +
    (tree.length
      ? `<div class="tree-toolbar">
           <button class="ghost" onclick="expandAllFolders(true)">Expand all</button>
           <button class="ghost" onclick="expandAllFolders(false)">Collapse all</button>
         </div>
         <div id="fileTree">${renderTree(tree, 0)}</div>`
      : `<div class="empty-state">No supported source files found.</div>`);
  document.getElementById("detail").innerHTML = emptyDetailState();
}

/* ---------------- File tree ---------------- */

/* Depth drives indentation only; the nesting itself comes from the
   server's tree structure. Top-level folders start open so the panel
   isn't a wall of collapsed rows on first view, deeper ones start
   closed so a deep project doesn't explode into hundreds of lines. */
function renderTree(nodes, depth) {
  return nodes.map(node => {
    const pad = 6 + depth * 13;
    if (node.type === "dir") {
      const open = depth === 0;
      return `<div class="tree-node">
        <div class="tree-row" style="padding-left:${pad}px" onclick="toggleFolder(this)" title="${esc(node.path)}">
          <span class="tree-caret${open ? ' open' : ''}">&#9656;</span>
          <span class="tree-icon dir"></span>
          <span class="tree-label">${esc(node.name)}</span>
          <span class="tree-count">${node.file_count}</span>
        </div>
        <div class="tree-children${open ? ' open' : ''}">${renderTree(node.children, depth + 1)}</div>
      </div>`;
    }
    return `<div class="tree-node">
      <div class="tree-row file-row" data-id="${node.id}" style="padding-left:${pad}px"
           onclick="selectFile(${node.id}, this)" title="${esc(node.path)}">
        <span class="tree-caret leaf">&#9656;</span>
        <span class="tree-icon file"></span>
        <span class="tree-label">${esc(node.name)}</span>
        <span class="tree-count">${node.symbol_count}</span>
      </div>
    </div>`;
  }).join("");
}

function toggleFolder(rowEl) {
  const caret = rowEl.querySelector(".tree-caret");
  const children = rowEl.nextElementSibling;
  const nowOpen = !children.classList.contains("open");
  children.classList.toggle("open", nowOpen);
  caret.classList.toggle("open", nowOpen);
}

function expandAllFolders(open) {
  document.querySelectorAll("#fileTree .tree-children").forEach(el => el.classList.toggle("open", open));
  document.querySelectorAll("#fileTree .tree-caret:not(.leaf)").forEach(el => el.classList.toggle("open", open));
}

async function doSearch() {
  const q = document.getElementById("searchBox").value.trim();
  if (!q || !currentRepo) return;
  const results = await api(`/repos/${currentRepo}/search?q=${encodeURIComponent(q)}`);
  const detail = document.getElementById("detail");
  detail.innerHTML = `
    <div class="detail-header" style="border-bottom:none;">
      <div class="detail-title-row"><span class="detail-title">Search results</span></div>
      <div class="detail-path">"${esc(q)}" \u2014 ${results.length} match${results.length === 1 ? '' : 'es'}</div>
    </div>
    <div class="tab-panel active" style="padding-top:0;">
      ${results.length ? results.map(r => `
        <div class="list-row clickable" onclick="selectSymbol(${r.id})">
          ${typeGlyph(r.type)}
          <div class="list-body">
            <div class="list-name">${esc(r.qualified_name)}</div>
            <div class="list-path">${esc(r.path)}</div>
          </div>
        </div>
      `).join("") : `<div class="empty-state">No symbols match "${esc(q)}".</div>`}
    </div>
  `;
}

async function selectFile(fileId, rowEl) {
  currentFile = fileId;
  document.querySelectorAll(".tree-row").forEach(el => el.classList.remove("active"));
  if (rowEl) rowEl.classList.add("active");
  const symbols = await api(`/repos/${currentRepo}/files/${fileId}/symbols`);
  const detail = document.getElementById("detail");
  detail.innerHTML = `
    <div class="detail-header" style="border-bottom:none;">
      <div class="detail-title-row"><span class="detail-title">Symbols</span></div>
      <div class="detail-path">${symbols.length} symbol${symbols.length === 1 ? '' : 's'} in this file</div>
    </div>
    <div class="tab-panel active" style="padding-top:0;">
      ${symbols.length ? symbols.map(s => `
        <div class="symbol-row" onclick="selectSymbol(${s.id})">
          ${typeGlyph(s.type)}
          <span class="name">${esc(s.qualified_name)}</span>
          <span class="lines">L${s.start_line}\u2013${s.end_line}</span>
        </div>
      `).join("") : `<div class="empty-state">No symbols found in this file.</div>`}
    </div>
  `;
}

function switchTab(tab) {
  document.querySelectorAll(".tab-btn").forEach(el => el.classList.toggle("active", el.dataset.tab === tab));
  document.querySelectorAll(".tab-panel").forEach(el => el.classList.toggle("active", el.dataset.tab === tab));
  if (tab === "graph" && currentSymbol) renderGraph(currentSymbol);
}

function rowList(items, opts) {
  opts = opts || {};
  if (!items.length) return `<div class="empty-state">${opts.emptyText}</div>`;
  return items.map(it => `
    <div class="list-row${it.id ? ' clickable' : ''}" ${it.id ? `onclick="selectSymbol(${it.id})"` : ''}>
      ${typeGlyph(it.type || 'function')}
      <div class="list-body">
        <div class="list-name">${esc(it.name)}</div>
        ${it.path ? `<div class="list-path">${esc(it.path)}</div>` : ''}
      </div>
      ${it.depth !== undefined ? `<span class="depth-chip">depth ${it.depth}</span>` : ''}
    </div>
  `).join("");
}

async function selectSymbol(symbolId) {
  currentSymbol = symbolId;
  const [sym, impact] = await Promise.all([
    api(`/symbols/${symbolId}`),
    api(`/symbols/${symbolId}/impact`),
  ]);

  const detail = document.getElementById("detail");
  detail.innerHTML = `
    <div class="detail-header">
      <div class="detail-title-row">
        <span class="type-badge">${typeGlyph(sym.type)}${sym.type}</span>
        <span class="detail-title">${esc(sym.name)}</span>
      </div>
      <div class="detail-path">${esc(sym.path)} <span class="lines">\u00b7 lines ${sym.start_line}\u2013${sym.end_line}</span></div>
      <div class="tab-strip">
        <button class="tab-btn active" data-tab="overview" onclick="switchTab('overview')">Overview</button>
        <button class="tab-btn" data-tab="callers" onclick="switchTab('callers')">Called by <span class="tab-count">${sym.callers.length}</span></button>
        <button class="tab-btn" data-tab="calls" onclick="switchTab('calls')">Calls <span class="tab-count">${sym.callees.length}</span></button>
        <button class="tab-btn" data-tab="tests" onclick="switchTab('tests')">Tests <span class="tab-count">${sym.tests.length}</span></button>
        <button class="tab-btn" data-tab="impact" onclick="switchTab('impact')">Impact <span class="tab-count">${impact.affected_symbols.length}</span></button>
        <button class="tab-btn" data-tab="graph" onclick="switchTab('graph')">Graph</button>
      </div>
    </div>

    <div class="tab-panel active" data-tab="overview">
      <div class="overview-grid">
        <div class="overview-tile" onclick="switchTab('callers')"><div class="n">${sym.callers.length}</div><div class="l">Called by</div></div>
        <div class="overview-tile" onclick="switchTab('calls')"><div class="n">${sym.callees.length}</div><div class="l">Calls</div></div>
        <div class="overview-tile" onclick="switchTab('tests')"><div class="n">${sym.tests.length}</div><div class="l">Tests</div></div>
        <div class="overview-tile" onclick="switchTab('impact')"><div class="n">${impact.affected_symbols.length}</div><div class="l">Impact</div></div>
        <div class="overview-tile" onclick="switchTab('graph')"><div class="n">&#8225;</div><div class="l">Graph view</div></div>
      </div>
    </div>

    <div class="tab-panel" data-tab="callers">
      ${rowList(sym.callers.map(c => ({id: c.id, name: c.qualified_name, path: c.path, type: c.type})), {emptyText: 'No known callers.'})}
    </div>

    <div class="tab-panel" data-tab="calls">
      ${rowList(sym.callees.map(c => ({id: c.id, name: c.qualified_name, path: c.path, type: c.type})), {emptyText: 'No outgoing calls detected.'})}
    </div>

    <div class="tab-panel" data-tab="tests">
      ${rowList(sym.tests.map(t => ({name: t.test_name, path: t.path})), {emptyText: 'No tests mapped to this symbol.'})}
    </div>

    <div class="tab-panel" data-tab="impact">
      <div class="impact-summary">
        <div class="stat-chip"><div class="n">${impact.affected_symbols.length}</div><div class="l">Symbols</div></div>
        <div class="stat-chip"><div class="n">${impact.affected_file_count}</div><div class="l">Files</div></div>
        <div class="stat-chip"><div class="n">${impact.affected_tests.length}</div><div class="l">Tests</div></div>
      </div>
      ${rowList(impact.affected_symbols.map(s => ({id: s.id, name: s.qualified_name, path: s.path, type: s.type, depth: s.depth})), {emptyText: 'Changing this symbol appears to be isolated \u2014 nothing else in the repo calls it.'})}
    </div>

    <div class="tab-panel" data-tab="graph">
      <div class="graph-legend">
        <span>${typeGlyph('function')} Function</span>
        <span>${typeGlyph('class')} Class</span>
        <span>${typeGlyph('method')} Method</span>
        <span class="root-key"><span class="key-dot"></span> Selected symbol</span>
      </div>
      <div id="graphContainer"><svg id="graphSvg" height="440"></svg><div id="graphNote" class="graph-note"></div></div>
    </div>
  `;
}

/* ---------------- Graph visualization (D3, monochrome) ---------------- */

async function renderGraph(symbolId) {
  const svg = d3.select("#graphSvg");
  svg.selectAll("*").remove();
  const container = document.getElementById("graphContainer");
  const width = container.clientWidth || 600;
  const height = 440;
  svg.attr("viewBox", [0, 0, width, height]);

  const data = await api(`/symbols/${symbolId}/graph?depth=2`);
  document.getElementById("graphNote").textContent = data.truncated
    ? `Showing a bounded neighborhood of this symbol (graph capped for readability).`
    : `${data.nodes.length} symbols, ${data.edges.length} connections.`;

  if (!data.nodes.length) return;

  const nodes = data.nodes.map(n => ({...n}));
  const links = data.edges.map(e => ({source: e.source, target: e.target}));

  const simulation = d3.forceSimulation(nodes)
    .force("link", d3.forceLink(links).id(d => d.id).distance(80).strength(0.6))
    .force("charge", d3.forceManyBody().strength(-220))
    .force("center", d3.forceCenter(width / 2, height / 2))
    .force("collide", d3.forceCollide(28));

  const link = svg.append("g").selectAll("line").data(links).join("line").attr("class", "graph-link");

  const node = svg.append("g").selectAll("g").data(nodes).join("g")
    .attr("class", d => `graph-node ${d.type}${d.root ? ' root' : ''}`)
    .call(d3.drag()
      .on("start", (event, d) => { if (!event.active) simulation.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; })
      .on("drag", (event, d) => { d.fx = event.x; d.fy = event.y; })
      .on("end", (event, d) => { if (!event.active) simulation.alphaTarget(0); d.fx = null; d.fy = null; }));

  node.append("circle").attr("r", d => d.root ? 10 : 7);
  node.append("text").attr("x", d => (d.root ? 14 : 11)).attr("y", 4).text(d => d.label);
  node.append("title").text(d => `${d.label}\n${d.path}`);

  node.on("click", (event, d) => { if (!d.root) selectSymbol(d.id); });

  simulation.on("tick", () => {
    link.attr("x1", d => d.source.x).attr("y1", d => d.source.y).attr("x2", d => d.target.x).attr("y2", d => d.target.y);
    node.attr("transform", d => `translate(${d.x},${d.y})`);
  });
}

document.addEventListener("keydown", (e) => {
  if (e.key !== "/" || e.metaKey || e.ctrlKey || e.altKey) return;
  const tag = document.activeElement?.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA") return;
  const box = document.getElementById("searchBox");
  if (box) { e.preventDefault(); box.focus(); }
});
