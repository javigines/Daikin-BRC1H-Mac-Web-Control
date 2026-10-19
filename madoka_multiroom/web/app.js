"use strict";
const cards = new Map();
const rooms = document.querySelector("#rooms");
const message = document.querySelector("#app-message");
let token = "";
let polling = false;
let failed = false;
const stamp = seconds => seconds ? new Date(seconds * 1000).toLocaleTimeString("es-ES", {hour:"2-digit",minute:"2-digit",second:"2-digit"}) : "Sin lecturas";

async function api(path, body) {
  const options = {signal: AbortSignal.timeout(10000), cache:"no-store"};
  if (body !== undefined) Object.assign(options, {method:"POST", headers:{"Content-Type":"application/json","X-Demo-Token":token},body:JSON.stringify(body)});
  const response = await fetch(path, options);
  const payload = await response.json();
  if (!response.ok) throw new Error(typeof payload.detail === "string" ? payload.detail : "Solicitud no válida");
  return payload;
}

function render(card, data) {
  card.data = data;
  const el = card.el;
  const state = data.state || {};
  const stale = failed || data.stale || !data.connected;
  el.dataset.stale = String(stale);
  el.querySelector(".room-name").textContent = data.name;
  el.querySelector(".room-id").textContent = data.id.toUpperCase();
  const connection = el.querySelector(".connection");
  connection.textContent = !data.enabled ? "Deshabilitado" : stale ? "Sin datos recientes" : "● Disponible";
  connection.dataset.state = stale ? "offline" : "online";
  el.querySelector(".model").textContent = `${data.info.model} / ${data.info.controller_version}`;
  el.querySelector(".room-temp").textContent = stale ? "—" : (state.room_temp ?? "—");
  el.querySelector(".setpoint").textContent = stale || state.setpoint == null ? "—" : `${state.setpoint}°`;
  el.querySelector(".updated").textContent = `${stale ? "Última lectura" : "Lectura"} · ${stamp(data.updated_at)}`;
  el.querySelector(".power").textContent = state.power_on ? "⏻  Encendido · apagar" : "⏻  Apagado · encender";
  el.querySelector(".power").setAttribute("aria-pressed", String(Boolean(state.power_on)));
  el.querySelector(".mode").value = state.mode_id ?? "";
  el.querySelector(".fan").value = state.fan_id ?? "";
  el.querySelector(".room-error").textContent = card.error || data.error || (failed ? "Sin comunicación con la aplicación local." : "");
  const disabled = card.busy || stale || !data.controls_enabled;
  for (const selector of [".power", ".mode", ".fan", ".decrease", ".increase"]) el.querySelector(selector).disabled = disabled;
  el.querySelector(".power").disabled ||= state.power_on == null;
  el.querySelector(".mode").disabled ||= state.mode_id == null;
  el.querySelector(".fan").disabled ||= state.fan_id == null;
  el.querySelector(".decrease").disabled ||= state.setpoint == null || state.setpoint <= state.min_setpoint;
  el.querySelector(".increase").disabled ||= state.setpoint == null || state.setpoint >= state.max_setpoint;
  el.querySelector(".apply-simulation").disabled = card.busy || failed || !data.enabled;
  el.querySelector(".history").disabled = failed;
  const protection = data.protection;
  el.querySelector(".policy-badge").textContent = protection.policy.mode;
  const flags = [protection.stopped ? "STOP de correcciones" : "", protection.maintenance ? "Mantenimiento" : "", protection.suspended ? "Suspensión temporal" : ""].filter(Boolean);
  const drift = Object.keys(protection.deviations);
  el.querySelector(".deviations").textContent = flags.join(" · ") || (drift.length ? `${drift.length} desviación/es${protection.notification ? " · aviso" : " · aceptada/s"}` : "Sin desviaciones");
  el.querySelector(".desired-state").textContent = `Deseado: ${Object.entries(protection.desired).map(([k,v]) => `${k}=${v}`).join(", ") || "sin valores autorizados"}. Intentos: ${protection.attempts}/${protection.policy.max_attempts}. Bloqueo nativo no aplicado ni verificado.`;
  const policyStamp = JSON.stringify([protection.policy, protection.maintenance]);
  if (card.policyStamp !== policyStamp) {
    el.querySelector(".policy-mode").value = protection.policy.mode;
    for (const input of el.querySelectorAll(".protected-fields input")) input.checked = protection.policy.protected.includes(input.value);
    el.querySelector(".allow-auto-on").checked = protection.policy.allow_auto_on;
    el.querySelector(".confirmations").value = protection.policy.confirmations;
    el.querySelector(".stabilization").value = protection.policy.stabilization_seconds;
    el.querySelector(".cooldown").value = protection.policy.cooldown_seconds;
    el.querySelector(".max-attempts").value = protection.policy.max_attempts;
    el.querySelector(".maintenance").checked = protection.maintenance;
    card.policyStamp = policyStamp;
  }
  for (const selector of [".save-policy", ".save-desired", ".save-restrictions", ".reset-budget", ".apply-external"]) el.querySelector(selector).disabled = card.busy || failed || !data.enabled;
  el.setAttribute("aria-busy", String(card.busy));
}

