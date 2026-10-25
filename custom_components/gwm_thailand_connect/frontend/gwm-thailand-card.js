const UNKNOWN = new Set(["unknown", "unavailable", "none", "None", ""]);
const SETTINGS = "/config/integrations/integration/gwm_thailand_connect";

export function reportedValue(state) {
  return state && typeof state.state === "string" && !UNKNOWN.has(state.state) ? state.state : null;
}

export function binaryLabel(state, on, off) {
  const value = reportedValue(state);
  return value === "on" ? on : value === "off" ? off : "Not reported";
}

export function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
}

export function groupVehicleSignals(registry, states) {
  const grouped = new Map();
  for (const entry of registry) {
    if (!entry.device_id || entry.platform !== "gwm_thailand_connect") continue;
    const state = states[entry.entity_id];
    const signal = state?.attributes?.gwm_signal;
    if (typeof signal !== "string") continue;
    if (!grouped.has(entry.device_id)) grouped.set(entry.device_id, {});
    grouped.get(entry.device_id)[signal] = state;
  }
  return [...grouped].filter(([, signals]) => signals.soc?.attributes?.gwm_controls)
    .map(([id, signals]) => ({ id, signals }));
}

const GROUPS = {
  locks: ["Locks", "mdi:lock-outline"],
  tailgate: ["Tailgate", "mdi:car-back"],
  windows: ["Windows", "mdi:car-door"],
  sunroof: ["Sunroof", "mdi:car-convertible"],
  climate: ["Climate & air", "mdi:fan"],
  seats: ["Front-seat ventilation", "mdi:car-seat-cooler"],
  find: ["Find my car", "mdi:car-light-high"],
  location: ["Location privacy", "mdi:map-marker-outline"],
};

