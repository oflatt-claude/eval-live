/* Exercise the engine against a Pyodide boundary whose default dict conversion
   returns plain objects. Run with: node test/graph-engine.test.mjs */
import { readFileSync } from "node:fs";
import vm from "node:vm";
import assert from "node:assert/strict";
import { setTimeout as delay } from "node:timers/promises";

const globals = new Map();
let created = 0;
let destroyed = 0;
function proxy(value) {
  created++;
  return {
    toJs(options) {
      assert.equal(options.create_pyproxies, false);
      function convert(item) {
        if (Array.isArray(item)) return item.map(convert);
        if (item !== null && typeof item === "object") {
          const pairs = Object.entries(item).map(([key, val]) => [key, convert(val)]);
          return (options.dict_converter || Object.fromEntries)(pairs);
        }
        return item;
      }
      return convert(value);
    },
    destroy() { destroyed++; },
  };
}

const pyodide = {
  globals,
  FS: { writeFile() {} },
  toPy(value) { return value; },
  async loadPackage() {},
  async loadPackagesFromImports() {},
  runPython() {},
  async runPythonAsync(source) {
    const data = globals.get("__eval_live_data__");
    if (source.includes(".run_tables(")) {
      return proxy([{ name: "Summary", rows: data.samples.map((row) => ({
                        ...row, styled: { text: row.id, style: { bold: true } },
                      })),
                      has_filter_source: true, caption: "Sample IDs" }]);
    }
    if (source.includes(".run_graphs(")) {
      return proxy([{ name: "Samples", src: "data:image/png;base64," + data.samples.length }]);
    }
    if (source.includes(".apply_table_filters(")) {
      const filters = globals.get("__eval_live_table_filters__");
      const ids = new Set(filters[0].filtered_rows.map((row) => row.id));
      return proxy({ samples: data.samples.filter((row) => ids.has(row.id)) });
    }
  },
};

const ctx = { console, setTimeout, clearTimeout, loadPyodide: async () => pyodide };
vm.createContext(ctx);
for (const file of ["sql-filter.js", "state.js", "graph-engine.js"]) {
  vm.runInContext(readFileSync(new URL("../eval_live/" + file, import.meta.url), "utf8"), ctx);
}
const state = ctx.makeState({ samples: [{ id: "one" }, { id: "two" }] }, "Demo", "script", "library");
const engine = ctx.createEngine("script", "library", (mutate) => mutate(state));

async function until(predicate) {
  const deadline = Date.now() + 3000;
  while (!predicate() && state.engine.status !== "error" && Date.now() < deadline) await delay(10);
  assert.notEqual(state.engine.status, "error", state.engine.statusText);
  assert.ok(predicate(), "engine did not finish");
}

engine.tick(state);
await until(() => state.engine.graphs.length === 1);
assert.equal(state.engine.graphs[0].name, "Samples");
assert.equal(state.engine.computedTables[0].caption, "Sample IDs");
assert.equal(JSON.stringify(state.engine.computedTables[0].rows.map((row) => row.id)), '["one","two"]');
assert.equal(state.engine.computedTables[0].rows[0].styled.text, "one");
assert.equal(state.engine.computedTables[0].rows[0].styled.style.bold, true);
console.log("  ok  graph and table results work with explicit dictionary conversion");

ctx.ensureTableUi(state, "Summary", "computed").sql = "one";
engine.tick(state);
await until(() => state.engine.graphs[0].src.endsWith(",1"));
assert.equal(JSON.stringify(state.engine.narrowing), '{"samples":[{"id":"one"}]}');
assert.equal(state.engine.computedTables[0].rows.length, 1);
assert.equal(state.data.samples.length, 2);
assert.equal(destroyed, created);
console.log("  ok  computed filtering converts narrowed data and releases result proxies");
