"use strict";
const cards = new Map();
const rooms = document.querySelector("#rooms");
const message = document.querySelector("#app-message");
let token = "", polling = false, failed = false;
let nativeReadonly = false, nativeAttempted = false, nativeRoomName = "";
let admin = null, lab = null, detail = null, pendingConfirmation = null;
const stamp = seconds => seconds ? new Date(seconds * 1000).toLocaleString("es-ES") : "Sin lecturas";
const names = {setpoint:"Temperatura",mode_id:"Modo",fan_id:"Ventilador",power_on:"Encendido"};
const policyNames = {FREE:"Libre",MONITOR:"Supervisar cambios",ENFORCE:"Proteger configuración"};

async function api(path, body, timeout = 10000) {
  const options = {signal:AbortSignal.timeout(timeout),cache:"no-store"};
  if (body !== undefined) Object.assign(options, {method:"POST",headers:{"Content-Type":"application/json","X-Demo-Token":token},body:JSON.stringify(body)});
  const response = await fetch(path, options);
  const payload = await response.json();
  if (!response.ok) throw new Error(typeof payload.detail === "string" ? payload.detail : "Solicitud no válida");
  return payload;
}
function endpoint(card, action) { return `/api/controllers/${encodeURIComponent(card.data.id)}/${action}`; }
function isStale(data) { return failed || data.stale || !data.connected; }
function protectionText(p) {
  if (p.stopped) return "Protección detenida · revisar en Configuración";
  if (p.maintenance) return "Protección en mantenimiento";
  if (p.suspended) return "Protección suspendida temporalmente";
  if (p.policy.mode === "ENFORCE") return Object.keys(p.deviations).length ? "Protección activa · cambios detectados" : "Configuración protegida";
  if (p.policy.mode === "MONITOR") return Object.keys(p.deviations).length ? "Cambios detectados · supervisión" : "Supervisión activa";
  return "";
}
function updateControls(root, card) {
  const data = card.data, state = data.state || {}, stale = isStale(data);
  root.querySelector(".room-temp").textContent = stale ? "—" : (state.room_temp ?? "—");
  root.querySelector(".setpoint").textContent = stale || state.setpoint == null ? "—" : `${state.setpoint}°`;
  const connection = root.querySelector(".connection");
  connection.textContent = !data.enabled ? "Deshabilitada" : stale ? "Sin datos recientes" : "Disponible";
  connection.dataset.state = stale ? "offline" : "online";
  const disabled = card.busy || stale || !data.controls_enabled;
  for (const selector of [".power", ".decrease", ".increase", ".mode", ".fan"]) {
    const input = root.querySelector(selector);
    if (input) input.disabled = disabled;
  }
  const power = root.querySelector(".power");
  power.textContent = nativeReadonly ? state.power_on == null ? "Encendido sin lectura" : state.power_on ? "Encendido · solo lectura" : "Apagado · solo lectura" : state.power_on ? "Encendido · apagar" : "Apagado · encender";
  power.setAttribute("aria-pressed", String(Boolean(state.power_on)));
  power.disabled ||= state.power_on == null;
  root.querySelector(".decrease").disabled ||= state.setpoint == null || state.setpoint <= state.min_setpoint;
  root.querySelector(".increase").disabled ||= state.setpoint == null || state.setpoint >= state.max_setpoint;
  const status = root.querySelector(".operation-status");
  status.textContent = card.busy ? "Aplicando…" : card.error ? card.error : data.error ? "Revisa la disponibilidad de esta sala." : "";
  status.dataset.error = String(Boolean(card.error || data.error));
  root.setAttribute("aria-busy", String(card.busy));
}
function render(card, data) {
  card.data = data;
  const el = card.el;
  el.querySelector(".room-name").textContent = data.name;
  el.querySelector(".details").setAttribute("aria-label", `Más opciones de ${data.name}`);
  el.querySelector(".increase").setAttribute("aria-label", `Subir temperatura de ${data.name}`);
  el.querySelector(".decrease").setAttribute("aria-label", `Bajar temperatura de ${data.name}`);
  updateControls(el, card);
  const protection = el.querySelector(".protection-notice");
  protection.textContent = protectionText(data.protection);
  protection.hidden = !protection.textContent;
  if (detail?.card === card) {
    updateControls(detail.el, card);
    document.querySelector("#detail-title").textContent = data.name;
    detail.el.querySelector(".mode").value = data.state.mode_id ?? "";
    detail.el.querySelector(".fan").value = data.state.fan_id ?? "";
    detail.el.querySelector(".mode").disabled ||= data.state.mode_id == null;
    detail.el.querySelector(".fan").disabled ||= data.state.fan_id == null;
    detail.el.querySelector(".updated").textContent = `Última lectura: ${stamp(data.updated_at)}`;
    detail.el.querySelector(".room-error").textContent = data.error || (isStale(data) ? "No hay una lectura reciente. Los controles están deshabilitados." : "");
    detail.el.querySelector(".history").disabled = failed;
  }
  if (admin?.card === card) renderAdmin(admin);
  if (lab?.card === card) {
    lab.el.querySelector(".lab-status").textContent = card.error || data.error || (card.busy ? "Aplicando…" : "Solo simulación");
    lab.el.querySelector(".technical-info").textContent = `${data.id} · ${data.info.model} · ${data.info.controller_version}`;
    for (const button of lab.el.querySelectorAll("button")) button.disabled = card.busy || failed || !data.enabled;
  }
}
function renderAdmin(view) {
  const {el,card} = view, p = card.data.protection;
  el.querySelector(".admin-status").textContent = card.error || (card.busy ? "Aplicando…" : protectionText(p) || "Sin restricciones");
  el.querySelector(".desired-state").textContent = `Estado autorizado: ${Object.entries(p.desired).map(([k,v]) => `${names[k]}: ${typeof v === "boolean" ? v ? "encendido" : "apagado" : v}`).join(", ") || "sin definir"}. Intentos: ${p.attempts}/${p.policy.max_attempts}.`;
  const stamp = JSON.stringify(p.policy);
  if (view.stamp !== stamp) {
    el.querySelector(".policy-mode").value = p.policy.mode;
    for (const input of el.querySelectorAll(".protected-fields input")) input.checked = p.policy.protected.includes(input.value);
    el.querySelector(".allow-auto-on").checked = p.policy.allow_auto_on;
    for (const [css,key] of [["confirmations","confirmations"],["stabilization","stabilization_seconds"],["cooldown","cooldown_seconds"],["max-attempts","max_attempts"]]) el.querySelector("."+css).value = p.policy[key];
    view.stamp = stamp;
  }
  el.querySelector(".toggle-maintenance").textContent = p.maintenance ? "Finalizar mantenimiento" : "Activar mantenimiento";
  el.querySelector(".restrictions-status").textContent = `${p.maintenance ? "Mantenimiento activo." : "Mantenimiento inactivo."} ${p.suspended ? "Suspensión hasta " + stampTime(p.suspended_until) : "Sin suspensión temporal."}`;
  for (const button of el.querySelectorAll("button")) button.disabled = card.busy || failed || !card.data.enabled;
}
const stampTime = stamp;
async function mutate(card, action, value) {
  if (card.busy) return;
  card.busy = true; card.epoch++; card.error = "";
  render(card, card.data);
  try {
    // Read the latest opposite restriction at confirmation time, not when the
    // dialog opened. Never replay a mutation after an ambiguous response.
    const body = typeof value === "function" ? value(await api(endpoint(card,"").replace(/\/$/,""))) : value;
    card.data = await api(endpoint(card,action), body);
  } catch (error) {
    card.error = error.name === "TimeoutError" ? "La operación ha tardado demasiado. Revisa el estado antes de repetir." : error.message;
    // A lost response does not establish whether the mutation applied.
    card.data = {...card.data,stale:true,connected:false};
  } finally { card.busy = false; render(card,card.data); }
}
function bindControls(el,card) {
  el.querySelector(".power").addEventListener("click", () => mutate(card,"power",{on:!card.data.state.power_on}));
  for (const [selector,delta] of [[".decrease",-1],[".increase",1]]) el.querySelector(selector).addEventListener("click", () => mutate(card,"setpoint",{temp:card.data.state.setpoint+delta}));
}
function createCard(data) {
  const el = document.querySelector("#room-template").content.firstElementChild.cloneNode(true);
  el.dataset.id = data.id;
  const card = {el,data,epoch:0,busy:false,error:""};
  bindControls(el,card);
  el.querySelector(".details").addEventListener("click", () => openDetail(card));
  rooms.append(el); cards.set(data.id,card);
  for (const id of ["admin-room","lab-room"]) {
    const option = document.createElement("option"); option.value = data.id; option.textContent = data.name;
    document.getElementById(id).append(option);
  }
  render(card,data);
  if (!admin && !document.querySelector("#view-admin").hidden) mountAdmin();
  if (!lab && !document.querySelector("#view-lab").hidden) mountLab();
}
function openDetail(card) {
  const el = document.querySelector("#detail-template").content.firstElementChild.cloneNode(true);
  document.querySelector("#detail-content").replaceChildren(el);
  detail = {el,card}; bindControls(el,card);
  el.querySelector(".mode").addEventListener("change", e => mutate(card,"mode",{mode:Number(e.target.value)}));
  el.querySelector(".fan").addEventListener("change", e => mutate(card,"fan",{speed:Number(e.target.value)}));
  el.querySelector(".history").addEventListener("click", () => history(card));
  render(card,card.data); document.querySelector("#detail-dialog").showModal();
}
document.querySelector("#close-detail").addEventListener("click", () => document.querySelector("#detail-dialog").close());
document.querySelector("#detail-dialog").addEventListener("close", () => {detail = null;});
function fieldValues(el,prefix) {
  const patch = {};
  for (const [suffix,field] of [["temp","setpoint"],["mode","mode_id"],["fan","fan_id"],["power","power_on"]]) {
    const value = el.querySelector(`.${prefix}-${suffix}`).value;
    if (value !== "") patch[field] = field === "power_on" ? value === "true" : Number(value);
  }
  return patch;
}
function confirmAction(card,title,description,action,body) {
  if (card.busy || failed) return;
  pendingConfirmation = {card,action,body};
  document.querySelector("#confirm-title").textContent = title;
  document.querySelector("#confirm-description").textContent = `${card.data.name}: ${description}`;
  document.querySelector("#confirm-reason").value = "";
  document.querySelector("#confirm-dialog").showModal();
  document.querySelector("#confirm-reason").focus();
}
document.querySelector("#cancel-confirm").addEventListener("click", () => document.querySelector("#confirm-dialog").close());
document.querySelector("#confirm-dialog").addEventListener("close", () => { pendingConfirmation = null; });
document.querySelector("#confirm-form").addEventListener("submit", e => {
  e.preventDefault();
  if (!pendingConfirmation || !e.target.reportValidity()) return;
  const reason = document.querySelector("#confirm-reason").value.trim();
  if (reason.length < 3) { document.querySelector("#confirm-reason").focus(); return; }
  const {card,action,body} = pendingConfirmation;
  document.querySelector("#confirm-dialog").close();
  mutate(card,action,typeof body === "function" ? current => ({...body(current),reason}) : {...body,reason});
});
function mountAdmin() {
  const card = cards.get(document.querySelector("#admin-room").value);
  if (!card) return;
  const el = document.querySelector("#admin-template").content.firstElementChild.cloneNode(true);
  document.querySelector("#admin-content").replaceChildren(el); admin = {el,card};
  el.querySelector(".selected-room").textContent = card.data.name;
  el.querySelector(".save-policy").addEventListener("click", () => mutate(card,"policy",{
    mode:el.querySelector(".policy-mode").value,protected:[...el.querySelectorAll(".protected-fields input:checked")].map(x => x.value),allow_auto_on:el.querySelector(".allow-auto-on").checked,
    confirmations:Number(el.querySelector(".confirmations").value),stabilization_seconds:Number(el.querySelector(".stabilization").value),cooldown_seconds:Number(el.querySelector(".cooldown").value),max_attempts:Number(el.querySelector(".max-attempts").value)}));
  el.querySelector(".save-desired").addEventListener("click", () => mutate(card,"desired",fieldValues(el,"desired")));
  el.querySelector(".toggle-maintenance").addEventListener("click", () => {
    const p = card.data.protection, maintenance = !p.maintenance;
    confirmAction(card,maintenance ? "Activar mantenimiento" : "Finalizar mantenimiento","Se conservará la suspensión temporal actual.","restrictions",current => ({maintenance,suspend_seconds:Math.min(3600,Math.max(0,Math.ceil(current.protection.suspended_until-Date.now()/1000)))}));
  });
  function suspension(seconds) { confirmAction(card,seconds ? "Suspender protección" : "Finalizar suspensión","El modo mantenimiento no cambiará.","restrictions",current => ({maintenance:current.protection.maintenance,suspend_seconds:seconds})); }
  el.querySelector(".suspend").addEventListener("click", () => { const input = el.querySelector(".suspend-seconds"); if (input.reportValidity()) suspension(Number(input.value)); });
  el.querySelector(".resume").addEventListener("click", () => suspension(0));
  el.querySelector(".reset-budget").addEventListener("click", () => confirmAction(card,"Rearmar correcciones","Se liberará la detención y se concederá un presupuesto nuevo de intentos. Revisa antes la causa.","reset-budget",{}));
  renderAdmin(admin);
}
function mountLab() {
  const card = cards.get(document.querySelector("#lab-room").value);
  if (!card) return;
  const el = document.querySelector("#lab-template").content.firstElementChild.cloneNode(true);
  document.querySelector("#lab-content").replaceChildren(el); lab = {el,card};
  el.querySelector(".selected-room").textContent = card.data.name;
  el.querySelector(".apply-simulation").addEventListener("click", () => mutate(card,"simulation",{status:el.querySelector(".sim-status").value,delay_ms:Number(el.querySelector(".sim-delay").value)}));
  el.querySelector(".apply-external").addEventListener("click", () => mutate(card,"external-change",fieldValues(el,"external")));
  el.querySelector(".events").addEventListener("click", () => events(card));
  render(card,card.data);
}
for (const button of document.querySelectorAll("[data-view]")) button.addEventListener("click", () => {
  for (const other of document.querySelectorAll("[data-view]")) other.setAttribute("aria-pressed",String(other === button));
  for (const name of ["rooms","admin","lab"]) document.querySelector("#view-"+name).hidden = name !== button.dataset.view;
  if (button.dataset.view === "admin") mountAdmin();
  if (button.dataset.view === "lab") mountLab();
  document.querySelector("#view-"+button.dataset.view).focus();
});
document.querySelector("#admin-room").addEventListener("change",mountAdmin);
document.querySelector("#lab-room").addEventListener("change",mountLab);
let eventsEpoch = 0;
async function events(card) {
  const epoch = ++eventsEpoch;
  const dialog = document.querySelector("#events-dialog");
  const list = document.querySelector("#events-list");
  list.replaceChildren();
  document.querySelector("#events-title").textContent = `Eventos · ${card.data.name}`;
  if (!dialog.open) dialog.showModal();
  try {
    const data = await api(`/api/controllers/${encodeURIComponent(card.data.id)}/events`);
    if (epoch !== eventsEpoch || !dialog.open) return;
    for (const event of data.events) {
      const item = document.createElement("li");
      item.textContent = `${new Date(event.created_at_ms).toLocaleString("es-ES")} · ${event.kind} · ${JSON.stringify(event.details)}`;
      list.append(item);
    }
    if (!data.events.length) { const item = document.createElement("li"); item.textContent = "Todavía no hay eventos."; list.append(item); }
  } catch (error) { if (epoch === eventsEpoch && dialog.open) list.textContent = error.message; }
}
document.querySelector("#close-events").addEventListener("click", () => { eventsEpoch++; document.querySelector("#events-dialog").close(); });

