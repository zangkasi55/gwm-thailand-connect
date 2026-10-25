import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const registered = new Map();
globalThis.HTMLElement = class {};
globalThis.customElements = {
  get: key => registered.get(key),
  define: (key, value) => registered.set(key, value),
};
const { binaryLabel, escapeHtml, groupVehicleSignals, reportedValue } =
  await import("../../custom_components/gwm_thailand_connect/frontend/gwm-thailand-card.js");

test("real zero is preserved but missing readings are never fabricated", () => {
  assert.equal(reportedValue({ state: "0" }), "0");
  for (const state of ["unknown", "unavailable", "none", "None", ""])
    assert.equal(reportedValue({ state }), null);
  assert.equal(reportedValue(undefined), null);
  assert.equal(binaryLabel({ state: "on" }, "Unlocked", "Locked"), "Unlocked");
  assert.equal(binaryLabel({ state: "off" }, "Unlocked", "Locked"), "Locked");
  assert.equal(binaryLabel({ state: "unknown" }, "Unlocked", "Locked"), "Not reported");
});

test("vehicles and unrelated integrations cannot contaminate each other's readings", () => {
  const registry = [
    { entity_id: "sensor.a", device_id: "car-a", platform: "gwm_thailand_connect" },
    { entity_id: "sensor.b", device_id: "car-b", platform: "gwm_thailand_connect" },
    { entity_id: "sensor.ess", device_id: "car-a", platform: "ess" },
  ];
  const state = value => ({ state: value, attributes: { gwm_signal: "soc", gwm_controls: { enabled: false } } });
  const groups = groupVehicleSignals(registry, {
    "sensor.a": state("55"), "sensor.b": state("0"), "sensor.ess": state("100"),
  });
  assert.equal(groups.length, 2);
  assert.equal(groups.find(v => v.id === "car-a").signals.soc.state, "55");
  assert.equal(groups.find(v => v.id === "car-b").signals.soc.state, "0");
});

test("owner supplied labels cannot inject dashboard HTML", () => {
  assert.equal(escapeHtml('<img src=x onerror="bad">'), "&lt;img src=x onerror=&quot;bad&quot;&gt;");
});

test("card load only reads the registry and contains no autostart or mock telemetry", async () => {
  const source = await readFile(new URL("../../custom_components/gwm_thailand_connect/frontend/gwm-thailand-card.js", import.meta.url), "utf8");
  assert.equal(registered.has("gwm-thailand-card"), true);
  assert.equal((source.match(/callService\(/g) || []).length, 1);
  assert.match(source, /data-confirm-check/);
  assert.match(source, /confirmed: true/);
  assert.doesNotMatch(source, /620\s*km|78\s*%|km\/h|localStorage|https:\/\/[^"]+/);
  assert.match(source, /No command was sent/);
});

test("constructor and telemetry changes never send a physical command", async () => {
  const Card = registered.get("gwm-thailand-card");
  const oldAttachShadow = globalThis.HTMLElement.prototype.attachShadow;
  globalThis.HTMLElement.prototype.attachShadow = function() {
    this.shadowRoot = { addEventListener() {}, innerHTML: "", querySelector: () => null };
  };
  try {
    let writes = 0;
    const card = new Card();
    card.setConfig({ type: "custom:gwm-thailand-card" });
    card.hass = {
      connected: true, states: {}, services: { gwm_thailand_connect: { thailand_control: {} } },
      callWS: async () => [],
      callService: async () => { writes++; },
    };
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(writes, 0);
    assert.match(card.shadowRoot.innerHTML, /No connected Thai vehicle/);
    await card._send();
    assert.equal(writes, 0);
  } finally {
    globalThis.HTMLElement.prototype.attachShadow = oldAttachShadow;
  }
});

test("confirmation, permission recheck and in-flight guard permit at most one explicit request", async () => {
  const Card = registered.get("gwm-thailand-card");
  const previous = globalThis.HTMLElement.prototype.attachShadow;
  const checkbox = { checked: false };
  globalThis.HTMLElement.prototype.attachShadow = function() {
    this.shadowRoot = {
      addEventListener() {}, innerHTML: "",
      querySelector: selector => selector === "[data-confirm-check]" ? checkbox : null,
    };
  };
  try {
    let writes = 0;
    let resolveRequest;
    const card = new Card();
    card.setConfig({ type: "custom:gwm-thailand-card" });
    card._registry = [{ entity_id: "sensor.a", device_id: "car-a", platform: "gwm_thailand_connect" }];
    const controls = { enabled: true, status: "advertised", actions: [{ action: "unlock", label: "Unlock", group: "locks" }] };
    card.hass = {
      connected: true,
      states: { "sensor.a": { entity_id: "sensor.a", state: "55", attributes: { gwm_signal: "soc", gwm_controls: controls } } },
      services: { gwm_thailand_connect: { thailand_control: {} } },
      callService: async (domain, service, data) => {
        writes++;
        assert.equal(domain, "gwm_thailand_connect");
        assert.equal(service, "thailand_control");
        assert.equal(data.confirmed, true);
        assert.equal(data.device_id, "car-a");
        await new Promise(resolve => { resolveRequest = resolve; });
      },
    };
    card._pending = { action: "unlock", label: "Unlock", device: "car-a" };
    await card._send();
    assert.equal(writes, 0);
    checkbox.checked = true;
    controls.enabled = false;
    await card._send();
    assert.equal(writes, 0);
    controls.enabled = true;
    card._pending = { action: "unlock", label: "Unlock", device: "car-a" };
    const sending = card._send();
    await card._send();
    assert.equal(writes, 1);
    resolveRequest();
    await sending;
    assert.equal(writes, 1);
  } finally {
    globalThis.HTMLElement.prototype.attachShadow = previous;
  }
});
