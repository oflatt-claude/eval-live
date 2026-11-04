/* Own the viewer state and connect controls, calculations, and the DOM.
   Filter edits request an engine calculation. Engine status/results and display
   controls only redraw the view, so publishing results cannot trigger a loop.
   Table controls live in state.ui and survive replacement of computed rows.
   Loaded after state.js, table-view.js, and graph-engine.js as a plain script. */

// Clarifying subtitles for known raw result tables.
const TABLE_DESCRIPTIONS = {
  warnings: "Expected, non-bug failures: timeouts and cells that use a feature "
    + "the backend/encoding does not support (push/pop, proofs-incompatible "
    + "commands). Kept out of “errors” on purpose.",
  errors: "Real, unexpected failures (likely bugs). Expected non-bug failures "
    + "live in the “warnings” table instead.",
  skipped: "Files excluded up front (not benchmarked) and why — e.g. "
    + "unsupported by the term encoding, or no bridge-normal reference.",
};

/**
 * Render evaluation tables (and, when a graph script is supplied, Pyodide graphs
 * + computed tables) into a container.
 *
 * Keep data unchanged after initialization. Both graphScript and evalLivePy are
 * needed for Python outputs; raw tables work without them. Whole numbers outside
 * JavaScript's exact integer range raise an error instead of being displayed.
 *
 * @param {HTMLElement|string} container
 * @param {Object} data - { tableName: rows[] }
 * @param {string} [name] - project name shown in the heading
 * @param {string} [graphScript] - Python building an eval_live.Registry
 * @param {string} [evalLivePy] - source of eval_live.py
 * @param {Object.<string,string>} [extraModules] - {filename: source} extra
 *   Python modules written to the Pyodide filesystem, importable by graphScript
 * @returns {Object} getState() exposes the current state, setState(mutator,
 *   recompute=true) applies a change, and render() redraws without running Python.
 */