let historyEpoch = 0;
async function history(card) {
  const epoch = ++historyEpoch;
  const dialog = document.querySelector("#history-dialog");
  document.querySelector("#history-title").textContent = `Historial · ${card.data.name}`;
  document.querySelector("#history-rows").replaceChildren();
  document.querySelector("#history-message").textContent = nativeReadonly ? "Cargando lecturas físicas…" : "Cargando muestras simuladas…";
  if (!dialog.open) dialog.showModal();
  try {
    const data = await api(`/api/controllers/${encodeURIComponent(card.data.id)}/history?limit=100`);
    if (epoch !== historyEpoch || !dialog.open) return;
    const body = document.querySelector("#history-rows");
    for (const sample of data.samples) {
      const row = document.createElement("tr");
      for (const value of [new Date(sample.captured_at_ms).toLocaleString("es-ES"), sample.state.room_temp ?? "—", sample.state.setpoint ?? "—", sample.state.power_on == null ? "—" : sample.state.power_on ? "Sí" : "No"]) {
        const cell = document.createElement("td"); cell.textContent = String(value); row.append(cell);
      }
      body.append(row);
    }
    document.querySelector("#history-message").textContent = data.samples.length ? `${data.samples.length} muestras · solo datos de esta sala` : "Todavía no hay muestras para esta sala.";
  } catch (error) {
    if (epoch === historyEpoch) document.querySelector("#history-message").textContent = error.message;
  }
}
document.querySelector("#close-history").addEventListener("click", () => { historyEpoch++; document.querySelector("#history-dialog").close(); });