const CSS = `
  :host{display:block;color:#d9e9ff;font:14px/1.5 var(--paper-font-body1_-_font-family,Segoe UI,sans-serif)}
  *{box-sizing:border-box}button,input,select{font:inherit}button,a,select,input{outline-offset:4px}
  button:focus-visible,a:focus-visible{outline:2px solid #7ae5ff}button{cursor:pointer}
  button:disabled{cursor:not-allowed;opacity:.42}a{color:#59cfff}ha-icon{--mdc-icon-size:23px}
  .shell{max-width:1500px;margin:auto;background:radial-gradient(ellipse at 40% 0,#043367 0,transparent 46%),#020e1d;padding:28px;border:1px solid #075182;border-radius:22px;box-shadow:0 0 35px #007bff18}
  header{display:flex;align-items:center;justify-content:space-between;gap:20px;margin-bottom:24px}
  h1{font:italic 800 clamp(26px,3.4vw,42px)/1 Segoe UI,sans-serif;letter-spacing:3px;margin:0;color:#deedff;text-shadow:0 2px 10px #64baff60}
  .eyebrow{color:#70a2ce;letter-spacing:3px;text-transform:uppercase;font-size:10px;margin-top:10px}
  .header-meta{text-align:right;color:#a8c6e3}.clock{font-size:19px;color:#d9e9ff}
  .badge{display:inline-flex;gap:7px;align-items:center;border:1px solid #17608a;border-radius:30px;padding:5px 11px;font-size:11px;color:#7bd4ff}
  .badge:before{content:"";width:6px;height:6px;background:#36cfff;border-radius:50%}
  nav{display:flex;gap:8px;flex-wrap:wrap;padding:0 0 18px;border-bottom:1px solid #124263;margin-bottom:20px}
  nav button,.settings{border:1px solid #153b5d;border-radius:10px;background:#051a2e;color:#a8c6e3;padding:10px 17px;display:flex;align-items:center;gap:9px;text-decoration:none}
  nav button[aria-current=true]{color:white;border-color:#058bff;background:linear-gradient(110deg,#007bff,#053874)}
  .settings{margin-left:auto}.dashboard{display:grid;grid-template-columns:minmax(220px,.85fr) minmax(320px,1.4fr) minmax(250px,1fr);gap:18px}
  .panel{border:1px solid #075181;border-radius:16px;background:linear-gradient(130deg,#051e34dd,#020d19e8);overflow:hidden}
  .panel-title{display:flex;gap:10px;align-items:center;color:#cfe5ff;font-size:15px;padding:17px 18px;border-bottom:1px solid #11364f}
  .panel-title ha-icon{color:#26bdff}.gauge{width:218px;height:218px;border-radius:50%;margin:25px auto 10px;display:grid;place-items:center;background:conic-gradient(from 225deg,#00bfff 0deg,#0768ff var(--arc),#112c46 var(--arc),#112c46 270deg,transparent 270deg);filter:drop-shadow(0 0 8px #0084ff40);position:relative}
  .gauge:before{content:"";position:absolute;inset:11px;border-radius:50%;background:radial-gradient(ellipse at top,#07233b,#020d19 70%);border:1px solid #175181}
  .gauge-text{z-index:1;text-align:center}.gauge-value{font-size:58px;line-height:1;font-weight:650;letter-spacing:-3px}
  .gauge-value small{font-size:22px;letter-spacing:0}.gauge-caption{color:#70a2ce;font-size:12px;margin-top:10px}
  .stats{padding:0 18px 16px}.stat{display:flex;align-items:center;gap:13px;padding:13px 0;border-bottom:1px solid #12334e}.stat:last-child{border:0}
  .stat ha-icon{color:#78bded}.stat span{color:#85a8c9;font-size:12px}.stat strong{display:block;color:#e2efff;font-size:20px;font-weight:500}
  .vehicle-status{padding:15px;text-align:center;font-size:15px}.vehicle-status ha-icon{color:#51c6ff;margin-right:8px}
  .car-scene{height:400px;position:relative;background:radial-gradient(ellipse at center,#004f8040,transparent 65%)}
  .car-scene:before{content:"";position:absolute;inset:25px 15%;border:1px solid #057fc534;border-radius:50%}
  .car-image{position:absolute;height:345px;width:auto;left:50%;top:48%;transform:translate(-50%,-50%);object-fit:contain}
  .car-outline{width:168px;border:3px solid #22aaff;border-radius:66px 66px 48px 48px;background:linear-gradient(90deg,#06213b,#075086 48%,#06213b);box-shadow:0 0 28px #009cff60,inset 0 0 25px #00101e}
  .car-outline:before,.car-outline:after{content:"";position:absolute;left:17px;right:17px;height:48px;border:2px solid #5dbbfa;background:#021120}
  .car-outline:before{top:64px;border-radius:35px 35px 12px 12px}.car-outline:after{bottom:35px;border-radius:10px 10px 25px 25px}
  .car-outline .roof-panel{position:absolute;inset:119px 24px 98px;border:1px solid #66c6ff;border-radius:15px;background:#052842}
  .car-outline .wheel{position:absolute;left:-11px;top:65px;width:8px;height:48px;background:#0c1926;border:1px solid #4984ac;border-radius:4px;box-shadow:172px 0 #0c1926,0 166px #0c1926,172px 166px #0c1926}
  .hotspot{position:absolute;border:1px solid #137cbd;border-radius:24px;background:#011322ee;color:#b3dafc;padding:7px 10px;font-size:11px;display:flex;align-items:center;gap:5px;box-shadow:0 0 13px #0199ff18;min-height:38px}
  .hotspot:hover{border-color:#42d7ff;background:#032c49}.hotspot.alert{color:#ffc586;border-color:#a16032}.hotspot.unknown{border-style:dashed;color:#89a4be}
  .door-fl{left:2%;top:35%}.door-fr{right:2%;top:35%}.door-rl{left:2%;top:57%}.door-rr{right:2%;top:57%}
  .roof{left:50%;top:17%;transform:translateX(-50%)}.boot{left:50%;bottom:0;transform:translateX(-50%)}
  .car-links{display:flex;justify-content:center;gap:8px;flex-wrap:wrap;padding:16px 12px}.chip{border:1px solid #155f91;border-radius:25px;color:#8ed3ff;background:#03182a;padding:7px 12px;display:flex;gap:6px;align-items:center}
  .climate-body{padding:20px 18px}.temperature{display:flex;align-items:center;justify-content:space-between;margin:10px 0 5px;gap:8px}
  .temperature strong{font-size:38px;font-weight:550}.temperature strong small{font-size:19px}
  .round{border:1px solid #15517d;background:#031b30;border-radius:50%;width:40px;height:40px;color:#d0e7ff;font-size:24px}
  .muted{color:#83a6c7;font-size:12px}.cabin{display:flex;justify-content:space-between;border-top:1px solid #153e5b;margin-top:17px;padding-top:12px;font-size:12px}
  .control-buttons{display:flex;flex-wrap:wrap;gap:8px;margin-top:16px}
  .control-buttons button,.send,.cancel{border:1px solid #176795;border-radius:9px;background:#07233d;color:#d1eaff;padding:10px 13px;min-height:41px}
  .control-buttons button:not(:disabled):hover{background:#063b66;border-color:#19b7ff}
  .control-buttons button.primary,.send{background:linear-gradient(120deg,#007fff,#034692);border-color:#159aff;color:white}
  .form-label{display:flex;justify-content:space-between;gap:12px;margin-top:15px;font-size:12px;color:#9ebedd}
  select,input[type=number]{background:#031a2c;border:1px solid #155078;border-radius:7px;color:#d8ebff;padding:5px 8px;max-width:90px}
  .notice{display:flex;gap:10px;align-items:flex-start;border:1px solid #17618b;border-radius:11px;background:#03203966;padding:12px 15px;margin:18px 0;color:#9cc8e9;font-size:12px}
  .notice.warn{border-color:#76552b;color:#e7c392;background:#35251045}.notice.error{border-color:#a65252;color:#ffc4c4}
  .console-heading{display:flex;align-items:center;justify-content:space-between;margin:25px 0 13px;gap:12px}.console-heading h2{font-size:18px;font-weight:500;margin:0}
  .console{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}.console-panel{padding:16px}.console-panel h3{display:flex;align-items:center;gap:9px;font-size:14px;font-weight:500;margin:0 0 8px}.console-panel h3 ha-icon{color:#35b8ff}
  .console-panel .control-buttons{margin-top:12px}.console-panel .control-buttons button{font-size:12px;padding:8px 10px;flex-grow:1}.console-panel .muted{min-height:18px}
  .unsupported{opacity:.67}.unsupported .control-buttons button{flex:1}.status-line{font-size:12px;color:#8eb7d8;padding:11px 0;display:flex;gap:12px;flex-wrap:wrap}
  .command-status{border:1px solid #154f73;border-radius:11px;margin-top:18px;padding:14px;font-size:12px;color:#9acbea}.command-status strong{color:#d5e9ff}
  .footer{display:flex;justify-content:space-between;gap:15px;flex-wrap:wrap;color:#5e91b8;font-size:10px;letter-spacing:1px;border-top:1px solid #123f5c;margin-top:24px;padding-top:15px}
  .device-select{max-width:250px;width:auto}.modal{position:fixed;inset:0;background:#000b;z-index:1000;display:grid;place-items:center;padding:20px}
  .dialog{max-width:480px;width:100%;padding:25px;border:1px solid #2589c1;border-radius:18px;background:#061a2d;box-shadow:0 15px 80px #0009}.dialog h2{margin:0 0 14px;font-size:22px}.dialog p{color:#a4c8e8}
  .dialog label{display:flex;gap:10px;align-items:flex-start;margin:20px 0;font-size:14px}.dialog input{margin-top:5px;accent-color:#008dff;width:18px;height:18px}.dialog-actions{display:flex;justify-content:flex-end;gap:10px}
  .empty{text-align:center;padding:60px 20px}.empty ha-icon{--mdc-icon-size:50px;color:#2ab6ff}
  .tyres{display:grid;grid-template-columns:1fr 1fr;gap:8px;padding:10px 16px 16px}.tyre{font-size:11px;color:#80aacc}.tyre strong{display:block;font-size:15px;color:#cae7ff}
  @media(max-width:1100px){.shell{padding:20px}.dashboard{grid-template-columns:minmax(200px,.8fr) minmax(310px,1.2fr)}.climate-panel{grid-column:1/-1}.climate-body{display:grid;grid-template-columns:1fr 1fr;gap:0 25px}.climate-body .temperature{grid-row:1/3}.climate-body .cabin{grid-column:1/-1}.console{grid-template-columns:repeat(3,minmax(0,1fr))}}
  @media(max-width:700px){.shell{padding:15px;border-radius:12px}.dashboard{grid-template-columns:1fr}.battery-panel .gauge{width:170px;height:170px;margin:18px auto 0}.battery-panel .gauge-value{font-size:45px}.battery-panel .stats{display:grid;grid-template-columns:1fr 1fr;gap:0 15px}.battery-panel .stat:last-child{grid-column:1/-1}.car-scene{height:390px}.console{grid-template-columns:1fr 1fr}.climate-panel{grid-column:auto}.climate-body{display:block}header{gap:10px}.clock{font-size:14px}.header-meta .muted{display:none}nav button,.settings{padding:8px 10px;font-size:12px}.settings{margin-left:0}.eyebrow{letter-spacing:1.7px;font-size:9px}.console-panel{padding:13px}.dialog{padding:20px}}
  @media(prefers-reduced-motion:no-preference){button{transition:background .15s,border-color .15s,opacity .15s}}
`;

class GwmThailandCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = {};
    this._registry = null;
    this._tab = "overview";
    this._temperature = 24;
    this._duration = 10;
    this._leftLevel = 0;
    this._rightLevel = 0;
    this._pending = null;
    this._busy = false;
    this._error = "";
    this.shadowRoot.addEventListener("click", event => this._click(event));
    this.shadowRoot.addEventListener("change", event => this._change(event));
    this.shadowRoot.addEventListener("keydown", event => {
      if (event.key === "Escape" && !this._busy) this._closeDialog();
    });
  }

  setConfig(config) {
    if (!config || typeof config !== "object") throw new Error("Haval card requires a configuration");
    if (config.device_id !== undefined && typeof config.device_id !== "string")
      throw new Error("Haval device_id must be a string");
    this._config = config;
    this._render();
  }

  set hass(hass) {
    const reconnected = this._hass?.connected === false && hass?.connected;
    this._hass = hass;
    if (reconnected) this._registryFailed = false;
    if (!this._registry || reconnected) this._loadRegistry();
    if (!this._pending) this._render();
  }

  _loadRegistry() {
    if (this._loading || this._registryFailed || !this._hass?.connected) return;
    this._loading = true;
    this._hass.callWS({ type: "config/entity_registry/list" }).then(rows => {
      this._registry = rows.filter(row => row.platform === "gwm_thailand_connect")
        .map(({ entity_id, device_id, platform }) => ({ entity_id, device_id, platform }));
      this._loading = false;
      if (!this._pending) this._render();
    }).catch(() => {
      this._loading = false;
      this._registryFailed = true;
      this._error = "Vehicle registry could not be read. Reload this page to try again.";
      if (!this._pending) this._render();
    });
  }

  connectedCallback() {
    this._timer = setInterval(() => {
      if (!this._pending) { this._loadRegistry(); this._render(); }
    }, 60000);
    this._render();
  }

  disconnectedCallback() { clearInterval(this._timer); }
  getCardSize() { return 12; }

  _vehicle() {
    const vehicles = groupVehicleSignals(this._registry || [], this._hass?.states || {});
    const selected = this._config.device_id || this._selectedDevice;
    return { vehicles, vehicle: selected ? vehicles.find(v => v.id === selected) : vehicles[0] };
  }

  _format(signals, key, unit = "") {
    const value = reportedValue(signals[key]);
    if (value === null) return "Not reported";
    const numeric = Number(value);
    const formatted = Number.isFinite(numeric)
      ? numeric.toLocaleString(undefined, { maximumFractionDigits: 1 }) : value;
    return escapeHtml(formatted + (unit ? " " + unit : ""));
  }

  _button(action, label, controls, primary = false) {
    const supported = controls.actions?.some(item => item.action === action);
    const enabled = supported && controls.enabled === true && this._hass?.connected
      && this._hass.services?.gwm_thailand_connect?.thailand_control && !this._busy;
    const reason = !supported ? "Not advertised for this vehicle"
      : controls.enabled !== true ? "Enable remote controls and enter your security PIN in GWM settings"
      : !this._hass?.connected ? "Home Assistant is disconnected"
      : !this._hass.services?.gwm_thailand_connect?.thailand_control ? "GWM control service is unavailable"
      : this._busy ? "A command is in progress" : "";
    return `<button data-action="${escapeHtml(action)}" ${enabled ? "" : "disabled"}
      title="${escapeHtml(reason)}" class="${primary ? "primary" : ""}">${escapeHtml(label)}</button>`;
  }

  _controlGroup(group, controls, signals) {
    const actions = (controls.actions || []).filter(item => item.group === group);
    const [title, icon] = GROUPS[group];
    let settings = "";
    if (group === "seats") {
      settings = `<label class="form-label">Driver / right ${this._levelSelect("right", this._rightLevel)}</label>
        <label class="form-label">Passenger / left ${this._levelSelect("left", this._leftLevel)}</label>
        <div class="muted">Apply sets both displayed front-seat levels. 0 = off.</div>`;
    }
    if (group === "location") settings = `<div class="muted">Reporting state not reported. Turning reporting off can prevent tracking in the official app.</div>`;
    if (group === "windows") settings = `<div class="muted">${[
      ["Front left", "window_front_left_open"], ["Front right", "window_front_right_open"],
      ["Rear left", "window_rear_left_open"], ["Rear right", "window_rear_right_open"],
    ].map(([label, key]) => `${label}: ${binaryLabel(signals[key], "Open", "Closed")}`).join("<br>")}</div>`;
    if (group === "sunroof") settings = `<div class="muted">Position not reported; closing is a separate confirmed command.</div>`;
    if (group === "seats") settings += `<div class="muted">Actual ventilation state is not reported.</div>`;
    if (group === "climate") settings = `<div class="muted">A/C: ${binaryLabel(signals.ac_active, "On", "Off")}. Purifier state not reported.</div>`;
    const buttons = actions.map(item => this._button(item.action, item.label, controls)).join("");
    return `<section class="panel console-panel"><h3><ha-icon icon="${icon}"></ha-icon>${title}</h3>
      ${settings || '<div class="muted">Explicit commands; each requires confirmation.</div>'}
      <div class="control-buttons">${buttons || "<span class=muted>Not advertised for this vehicle</span>"}</div></section>`;
  }

  _levelSelect(side, current) {
    return `<select data-level="${side}" aria-label="${side === "right" ? "Driver" : "Passenger"} ventilation level">
      ${[0, 1, 2, 3].map(n => `<option value="${n}" ${n === current ? "selected" : ""}>${n === 0 ? "Off" : "Level " + n}</option>`).join("")}</select>`;
  }

  _render() {
    if (!this.shadowRoot || !this._hass) return;
    const { vehicles, vehicle } = this._vehicle();
    const signals = vehicle?.signals || {};
    const controls = signals.soc?.attributes?.gwm_controls || { status: "unavailable", enabled: false, actions: [] };
    const soc = reportedValue(signals.soc);
    const percent = soc !== null && Number.isFinite(Number(soc)) && Number(soc) >= 0 && Number(soc) <= 100 ? Number(soc) : null;
    const lock = binaryLabel(signals.lock_open, "Unlocked", "Locked");
    const ac = binaryLabel(signals.ac_active, "On", "Off");
    const reportedAt = signals.soc?.attributes?.gwm_reported_at;
    const reportedDate = typeof reportedAt === "string" ? new Date(reportedAt) : null;
    const cloudTime = reportedDate && Number.isFinite(reportedDate.getTime())
      ? reportedDate.toLocaleString() : "Not reported";
    const now = new Date();
    const time = now.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
    const date = now.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
    const selectors = vehicles.length > 1 ? `<select class="device-select" data-device aria-label="Haval vehicle">
      ${vehicles.map((v, i) => `<option value="${escapeHtml(v.id)}" ${v.id === vehicle?.id ? "selected" : ""}>Haval ${i + 1}</option>`).join("")}</select>` : "";
    const tabs = [["overview", "mdi:home-outline", "Overview"], ["climate", "mdi:fan", "Climate"],
      ["console", "mdi:console", "Console"]];
    const header = `<header><div><h1>GWM THAILAND</h1><div class="eyebrow">MY GWM / HOME ASSISTANT</div></div>
      <div class="header-meta"><div class="clock">${escapeHtml(time)}</div><div class="muted">${escapeHtml(date)}</div>
      <span class="badge">${this._hass.connected && vehicle ? "Cloud readings" : "Waiting for connection"}</span></div></header>
      <nav aria-label="Haval sections">${tabs.map(([tab, icon, label]) =>
        `<button data-tab="${tab}" aria-current="${this._tab === tab}"><ha-icon icon="${icon}"></ha-icon>${label}</button>`).join("")}
        ${selectors}<a class="settings" href="${SETTINGS}"><ha-icon icon="mdi:cog-outline"></ha-icon>GWM settings</a></nav>`;
    if (!vehicle) {
      this.shadowRoot.innerHTML = `<style>${CSS}</style><div class="shell">${header}<div class="empty">
        <ha-icon icon="mdi:car-connected"></ha-icon><h2>${this._loading ? "Loading your Haval" : "No connected Thai vehicle"}</h2>
        <p>${escapeHtml(this._error || "Open GWM settings to finish setup. No sample readings are displayed.")}</p>
        <a href="${SETTINGS}">Open GWM settings</a></div></div>`;
      return;
    }
    const notices = `<div class="notice ${controls.enabled ? "" : "warn"}"><ha-icon icon="${controls.enabled ? "mdi:shield-check-outline" : "mdi:shield-lock-outline"}"></ha-icon>
      <span>${controls.status !== "advertised" ? "Control capabilities are unavailable. Telemetry remains separate; no controls are enabled."
        : controls.enabled ? "Remote controls are enabled. Every action requires confirmation; GWM acceptance is not proof the vehicle has completed it."
          : "Controls are locked. In GWM settings, choose Configure, enable remote commands and enter your vehicle security PIN privately. Nothing is sent by opening this dashboard."}</span></div>
      ${this._error ? `<div role="alert" class="notice error">${escapeHtml(this._error)}</div>` : ""}`;
    const door = (key, label, cls) => {
      const state = binaryLabel(signals[key], "Open", "Closed");
      return `<button class="hotspot ${cls} ${state === "Open" ? "alert" : state === "Not reported" ? "unknown" : ""}"
        data-info="${escapeHtml(signals[key]?.entity_id || "")}" aria-label="${label}: ${state}">
        <ha-icon icon="mdi:car-door"></ha-icon>${label}<br>${state}</button>`;
    };
    const stat = (icon, label, value) => `<div class="stat"><ha-icon icon="${icon}"></ha-icon><div><span>${label}</span><strong>${value}</strong></div></div>`;
    const dashboard = `<div class="dashboard">
      <section class="panel battery-panel"><div class="panel-title"><ha-icon icon="mdi:battery-charging"></ha-icon>Battery & range</div>
        <div class="gauge" style="--arc:${percent === null ? 0 : percent * 2.7}deg" role="img" aria-label="Battery ${percent === null ? "not reported" : percent + " percent"}">
          <div class="gauge-text"><div class="gauge-value">${percent === null ? "--" : this._format(signals, "soc")}<small>${percent === null ? "" : "%"}</small></div>
          <div class="gauge-caption">BATTERY SOC / CLOUD REPORTED</div></div></div>
        <div class="stats">${stat("mdi:map-marker-distance", "Reported range", this._format(signals, "range_km", "km"))}
          ${stat("mdi:counter", "Odometer", this._format(signals, "odometer_km", "km"))}
          ${stat("mdi:ev-station", "Charging", escapeHtml(reportedValue(signals.charging_status)?.replace(/_/g, " ") || "Not reported"))}</div>
      </section>
      <section class="panel vehicle-panel"><div class="vehicle-status"><ha-icon icon="${lock === "Locked" ? "mdi:lock-outline" : "mdi:lock-open-outline"}"></ha-icon>Doors ${lock.toLowerCase()}</div>
        <div class="car-scene"><div class="car-image car-outline" role="img" aria-label="Original generic vehicle diagram"><div class="roof-panel"></div><div class="wheel"></div></div>
          ${door("door_front_passenger_open", "Passenger", "door-fl")}${door("door_front_driver_open", "Driver", "door-fr")}
          ${door("door_rear_passenger_side_open", "Rear left", "door-rl")}${door("door_rear_driver_side_open", "Rear right", "door-rr")}
          <button class="hotspot roof" data-group="sunroof"><ha-icon icon="mdi:car-convertible"></ha-icon>Sunroof controls</button>
          <button class="hotspot boot ${reportedValue(signals.trunk_open) === "on" ? "alert" : ""}" data-group="tailgate">
            <ha-icon icon="mdi:car-back"></ha-icon>Boot: ${binaryLabel(signals.trunk_open, "Open", "Closed")}</button>
        </div><div class="car-links"><button class="chip" data-group="locks"><ha-icon icon="mdi:lock-outline"></ha-icon>Locks</button>
          <button class="chip" data-group="windows"><ha-icon icon="mdi:car-door"></ha-icon>Windows</button>
          <button class="chip" data-group="seats"><ha-icon icon="mdi:car-seat-cooler"></ha-icon>Seats</button></div>
        <div class="tyres">${[["front_left", "Front left"], ["front_right", "Front right"], ["rear_left", "Rear left"], ["rear_right", "Rear right"]]
          .map(([pos, label]) => `<div class="tyre">${label} tyre<strong>${this._format(signals, "tire_pressure_" + pos + "_kpa", "kPa")}</strong></div>`).join("")}</div>
      </section>
      <section class="panel climate-panel"><div class="panel-title"><ha-icon icon="mdi:fan"></ha-icon>Climate control <span class="badge">${ac}</span></div>
        <div class="climate-body"><div class="temperature"><button class="round" data-temp="-1" aria-label="Decrease requested temperature" ${this._temperature <= 16 ? "disabled" : ""}>-</button>
          <strong>${this._temperature}<small>&deg;C</small></strong><button class="round" data-temp="1" aria-label="Increase requested temperature" ${this._temperature >= 32 ? "disabled" : ""}>+</button></div>
          <div class="muted">Requested setpoint, not a cabin measurement</div>
          <label class="form-label">Requested run time <select data-duration aria-label="Requested run time">
            ${[5,10,15,20,25,30].map(n => `<option value="${n}" ${n === this._duration ? "selected" : ""}>${n} min</option>`).join("")}</select></label>
          <div class="control-buttons">${this._button("climate_on", "A/C on", controls, true)}${this._button("climate_off", "A/C off", controls)}</div>
          <div class="cabin"><span>Reported cabin temperature</span><span>${this._format(signals, "interior_temperature_c", "\u00b0C")}</span></div>
          <div class="notice warn"><span>Engine start/stop is not advertised for this vehicle. A/C is not presented as an engine command.</span></div>
          <div class="control-buttons"><button disabled title="Not advertised by GWM for this car">Engine start</button><button disabled title="Not advertised by GWM for this car">Engine stop</button></div>
        </div></section></div>`;
    const allGroups = Object.keys(GROUPS);
    const shownGroups = this._tab === "climate" ? ["climate", "seats"] : allGroups;
    const console = `<div class="console-heading" id="console-heading"><h2>Vehicle command console</h2><span class="muted">Advertised functions / owner confirmation required</span></div>
      <div class="console">${shownGroups.map(group => this._controlGroup(group, controls, signals)).join("")}
      ${this._tab === "climate" ? "" : `<section class="panel console-panel unsupported"><h3><ha-icon icon="mdi:engine-outline"></ha-icon>Engine & driving</h3>
        <div class="muted">Engine, drive modes, steering heat, parking sensors and camera controls are not advertised.</div>
        <div class="control-buttons"><button disabled>Not available</button></div></section>
        <section class="panel console-panel unsupported"><h3><ha-icon icon="mdi:ev-station"></ha-icon>Charging management</h3>
        <div class="muted">Direct start/stop is not advertised. Schedule editing and smart precooling remain in the official app.</div>
        <div class="control-buttons"><button disabled>Start charge</button><button disabled>Stop charge</button></div></section>`}</div>`;
    this.shadowRoot.innerHTML = `<style>${CSS}</style><div class="shell">${header}${notices}
      ${this._tab === "overview" || this._tab === "climate" ? dashboard : ""}${console}
      <div class="command-status" role="status"><strong>Command result</strong><br>${escapeHtml(reportedValue(signals.command_status) || "No vehicle command has been run.")}</div>
      <div class="status-line">Cloud timestamp: ${escapeHtml(cloudTime)}. Cached readings; not proof the vehicle is online now.
        <a href="/gwm-thailand/details">All readings & reported timestamps</a></div>
      <footer class="footer"><span>YOUR HAVAL / CONNECTED HOME</span><span>NO COMMANDS SENT ON PAGE LOAD / NO SAMPLE TELEMETRY</span></footer>
      ${this._pending ? this._dialogMarkup() : ""}</div>`;
  }

  _dialogMarkup() {
    const details = this._pending;
    return `<div class="modal"><section class="dialog" role="dialog" aria-modal="true" aria-labelledby="confirm-title">
      <h2 id="confirm-title">${escapeHtml(details.label)}?</h2><p>This sends a physical vehicle or privacy command to your selected Haval.</p>
      ${details.action === "climate_on" ? `<p>Requested A/C: ${this._temperature}&deg;C for ${this._duration} minutes.</p>` : ""}
      ${details.action === "seat_ventilation" ? `<p>Driver/right level ${this._rightLevel}; passenger/left level ${this._leftLevel}, for ${this._duration} minutes.</p>` : ""}
      ${details.action === "location_off" ? "<p>Location reporting will be disabled. This may prevent tracking in the official app.</p>" : ""}
      <p>Confirm the car is parked, the area is clear and safe, and no occupants or objects could be trapped by a closing window, sunroof or tailgate.</p>
      <label><input type="checkbox" data-confirm-check>I confirm this command is safe to send.</label>
      <div class="dialog-actions"><button class="cancel" data-cancel>Cancel</button><button class="send" data-send disabled>Send command</button></div>
    </section></div>`;
  }

  _closeDialog() {
    const action = this._pending?.action;
    this._pending = null;
    this._render();
    if (action) this.shadowRoot.querySelector(`[data-action="${action}"]`)?.focus();
  }

  _click(event) {
    const target = event.target.closest("button,a");
    if (!target || target.disabled || this._busy) return;
    if (target.hasAttribute("data-cancel")) return this._closeDialog();
    if (target.hasAttribute("data-send")) return void this._send();
    if (target.dataset.tab) { this._tab = target.dataset.tab; this._render(); }
    if (target.dataset.temp) { this._temperature = Math.max(16, Math.min(32, this._temperature + Number(target.dataset.temp))); this._render(); }
    if (target.dataset.group) {
      this._tab = target.dataset.group === "climate" ? "climate" : "console";
      this._render();
      this.shadowRoot.getElementById("console-heading")?.scrollIntoView({ block: "nearest" });
    }
    if (target.dataset.info) this.dispatchEvent(new CustomEvent("hass-more-info", {
      detail: { entityId: target.dataset.info }, bubbles: true, composed: true,
    }));
    if (target.dataset.action) {
      const { vehicle } = this._vehicle();
      const controls = vehicle?.signals.soc.attributes.gwm_controls;
      const spec = controls?.actions?.find(item => item.action === target.dataset.action);
      if (!spec || controls.enabled !== true) return;
      this._pending = { action: spec.action, label: spec.label, device: vehicle.id };
      this._render();
      this.shadowRoot.querySelector("[data-confirm-check]")?.focus();
    }
  }

  _change(event) {
    const input = event.target;
    if (input.hasAttribute("data-confirm-check")) {
      this.shadowRoot.querySelector("[data-send]").disabled = input.checked !== true;
    } else if (input.hasAttribute("data-duration")) {
      this._duration = Number(input.value);
    } else if (input.dataset.level) {
      if (input.dataset.level === "right") this._rightLevel = Number(input.value);
      else this._leftLevel = Number(input.value);
    } else if (input.hasAttribute("data-device")) {
      this._selectedDevice = input.value;
      this._pending = null;
      this._render();
    }
  }

  async _send() {
    if (!this._pending || this._busy || !this.shadowRoot.querySelector("[data-confirm-check]")?.checked) return;
    const pending = this._pending;
    const { vehicle } = this._vehicle();
    const controls = vehicle?.signals.soc.attributes.gwm_controls;
    if (vehicle?.id !== pending.device || controls?.enabled !== true || !this._hass?.connected
      || !controls.actions?.some(item => item.action === pending.action)) {
      this._error = "Vehicle connection or control permission changed. No command was sent.";
      this._closeDialog();
      return;
    }
    this._busy = true;
    this._pending = null;
    this._error = "";
    this._render();
    try {
      await this._hass.callService("gwm_thailand_connect", "thailand_control", {
        device_id: pending.device, action: pending.action, confirmed: true,
        temperature: this._temperature, duration_minutes: this._duration,
        front_left_level: this._leftLevel, front_right_level: this._rightLevel,
      });
    } catch (error) {
      this._error = `Command not confirmed: ${error?.message || "the request failed"}. Check command status before retrying; a transport error can leave the outcome unknown.`;
    } finally {
      this._busy = false;
      this._render();
    }
  }
}

if (!customElements.get("gwm-thailand-card")) customElements.define("gwm-thailand-card", GwmThailandCard);
if (typeof window !== "undefined") {
  window.customCards = window.customCards || [];
  if (!window.customCards.some(card => card.type === "gwm-thailand-card"))
    window.customCards.push({ type: "gwm-thailand-card", name: "Haval H6", description: "Live Thai GWM readings and confirmed controls" });
}
