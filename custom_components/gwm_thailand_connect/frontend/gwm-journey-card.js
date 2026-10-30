import {escapeHtml as esc} from "./shared.js";

const DOMAINS=new Set(["gwm_thailand_connect","gwm_ora"]);
const LABELS={temporary_stop:"Temporary stop",parking:"Parked",unconfirmed_stop:"Stop · unconfirmed"};
const COLORS={temporary_stop:"#36a6f2",parking:"#26b177",unconfirmed_stop:"#efa935"};
const DOORS={door_front_driver_open:"Driver",door_front_passenger_open:"Passenger",
  door_rear_driver_side_open:"Rear right",door_rear_passenger_side_open:"Rear left",trunk_open:"Boot"};
const CSS_URL=new URL("./vendor/leaflet.css",import.meta.url);
const WEEK=7*86400000;
const ATTRIBUTION='© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">OpenStreetMap contributors</a> · Protomaps';
const CAR='<svg viewBox="0 0 40 40" aria-hidden="true"><path d="M10 15l4-7h12l4 7 3 3v12H7V18z" fill="#087ada" stroke="#fff" stroke-width="2"/><path d="M14 15h12l-2-4h-8z" fill="#c8eeff"/><circle cx="12" cy="24" r="2" fill="white"/><circle cx="28" cy="24" r="2" fill="white"/><path d="M10 30v3m20-3v3" stroke="#064376" stroke-width="4"/></svg>';

export function localTileTemplate(value){
  if(value===undefined || value==="")return null;
  if(typeof value!=="string" || !/^\/local\/[A-Za-z0-9_{}./-]+$/.test(value) || value.includes("..")
    || !["{z}","{x}","{y}"].every(key=>value.includes(key))){
    throw new Error("Map tiles must be a local /local/ template with {z}, {x}, {y}");
  }
  return value;
}

export function localMapArchive(value="/local/gwm-maps/thailand.pmtiles"){
  if(typeof value!=="string" || !/^\/local\/[A-Za-z0-9_./-]+\.pmtiles$/.test(value) || value.includes("..")){
    throw new Error("The OpenStreetMap archive must be a same-host /local/ PMTiles file");
  }
  return value;
}

export function mapLanguage(value="th"){
  if(!["th","en"].includes(value))throw new Error("Map language must be th or en");
  return value;
}

export function validPosition(value){
  return !!value && typeof value.latitude==="number" && typeof value.longitude==="number"
    && Number.isFinite(value.latitude) && Number.isFinite(value.longitude)
    && Math.abs(value.latitude)<=85.05112878 && Math.abs(value.longitude)<=180
    && !(value.latitude===0 && value.longitude===0);
}

export function freshCurrent(value,now=Date.now()){
  if(value?.availability!=="available" || !validPosition(value.position))return null;
  const reported=Date.parse(value.reported_at),expires=Date.parse(value.expires_at);
  if(!Number.isFinite(reported) || !Number.isFinite(expires) || reported>now+30000
    || now-reported>300000 || expires<=now || expires>reported+300000)return null;
  return value;
}

export function elapsed(value){
  if(typeof value!=="number" || !Number.isFinite(value) || value<0)return "Not reported";
  const seconds=Math.round(value),minutes=Math.floor(seconds/60);
  return minutes>=60 ? `${Math.floor(minutes/60)}h ${minutes%60}m` : minutes ? `${minutes}m ${seconds%60}s` : `${seconds}s`;
}
const time=value=>Number.isFinite(Date.parse(value))?new Date(value).toLocaleTimeString([],{hour:"2-digit",minute:"2-digit"}):"—";
const battery=value=>typeof value==="number" && Number.isFinite(value)?`${value.toLocaleString()}%`:"—";
const bool=(value,yes,no)=>value===true?yes:value===false?no:"—";
const statusText=current=>{
  const open=Object.values(current.doors || {}).filter(value=>value===true).length;
  const reported=Object.values(current.doors || {}).filter(value=>typeof value==="boolean").length;
  return [
    `Battery ${battery(current.battery)}`,bool(current.locked,"Locked","Unlocked"),
    open?`${open} door${open===1?"":"s"} open`:reported===5?"Doors closed":reported?"Doors partially reported":"Doors —",
    `A/C ${bool(current.ac_active,"on","off")}`,
  ];
};