function initEvalLive(container, data, name, graphScript, evalLivePy, extraModules = {}) {
  if (typeof container === "string") container = document.getElementById(container);
  container.classList.add("eval-live");
  container.innerHTML = "";

  const state = makeState(data, name, graphScript, evalLivePy);
  let engine = null;   // created at the end, once the DOM skeleton exists

  // --- reducer dispatch ---------------------------------------------------
  /**
   * Apply a synchronous state mutation and redraw. Return false from mutator
   * for a no-op. Keep recompute=true for filter edits; engine updates and changes
   * to collapse, highlighting, or the active graph must pass false.
   */
  function setState(mutator, recompute = true) {
    const changed = mutator(state);
    if (changed === false) return;          // reducer reported a no-op
    render();
    if (engine && recompute) engine.tick(state);
  }

  const handlers = {
    onToggleCollapse: (n, kind) => setState((s) => toggleCollapsed(s, n, kind), false),
    onSql: (n, val, kind) => setState((s) => setSql(s, n, val, kind)),
    onColFilter: (n, col, val, kind) => setState((s) => setColFilter(s, n, col, val, kind)),
    onHighlight: (n, mode, kind) => setState((s) => setHighlight(s, n, mode, kind), false),
    onCheckbox: (n, col, checked, all, kind) => setState((s) => toggleCheckbox(s, n, col, checked, all, kind)),
  };

  // --- persistent DOM skeleton (built once) -------------------------------
  if (name) {
    const h1 = document.createElement("h1");
    h1.className = "eval-live-title";
    h1.textContent = name + " — Eval Live";
    container.appendChild(h1);
  }

  // Graph section (only with a graph script). render() fills bar + display.
  let graphStatusEl = null, graphBar = null, graphDisplay = null;
  let lastGraphsRef = null;
  if (graphScript && evalLivePy) {
    const graphSection = document.createElement("div");
    graphSection.className = "graph-section";
    graphStatusEl = document.createElement("div");
    graphStatusEl.className = "graph-status";
    graphSection.appendChild(graphStatusEl);
    graphBar = document.createElement("div");
    graphBar.className = "graph-bar";
    graphSection.appendChild(graphBar);
    graphDisplay = document.createElement("div");
    graphDisplay.className = "graph-display";
    graphSection.appendChild(graphDisplay);
    container.appendChild(graphSection);
  }

  const clearBtn = document.createElement("button");
  clearBtn.className = "clear-filters-btn";
  clearBtn.textContent = "Clear all filters";
  clearBtn.addEventListener("click", () => setState((s) => clearAllFilters(s)));
  container.appendChild(clearBtn);

  const computedContainer = document.createElement("div");
  computedContainer.className = "computed-container";
  container.appendChild(computedContainer);

  let rawHeader = null;
  const rawContainer = document.createElement("div");
  rawContainer.className = "raw-container";

  // Raw table views: data is fixed, so build once.
  const rawViews = [];
  for (const [tableName, rows] of Object.entries(data)) {
    if (!Array.isArray(rows) || rows.length === 0) continue;
    const view = buildTableView(tableName, rows, "raw", true,
                                TABLE_DESCRIPTIONS[tableName], handlers);
    rawViews.push(view);
  }
  if (rawViews.length > 0) {
    rawHeader = document.createElement("h2");
    rawHeader.className = "raw-tables-header";
    rawHeader.textContent = "Raw Tables";
    container.appendChild(rawHeader);
    container.appendChild(rawContainer);
    for (const v of rawViews) rawContainer.appendChild(v.section);
  }

  // Replace computed views when the engine publishes a report. Their controls
  // remain in state.ui, independent of the rows being replaced.
  let computedViews = [];
  let lastComputedRef = null;
  let lastUnfilteredRef = null;
  let lastMessagesRef = null;
  function rebuildComputedViews() {
    computedContainer.innerHTML = "";
    computedViews = [];
    for (const t of [...state.engine.computedTables, ...state.engine.messages]) {
      const kind = state.engine.messages.includes(t) ? "message" : "computed";
      const unfiltered = state.engine.computedUnfiltered.find((u) => u.name === t.name);
      const optionRows = kind === "computed" && unfiltered ? unfiltered.rows : t.rows;
      if (!optionRows || optionRows.length === 0) continue;
      ensureTableUi(state, t.name, kind);     // persist ui across rebuilds
      // Checkbox options come from the UNFILTERED version of this table, so
      // unchecking a value doesn't make it disappear as the display narrows.
      const view = buildTableView(t.name, t.rows, kind, true,
                                  t.caption, handlers, optionRows);
      computedViews.push(view);
      computedContainer.appendChild(view.section);
    }
  }

  // --- graph section reconcile -------------------------------------------
  function syncGraphs() {
    if (!graphStatusEl) return;
    const eng = state.engine;
    graphStatusEl.textContent =
      eng.status === "ready" ? (eng.graphs.length ? "" : "No graphs registered.")
                             : eng.statusText || "";

    if (eng.graphs !== lastGraphsRef) {
      lastGraphsRef = eng.graphs;
      graphBar.innerHTML = "";
      for (const g of eng.graphs) {
        const btn = document.createElement("button");
        btn.className = "graph-btn";
        btn.textContent = g.name;
        btn.addEventListener("click", () => setState((s) => setActiveGraph(s, g.name), false));
        graphBar.appendChild(btn);
      }
    }

    const active = resolveActiveGraph(state);
    const btns = graphBar.querySelectorAll(".graph-btn");
    btns.forEach((b) => b.classList.toggle("active", b.textContent === active));

    graphDisplay.innerHTML = "";
    const g = eng.graphs.find((x) => x.name === active);
    if (eng.status === "ready" && g && g.src) {
      const img = document.createElement("img");
      img.src = g.src;
      img.alt = g.name;
      graphDisplay.appendChild(img);
    }
  }

  // --- the render: reconcile everything from state ------------------------
  function render() {
    syncGraphs();
    // Rebuild computed views when the displayed rows OR the unfiltered rows
    // (which feed the dropdown options) change.
    if (state.engine.computedTables !== lastComputedRef ||
        state.engine.computedUnfiltered !== lastUnfilteredRef ||
        state.engine.messages !== lastMessagesRef) {
      lastComputedRef = state.engine.computedTables;
      lastUnfilteredRef = state.engine.computedUnfiltered;
      lastMessagesRef = state.engine.messages;
      rebuildComputedViews();
    }
    for (const v of computedViews) v.sync(state);
    for (const v of rawViews) v.sync(state);
  }

  render();
  if (graphScript && evalLivePy) {
    engine = createEngine(graphScript, evalLivePy, (mutate) => setState(mutate, false), extraModules);
    engine.tick(state);     // kick off Pyodide load + initial graphs/tables
  }

  return { getState: () => state, setState, render };
}