async function mutate(card, action, value) {
  if (card.busy) return;
  card.busy = true; card.epoch++; card.error = "";
  render(card, card.data);
  try {
    const data = await api(`/api/controllers/${encodeURIComponent(card.data.id)}/${action}`, value);
    failed = false;
    card.data = data;
  } catch (error) {
    card.error = error.name === "TimeoutError" ? "La operación ha tardado demasiado. Revisa el estado antes de repetir." : error.message;
    // A failed mutation may have applied before its response was lost.
    card.data = {...card.data, stale:true, connected:false};
  } finally {
    card.busy = false;
    render(card, card.data);
  }
}

function createCard(data) {
  const el = document.querySelector("#room-template").content.firstElementChild.cloneNode(true);
  el.dataset.id = data.id;
  const card = {el, data, epoch:0, busy:false, error:""};
  el.querySelector(".power").addEventListener("click", () => mutate(card, "power", {on:!card.data.state.power_on}));
  el.querySelector(".mode").addEventListener("change", event => mutate(card, "mode", {mode:Number(event.target.value)}));
  el.querySelector(".fan").addEventListener("change", event => mutate(card, "fan", {speed:Number(event.target.value)}));
  for (const [selector, delta] of [[".decrease",-1],[".increase",1]]) el.querySelector(selector).addEventListener("click", () => mutate(card, "setpoint", {temp:card.data.state.setpoint + delta}));
  el.querySelector(".apply-simulation").addEventListener("click", () => mutate(card, "simulation", {status:el.querySelector(".sim-status").value, delay_ms:Number(el.querySelector(".sim-delay").value)}));
  el.querySelector(".history").addEventListener("click", () => history(card));
  el.querySelector(".save-policy").addEventListener("click", () => mutate(card, "policy", {
    mode:el.querySelector(".policy-mode").value,
    protected:[...el.querySelectorAll(".protected-fields input:checked")].map(input => input.value),
    allow_auto_on:el.querySelector(".allow-auto-on").checked,
    confirmations:Number(el.querySelector(".confirmations").value),
    stabilization_seconds:Number(el.querySelector(".stabilization").value),
    cooldown_seconds:Number(el.querySelector(".cooldown").value),
    max_attempts:Number(el.querySelector(".max-attempts").value)
  }));
  el.querySelector(".save-desired").addEventListener("click", () => mutate(card, "desired", fieldValues(el, "desired")));
  el.querySelector(".save-restrictions").addEventListener("click", () => mutate(card, "restrictions", {maintenance:el.querySelector(".maintenance").checked, suspend_seconds:Number(el.querySelector(".suspend-seconds").value), reason:el.querySelector(".admin-reason").value}));
  el.querySelector(".reset-budget").addEventListener("click", () => mutate(card, "reset-budget", {reason:el.querySelector(".admin-reason").value}));
  el.querySelector(".apply-external").addEventListener("click", () => mutate(card, "external-change", fieldValues(el, "external")));
  el.querySelector(".events").addEventListener("click", () => events(card));
  rooms.append(el);
  cards.set(data.id, card);
  render(card, data);
}

function fieldValues(el, prefix) {
  const patch = {};
  for (const [suffix, field] of [["temp","setpoint"],["mode","mode_id"],["fan","fan_id"],["power","power_on"]]) {
    const value = el.querySelector(`.${prefix}-${suffix}`).value;
    if (value !== "") patch[field] = field === "power_on" ? value === "true" : Number(value);
  }
  return patch;
}

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
  } catch (error) { list.textContent = error.message; }
}
document.querySelector("#close-events").addEventListener("click", () => { eventsEpoch++; document.querySelector("#events-dialog").close(); });

let historyEpoch = 0;
async function history(card) {
  const epoch = ++historyEpoch;
  const dialog = document.querySelector("#history-dialog");
  document.querySelector("#history-title").textContent = `Historial · ${card.data.name}`;
  document.querySelector("#history-rows").replaceChildren();
  document.querySelector("#history-message").textContent = "Cargando muestras simuladas…";
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

async function poll() {
  if (polling) return;
  polling = true;
  const epochs = new Map([...cards].map(([id, card]) => [id, card.epoch]));
  try {
    token = (await api("/api/session")).token;
    const data = await api("/api/controllers");
    failed = false;
    for (const room of data.controllers) {
      const card = cards.get(room.id);
      if (!card) createCard(room);
      else if (!card.busy && card.epoch === epochs.get(room.id)) render(card, room);
    }
    message.textContent = data.controllers.length ? "Cada tarjeta controla únicamente su sala simulada." : "No hay controladores configurados.";
    document.querySelector("#online-count").textContent = `${data.controllers.filter(r => r.connected).length} / ${data.controllers.length}`;
  } catch (error) {
    failed = true; message.textContent = `Aplicación local no disponible: ${error.message}`;
    for (const card of cards.values()) render(card, card.data);
  } finally {
    polling = false;
  }
}
setInterval(() => {
  for (const card of cards.values()) {
    if (card.data.updated_at && Date.now()/1000 - card.data.updated_at > card.data.stale_after_seconds) render(card, {...card.data, stale:true});
  }
}, 1000);
poll();
setInterval(poll, 2000);