const CSS=`
  :host{display:block;color:var(--primary-text-color,#17314d);font:14px/1.45 system-ui,Segoe UI,sans-serif}
  *{box-sizing:border-box}button,select{font:inherit;background:var(--card-background-color,#fff);color:inherit;border:1px solid #8da6ba55;border-radius:9px;padding:8px 12px}
  button{cursor:pointer}button:disabled{opacity:.45;cursor:not-allowed}button:focus-visible,select:focus-visible{outline:2px solid #1685d0;outline-offset:3px}
  .shell{background:var(--card-background-color,#fff);border:1px solid #8da6ba44;border-radius:14px;overflow:hidden}
  header{display:flex;align-items:center;gap:10px;padding:12px 15px;flex-wrap:wrap}h1{font-size:19px;margin:0 auto 0 0}.tools{display:flex;align-items:center;gap:8px}
  .viewport{position:relative;min-height:440px;height:clamp(440px,72vh,850px);background:#e8eef2}.map{position:absolute;inset:0}.map:not(.ready){visibility:hidden}
  .blank{position:absolute;inset:0;display:grid;place-content:center;text-align:center;color:#63788c;pointer-events:none}.blank strong{font-size:17px;font-weight:500}.blank small{display:block;font-size:12px;margin-top:5px}
  .blank.hidden{display:none}.map-actions{position:absolute;top:12px;right:12px;z-index:500;display:flex;gap:7px}.map-actions button{background:#fffc;color:#17314d;box-shadow:0 2px 6px #0001}
  .status{position:absolute;left:12px;right:12px;bottom:32px;z-index:500;display:flex;gap:7px;flex-wrap:wrap;pointer-events:none}
  .chip{display:inline-flex;align-items:center;gap:5px;border-radius:20px;padding:7px 11px;background:#ffffffed;color:#17314d;box-shadow:0 1px 5px #0002;font-size:12px}
  .chip.warn{background:#fff0d4;color:#79510e}.chip.error{background:#ffe5e5;color:#8f2d2d}
  .credit{position:absolute;right:0;bottom:0;z-index:500;background:#fffd;padding:2px 6px;color:#294253;font-size:11px}.credit a{color:#235e92}
  .car-marker{width:42px;height:42px;filter:drop-shadow(0 2px 3px #0006)}.car-marker svg{display:block;width:42px;height:42px}
  .stop-marker{display:grid;place-items:center;width:27px;height:27px;border:2px solid white;border-radius:50%;font-weight:700;color:white;box-shadow:0 1px 4px #0006;background:var(--stop-color)}
  .popup{color:#17314d;min-width:190px}.popup strong{display:block;font-size:15px;margin-bottom:7px}.popup div{display:flex;justify-content:space-between;gap:18px;margin:5px 0}.popup small{color:#607389}
  .history{border-top:1px solid #8da6ba33;padding:10px 15px}.history summary{cursor:pointer;font-size:13px}.stop-list{display:flex;gap:8px;overflow-x:auto;padding:12px 0 3px}.stop-list button{text-align:left;white-space:nowrap;min-width:150px}.stop-list small{display:block;font-size:11px;color:#63788c}
  .history-options{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-top:12px}.history-options label{display:flex;align-items:center;gap:7px;font-size:12px}.history-options button{font-size:12px;color:#993f3f}
  .leaflet-control-attribution{display:none}.leaflet-popup-content{margin:12px 15px}.leaflet-control-zoom a{color:#17314d}
  @media(max-width:600px){header{padding:10px}h1{font-size:17px}.viewport{min-height:440px;height:70vh}.tools{gap:5px}button,select{padding:7px 9px}.map-actions{right:8px}.status{left:8px;right:8px;bottom:32px}.chip{padding:6px 9px}}
`;