document.querySelector("#events-dialog").addEventListener("cancel", () => { eventsEpoch++; });
document.querySelector("#history-dialog").addEventListener("cancel", () => { historyEpoch++; });
async function poll() {
  if (polling) return;
  polling = true;
  const epochs = new Map([...cards].map(([id,card]) => [id,card.epoch]));
  try {
    const session = await api("/api/session");
    token = session.token;
    nativeReadonly = session.mode === "native-readonly";
    if (nativeReadonly) {
      document.querySelector(".demo-label").textContent = "Bluetooth · solo lectura supervisada";
      document.querySelector("#native-admission").hidden = false;
      for (const view of ["admin","lab"]) document.querySelector(`[data-view="${view}"]`).hidden = true;
      if (session.admission_attempted && !nativeAttempted) document.querySelector("#native-status").textContent = "Intento consumido. Revisión supervisada necesaria.";
      nativeAttempted ||= session.admission_attempted;
      document.querySelector("#native-connect").disabled = nativeAttempted || !nativeRoomName;
    }
    const data = await api("/api/controllers"); failed = false;
    if (nativeReadonly) {
      nativeRoomName = data.controllers.find(room => room.id === session.selected_id)?.name || "";
      document.querySelector("#native-selected").textContent = nativeRoomName ? `Sala autorizada: ${nativeRoomName}` : "Sala autorizada no disponible.";
      document.querySelector("#native-connect").disabled = nativeAttempted || !nativeRoomName;
    }
    for (const room of data.controllers) {
      const card = cards.get(room.id);
      if (!card) createCard(room);
      else if (!card.busy && card.epoch === epochs.get(room.id)) render(card,room);
    }
    message.textContent = data.controllers.length ? "" : "No hay salas configuradas.";
  } catch (error) {
    failed = true; message.textContent = `Aplicación local no disponible: ${error.message}`;
    for (const card of cards.values()) render(card,card.data);
  } finally { polling = false; updateOverview(); }
}
function updateOverview() {
  const list = [...cards.values()];
  document.querySelector("#online-count").textContent = `${list.filter(c => !isStale(c.data)).length} de ${list.length} salas disponibles`;
}
setInterval(() => {
  for (const card of cards.values()) if (card.data.updated_at && Date.now()/1000-card.data.updated_at > card.data.stale_after_seconds) render(card,{...card.data,stale:true});
  updateOverview();
},1000);
document.querySelector("#native-connect").addEventListener("click", async () => {
  if (!nativeReadonly || !nativeRoomName || nativeAttempted || !window.confirm(`${nativeRoomName}: ¿confirmas la autorización vigente para una conexión de solo lectura? No se podrá repetir.`)) return;
  nativeAttempted = true;
  document.querySelector("#native-connect").disabled = true;
  const status = document.querySelector("#native-status");
  status.textContent = "Conectando y suscribiendo… Atiende el diálogo de macOS. No repitas la operación.";
  try {
    await api("/api/physical/admit", {}, 180000);
    status.textContent = "Sesión preparada. Solo lecturas. Al detener, conserva los registros para revisión.";
  } catch (_) {
    status.textContent = "STOP: conexión no confirmada. Conserva los registros y solicita revisión. No repitas.";
  }
  poll();
});
poll(); setInterval(poll,2000);