class GwmJourneyCard extends HTMLElement {
  constructor(){
    super();this.attachShadow({mode:"open"});this._domain="gwm_thailand_connect";this._hours=24;
    this._requestId=0;this._drawId=0;this._mapGeneration=0;this._devices=[];this._registry=null;this._history=null;this._error="";this._fitNext=true;
    this.shadowRoot.innerHTML=`<link rel="stylesheet" href="${CSS_URL.href}"><style>${CSS}</style><section class="shell">
      <header><h1>Haval journeys</h1><span class="tools"><select data-device aria-label="Vehicle" hidden></select><select data-hours aria-label="History period"><option value="12">12 h</option><option value="24" selected>24 h</option><option value="72">3 days</option><option value="168">7 days</option></select><button data-refresh title="Read cached cloud status; does not wake the car">Refresh</button></span></header>
      <div class="viewport"><div class="map" aria-label="OpenStreetMap vehicle journey map"></div><div class="blank"><strong>No recent GPS</strong><small>Waiting for a valid vehicle location</small></div>
      <div class="map-actions"><button data-locate title="Show recent car location" disabled>Car</button><button data-fit title="Fit recorded journey" disabled>Journey</button></div><div class="status"></div><div class="credit">${ATTRIBUTION}</div></div>
      <details class="history"><summary>Stops · last 7 days</summary><div class="stop-list"></div><div class="history-options"><label><input type="checkbox" data-clear-check>Clear this car’s history</label><button data-clear disabled>Delete</button></div></details></section>`;
    this.shadowRoot.addEventListener("click",event=>this._click(event));
    this.shadowRoot.addEventListener("change",event=>this._change(event));
  }
  setConfig(config){
    if(!config || typeof config!=="object")throw new Error("Journey card configuration required");
    const domain=config.domain || "gwm_thailand_connect";
    if(!DOMAINS.has(domain))throw new Error("Unsupported GWM integration domain");
    if(config.device_id!==undefined && typeof config.device_id!=="string")throw new Error("device_id must be a HA registry ID");
    this._tiles=localTileTemplate(config.local_tile_url);
    this._archive=localMapArchive(config.map_archive);
    const language=mapLanguage(config.map_language);
    if(this._mapLanguage!==undefined && this._mapLanguage!==language)this._resetMap();
    this._mapLanguage=language;
    this._domain=domain;this._config=config;
    if(this.isConnected && this._history)this._updateMap();
  }
  set hass(value){
    const first=!this._hass,reconnected=this._hass?.connected===false && value?.connected;
    const disconnected=this._hass?.connected===true && value?.connected!==true;
    this._hass=value;
    if(first || reconnected)this._load();
    else if(disconnected)this._updateMap();
    else this._renderStatus();
  }
  connectedCallback(){
    this._poll=setInterval(()=>{if(!this._loading && !document.hidden)this._load();},60000);
    this._freshness=setInterval(()=>{
      if(this._displayedCurrent && !this._current())this._updateMap();else this._renderStatus();
    },15000);
    if(this._hass?.connected)this._load();
  }
  disconnectedCallback(){
    clearInterval(this._poll);clearInterval(this._freshness);this._requestId++;
    this._resetMap();
  }
  _resetMap(){
    this._drawId++;this._mapGeneration++;
    this._mapAbort?.abort();this._mapAbort=null;this._mapCreating=null;this._loading=false;
    this._resize?.disconnect();this._map?.remove();this._map=null;this._runtime=null;
    this._layers=null;this._baseLayer=null;this._car=null;this._displayedCurrent=false;this._fitNext=true;
  }
  getCardSize(){return 12;}
  async _load(refreshCloud=false){
    if(!this._hass?.connected){this._renderStatus();return;}
    const request=++this._requestId;this._loading=true;this._error="";this._renderStatus();
    try{
      const rows=this._registry?.length?this._registry:await this._hass.callWS({type:"config/entity_registry/list"});
      if(request!==this._requestId)return;
      this._registry=rows;
      this._devices=[...new Set(rows.filter(row=>row.platform===this._domain && row.device_id).map(row=>row.device_id))];
      const device=this._config?.device_id || this._device || this._devices[0];
      if(!device){this._history=null;this._error="Connect GWM in integration settings";return;}
      this._device=device;
      const data=await this._hass.callWS({
        type:this._domain+"/journeys",device_id:device,hours:this._hours,
        ...(refreshCloud===true?{refresh_cloud:true}:{}),
      });
      if(request!==this._requestId)return;
      this._history=data;
    }catch(error){if(request===this._requestId){this._error=error?.message || "History unavailable";}}
    finally{
      if(request===this._requestId){this._loading=false;this._renderStatus();await this._updateMap();}
    }
  }
  _current(){
    return this._hass?.connected && !this._error?freshCurrent(this._history?.current):null;
  }
  _segments(){
    const now=Date.now(),cutoff=now-Math.min(this._hours*3600000,WEEK);
    return (this._history?.segments || []).map(segment=>segment.filter(point=>
      validPosition(point) && Date.parse(point.time)>cutoff && Date.parse(point.time)<=now+30000)).filter(segment=>segment.length);
  }
  _stops(){
    const cutoff=Date.now()-Math.min(this._hours*3600000,WEEK);
    return (this._history?.stops || []).filter(stop=>validPosition(stop.position) && Date.parse(stop.start)>cutoff);
  }
  _renderStatus(){
    if(!this.shadowRoot)return;
    const current=this._current(),data=this._history;
    const messages=[];
    if(this._error)messages.push(`<span class="chip error" role="alert">${esc(this._error)}</span>`);
    else if(!this._hass?.connected)messages.push('<span class="chip warn">Disconnected</span>');
    else if(current)messages.push(...statusText(current).map(label=>`<span class="chip">${esc(label)}</span>`),
      `<span class="chip" title="Manufacturer GPS time">Updated ${esc(time(current.reported_at))}</span>`);
    else messages.push('<span class="chip warn">No recent GPS</span>');
    if(data?.storage_warning)messages.push('<span class="chip error">History storage error</span>');
    if(this._mapError)messages.push('<span class="chip error">Map data unavailable</span>');
    if(data?.status==="disabled")messages.push('<span class="chip warn">History recording off</span>');
    this.shadowRoot.querySelector(".status").innerHTML=messages.join("");
    this.shadowRoot.querySelector("[data-refresh]").disabled=!!this._loading;
    const devices=this.shadowRoot.querySelector("[data-device]");
    devices.hidden=!!this._config?.device_id || this._devices.length<2;
    devices.innerHTML=this._devices.map((id,index)=>`<option value="${esc(id)}" ${id===this._device?"selected":""}>Haval ${index+1}</option>`).join("");
    this.shadowRoot.querySelector("[data-locate]").disabled=!current;
    const stops=this._stops();
    this.shadowRoot.querySelector(".stop-list").innerHTML=[...stops].reverse().map(stop=>
      `<button data-stop="${esc(stop.id)}">${esc(LABELS[stop.kind] || "Stop")} · ${esc(time(stop.start))}<small>${esc(elapsed(stop.observed_duration_seconds))} · Battery ${esc(battery(stop.battery_latest))}</small></button>`).join("") || '<small>No recorded stops</small>';
    this.shadowRoot.querySelector(".history summary").textContent=`Stops (${stops.length}) · rolling 7 days`;
  }
  async _ensureMap(){
    if(!this.isConnected)return false;
    if(this._map)return true;
    if(this._mapCreating)return this._mapCreating;
    const generation=this._mapGeneration,abort=new AbortController();
    const active=()=>this.isConnected && this._mapGeneration===generation;
    this._mapAbort=abort;
    const creating=(async()=>{
      try{
        const runtime=await import("./vendor/map-runtime.js?v=0.3.4");
        if(!active())return false;
        if(!this._tiles){
          const probe=await fetch(new URL(this._archive,location.origin),{
            headers:{Range:"bytes=0-7"},credentials:"same-origin",
            signal:AbortSignal.any([abort.signal,AbortSignal.timeout(15000)]),
          });
          if(!active()){await probe.body?.cancel();return false;}
          if(probe.status!==206 || probe.headers.get("content-range")?.startsWith("bytes 0-7/")!==true){
            await probe.body?.cancel();
            throw new Error("Local OSM archive unavailable or byte ranges unsupported");
          }
          const header=new Uint8Array(await probe.arrayBuffer());
          if(!active())return false;
          if(header.length!==8 || new TextDecoder().decode(header.slice(0,7))!=="PMTiles" || header[7]!==3){
            throw new Error("Local OSM archive is not PMTiles v3");
          }
        }
        if(!active())return false;
        const {L,placeMapLayer}=runtime;
        this._runtime=runtime;this._mapError=false;
        this._map=L.map(this.shadowRoot.querySelector(".map"),{
          attributionControl:false,zoomControl:true,preferCanvas:false,maxZoom:19,minZoom:3,
          fadeAnimation:false,zoomAnimation:false,markerZoomAnimation:false,
        });
        this._layers=L.layerGroup().addTo(this._map);
        const layer=this._tiles ? L.tileLayer(this._tiles,{maxZoom:19,keepBuffer:1})
          : placeMapLayer({url:new URL(this._archive,location.origin).href,flavor:"light",lang:this._mapLanguage,maxDataZoom:13,maxZoom:19});
        this._baseLayer=layer;
        layer.on("tileerror",()=>{
          if(active() && this._baseLayer===layer){this._mapError=true;this._renderStatus();}
        });
        layer.addTo(this._map);
        this._resize=new ResizeObserver(()=>this._map?.invalidateSize({pan:false}));
        this._resize.observe(this.shadowRoot.querySelector(".viewport"));
        return true;
      }catch(error){
        if(!active() || abort.signal.aborted)return false;
        this._resize?.disconnect();this._map?.remove();this._map=null;this._runtime=null;
        this._layers=null;this._baseLayer=null;
        this._error=error?.message || "Map could not load";this._renderStatus();
        return false;
      }finally{
        if(this._mapCreating===creating)this._mapCreating=null;
        if(this._mapAbort===abort)this._mapAbort=null;
      }
    })();
    this._mapCreating=creating;
    return creating;
  }
  _popup(current){
    return `<section class="popup"><strong>Haval</strong>${statusText(current).map(label=>`<div>${esc(label)}</div>`).join("")}
      ${Object.entries(current.doors || {}).filter(([,state])=>state===true).map(([key])=>`<div>${esc(DOORS[key] || "Door")} open</div>`).join("")}
      <small>GPS ${esc(time(current.reported_at))}</small></section>`;
  }
  async _updateMap(){
    const draw=++this._drawId;
    this._renderStatus();
    const current=this._current(),segments=this._segments(),stops=this._stops();
    const points=[...segments.flat(),...stops.map(stop=>stop.position),...(current?[current.position]:[])];
    this.shadowRoot.querySelector("[data-fit]").disabled=!points.length;
    this.shadowRoot.querySelector(".blank").classList.toggle("hidden",!!points.length);
    this.shadowRoot.querySelector(".map").classList.toggle("ready",!!points.length);
    if(!points.length){this._layers?.clearLayers();this._displayedCurrent=false;return;}
    if(!await this._ensureMap() || draw!==this._drawId)return;
    const {L}=this._runtime;this._layers.clearLayers();this._markers=new Map();
    for(const segment of segments)L.polyline(segment.map(p=>[p.latitude,p.longitude]),{
      color:"#087ada",weight:4,opacity:.75,dashArray:"8 4",
    }).addTo(this._layers);
    stops.forEach((stop,index)=>{
      const marker=L.marker([stop.position.latitude,stop.position.longitude],{
        icon:L.divIcon({className:"",html:`<span class="stop-marker" style="--stop-color:${COLORS[stop.kind] || COLORS.unconfirmed_stop}">${index+1}</span>`,iconSize:[27,27],iconAnchor:[13,13]}),
      }).bindPopup(`<section class="popup"><strong>${esc(LABELS[stop.kind] || "Stop")}</strong><div>${esc(time(stop.start))} · ${esc(elapsed(stop.observed_duration_seconds))}</div><div>Battery ${esc(battery(stop.battery_arrival))} → ${esc(battery(stop.battery_latest))}</div>
        ${(this._history?.doors || []).filter(door=>door.stop_id===stop.id).map(door=>`<div>${esc(DOORS[door.door] || "Door")}<span>${door.estimated_duration_seconds===null?"Unknown interval":"~"+esc(elapsed(door.estimated_duration_seconds))}</span></div>`).join("")}</section>`).addTo(this._layers);
      this._markers.set(stop.id,marker);
    });
    if(current){
      this._car=L.marker([current.position.latitude,current.position.longitude],{
        icon:L.divIcon({className:"car-marker",html:CAR,iconSize:[42,42],iconAnchor:[21,21]}),zIndexOffset:1000,
      }).bindPopup(this._popup(current)).addTo(this._layers);
    }else this._car=null;
    this._displayedCurrent=!!current;
    if(this._fitNext){
      const bounds=L.latLngBounds(points.map(point=>[point.latitude,point.longitude]));
      this._map.fitBounds(bounds,{padding:[45,45],maxZoom:16,animate:false});
      this._fitNext=false;
    }
    this._map.invalidateSize({pan:false});
  }
  _click(event){
    const target=event.target.closest("button");if(!target || target.disabled)return;
    if(target.hasAttribute("data-refresh")){this._registry=null;this._load(true);}
    if(target.hasAttribute("data-fit")){this._fitNext=true;this._updateMap();}
    if(target.hasAttribute("data-locate")){
      const current=this._current();
      if(current && this._map){this._map.setView([current.position.latitude,current.position.longitude],16,{animate:false});this._car?.openPopup();}
    }
    if(target.dataset.stop){
      const marker=this._markers?.get(target.dataset.stop);
      if(marker){this._map.setView(marker.getLatLng(),16,{animate:false});marker.openPopup();}
    }
    if(target.hasAttribute("data-clear"))this._clear();
  }
  _change(event){
    const input=event.target;
    if(input.hasAttribute("data-clear-check"))this.shadowRoot.querySelector("[data-clear]").disabled=input.checked!==true || !!this._clearing;
    if(input.hasAttribute("data-hours") || input.hasAttribute("data-device")){
      if(input.hasAttribute("data-hours"))this._hours=Number(input.value);else this._device=input.value;
      this._history=null;this._fitNext=true;this.shadowRoot.querySelector("[data-clear-check]").checked=false;
      this.shadowRoot.querySelector("[data-clear]").disabled=true;this._updateMap();this._load();
    }
  }
  async _clear(){
    if(this._clearing || !this._device || !this.shadowRoot.querySelector("[data-clear-check]")?.checked)return;
    this._clearing=true;const device=this._device;
    this.shadowRoot.querySelector("[data-clear]").disabled=true;
    try{
      await this._hass.callWS({type:this._domain+"/clear_journeys",device_id:device,confirmed:true});
      if(this._device===device){this._history=null;this._fitNext=true;await this._load();}
    }catch(error){if(this._device===device)this._error=error?.message || "History deletion failed";}
    finally{
      this._clearing=false;this.shadowRoot.querySelector("[data-clear-check]").checked=false;
      this.shadowRoot.querySelector("[data-clear]").disabled=true;this._renderStatus();
    }
  }
}

if(!customElements.get("gwm-journey-card"))customElements.define("gwm-journey-card",GwmJourneyCard);
if(typeof window!=="undefined"){
  window.customCards=window.customCards || [];
  if(!window.customCards.some(card=>card.type==="gwm-journey-card"))window.customCards.push({
    type:"gwm-journey-card",name:"Haval journeys",description:"OpenStreetMap, recent car status and seven-day journeys",
  });
}
