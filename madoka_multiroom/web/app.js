"use strict";
const cards = new Map();
const rooms = document.querySelector("#rooms");
const message = document.querySelector("#app-message");
let token = "", polling = false, failed = false;
let localMode = false;
let nativeOffice = false, nativeReadonly = false, nativeAttempted = false, nativeRoomName = "";
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
  if (localMode) {
    let notice = root.querySelector(".mode-notice");
    if (!notice) { notice = document.createElement("p"); notice.className = "mode-notice small muted"; root.querySelector(".power").after(notice); }
    notice.textContent = state.mode_notice || "";
  }
  root.querySelector(".room-temp").textContent = stale ? "—" : (state.room_temp ?? "—");
  root.querySelector(".setpoint").textContent = stale || state.setpoint == null ? "—" : `${state.setpoint}°`;
  const connection = root.querySelector(".connection");
  connection.textContent = localMode && data.enabled ?
    (data.connected && stale && data.connection_status === "Conectado" ? "Conectado · lectura pendiente" : data.connection_status) :
    !data.enabled ? "Deshabilitada" : stale ? "Sin datos recientes" : "Disponible";
  connection.dataset.state = (localMode ? !data.connected : stale) ? "offline" : "online";
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
  if (nativeOffice) {
    const caps = data.manual_controls || {};
    power.disabled ||= !caps.power;
    root.querySelector(".decrease").disabled ||= !caps.setpoint;
    root.querySelector(".increase").disabled ||= !caps.setpoint;
    for (const [css,key,observed] of [["mode","modes",state.mode_id],["fan","fans",state.fan_id]]) {
      const select = root.querySelector("."+css);
      if (!select) continue;
      if (localMode && css === "fan" && !select.querySelector('option[value="0"]')) {
        // An observed Auto value is display-only; it is never a write choice.
        const automatic = document.createElement("option"); automatic.value = "0"; automatic.textContent = "Auto"; select.prepend(automatic);
      }
      if (localMode && observed == null) {
        let unavailable = select.querySelector('option[value=""]');
        if (!unavailable) { unavailable = document.createElement("option"); unavailable.value = ""; select.prepend(unavailable); }
        unavailable.textContent = css !== "fan" ? "No disponible" :
          state.field_status?.fan_id === "not_applicable" ? "No aplicable en este modo" :
          state.field_status?.fan_id === "unknown" ? "Lectura desconocida" : "Sin lectura";
      }
      select.value = observed ?? "";
      select.disabled ||= !(caps[key]?.length);
      for (const option of select.options) option.disabled = (localMode && css === "fan" && option.value === "0") || !caps[key]?.includes(Number(option.value));
    }
  }
  const status = root.querySelector(".operation-status");
  const outcome = data.last_operation?.status;
  status.textContent = localMode ? (card.busy ? "Aplicando cambio" : card.error ||
    (["pending","uncertain"].includes(outcome) ? "No se pudo confirmar el cambio. No se repetirá la orden." :
      data.error || (outcome === "confirmed" ? "Cambio confirmado" : outcome === "reviewed_uncertain" ? "Estado actualizado; el cambio anterior no se confirmó" : ""))) : card.busy ? nativeOffice ? "Orden pendiente · esperando confirmación GET…" : "Aplicando…" : card.error ? card.error :
    nativeOffice && ["pending","uncertain"].includes(outcome) ? "STOP · resultado incierto. No repitas la orden. Revisa las lecturas." :
    data.error ? "Sala no disponible. Revisa la conexión y sus registros; las demás salas son independientes." :
    nativeOffice && outcome === "confirmed" ? "Cambio confirmado mediante lectura GET." :
    nativeOffice && outcome === "reviewed_uncertain" ? "Estado observado revisado; la orden anterior sigue sin confirmar." : "";
  const lastRead = root.querySelector(".last-read");
  if (lastRead) lastRead.textContent = `Última lectura: ${stamp(data.updated_at)}`;
  const recover = root.querySelector(".native-recover");
  if (recover) { recover.hidden = !data.error && !["pending","uncertain"].includes(outcome); recover.disabled = card.busy || failed; }
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
  protection.textContent = localMode ? "" : protectionText(data.protection);
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
    if (localMode) {
      let diagnostics = detail.el.querySelector(".local-diagnostics");
      if (!diagnostics) { diagnostics = document.createElement("details"); diagnostics.className = "local-diagnostics";
        const title = document.createElement("summary"); title.textContent = "Diagnóstico del último cambio";
        diagnostics.append(title, document.createElement("pre")); detail.el.append(diagnostics); }
      diagnostics.querySelector("pre").textContent = JSON.stringify(data.last_operation?.diagnostics || data.diagnostic || [], null, 2);
      let targets = detail.el.querySelector(".local-targets");
      if (!targets) { targets = document.createElement("p"); targets.className = "local-targets muted"; detail.el.querySelector(".updated").before(targets); }
      targets.textContent = `Objetivos guardados · frío: ${data.state.cooling_setpoint ?? "—"} °C · calor: ${data.state.heating_setpoint ?? "—"} °C. Activo: ${data.state.setpoint ?? "no aplicable"}`;
    }
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
  if (nativeOffice) {
    el.querySelector('.policy-mode option[value="ENFORCE"]').disabled = true;
    el.querySelector(".allow-auto-on").disabled = true;
    for (const css of ["save-desired","reset-budget"]) el.querySelector("."+css).disabled = true;
  }
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
    card.data = await api(endpoint(card,action), body, nativeOffice ? action === "review-outcome" ? 210000 : localMode ? 150000 : 40000 : 10000);
  } catch (error) {
    card.error = localMode ? error.message : nativeOffice ? "STOP · respuesta no confirmada. No repitas la orden; consulta el resultado y solicita revisión." : error.name === "TimeoutError" ? "La operación ha tardado demasiado. Revisa el estado antes de repetir." : error.message;
    // A lost response does not establish whether the mutation applied.
    if (!localMode) card.data = {...card.data,stale:true,connected:false};
  } finally { card.busy = false; render(card,card.data); updateOverview(); }
}
function manual(card,action,value) {
  if (localMode) {
    const proposal = action === "setpoint" && card.data.manual_controls?.setpoint_proposals?.find(p => p.value === value);
    if (proposal) {
      if (card.busy || failed) return;
      pendingSetpoint = {card,value,token:proposal.token};
      document.querySelector("#setpoint-confirm-description").textContent = `${card.data.name}: Frío ${proposal.cooling_before} → ${proposal.cooling_after} °C; Calor ${proposal.heating_before} → ${proposal.heating_after} °C.`;
      document.querySelector("#setpoint-confirm-dialog").showModal();
      document.querySelector("#cancel-setpoint-confirm").focus();
      return;
    }
    return mutate(card,"manual",{operation_id:crypto.randomUUID(),action,value,
      ...(action === "mode" ? {expected_mode:card.data.state.mode_id} : {})});
  }
  if (!nativeOffice) return mutate(card,action,action === "power" ? {on:value} : action === "setpoint" ? {temp:value} : action === "mode" ? {mode:value} : {speed:value});
  confirmAction(card,"Confirmar cambio físico",`Se enviará una sola orden (${names[action] || action}: ${value}). Se guardará el estado anterior y se verificará con GET. No se repetirá automáticamente.`,"manual",{operation_id:crypto.randomUUID(),action,value});
}
function bindControls(el,card) {
  el.querySelector(".power").addEventListener("click", () => manual(card,"power",!card.data.state.power_on));
  for (const [selector,delta] of [[".decrease",-1],[".increase",1]]) el.querySelector(selector).addEventListener("click", () => {
    const state = card.data.state;
    const value = nativeOffice ? Math.max(state.min_setpoint,Math.min(state.max_setpoint,state.setpoint+delta)) : state.setpoint+delta;
    manual(card,"setpoint",value);
  });
  for (const [css,action] of [["mode","mode"],["fan","fan"]]) {
    const select = el.querySelector("."+css);
    if (select) select.addEventListener("change", e => { manual(card,action,Number(e.target.value)); render(card,card.data); });
  }
}
function createCard(data) {
  const el = document.querySelector("#room-template").content.firstElementChild.cloneNode(true);
  el.dataset.id = data.id;
  const card = {el,data,epoch:0,busy:false,error:""};
  if (nativeOffice) {
    const selects = document.querySelector("#detail-template").content.querySelector(".selects").cloneNode(true);
    el.querySelector(".power").after(selects);
    const lastRead = document.createElement("p"); lastRead.className = "last-read small muted"; el.append(lastRead);
    const recover = document.createElement("button"); recover.className = "native-recover"; recover.textContent = localMode ? "Consultar estado" : "Revisar estado · sin repetir órdenes"; recover.hidden = true;
    recover.addEventListener("click", () => localMode ? mutate(card,"review-outcome",{}) : confirmAction(card,"Revisar estado observado","Solo se leerá el estado. La revisión no confirma la orden anterior ni evita una cuarentena pendiente.","review-outcome",{}));
    el.append(recover);
    if (localMode) {
      const configure = document.createElement("button"); configure.className = "local-configure"; configure.textContent = "Configurar controles";
      configure.addEventListener("click", () => localSettings(card)); el.append(configure);
      const hint = document.createElement("p"); hint.className = "small muted";
      hint.textContent = "Los controles no disponibles se muestran deshabilitados."; el.append(hint);
    }
  }
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
function localSettings(card) {
  const dialog = document.querySelector("#local-controls-dialog");
  const controls = card.data.local_controls;
  dialog.querySelector("h2").textContent = `Controles · ${card.data.name}`;
  for (const key of ["power","setpoint"]) dialog.querySelector(`[name="${key}"]`).checked = controls[key];
  for (const key of ["modes","fans"]) for (const input of dialog.querySelectorAll(`[name="${key}"]`)) input.checked = controls[key].includes(Number(input.value));
  for (const key of ["minimum","maximum"]) dialog.querySelector(`[name="${key}"]`).value = controls[key];
  const evidence = controls.mode_commissioning;
  for (const key of ["restrictions","physical_scope","dry_fan_effect"]) dialog.querySelector(`[name="${key}"]`).value = evidence?.[key] || (key === "dry_fan_effect" ? "preserve" : "unknown");
  dialog.querySelector('[name="ventilation_hardware"]').checked = evidence?.ventilation_hardware || false;
  dialog.querySelector('[name="wall_verified"]').checked = false;
  dialog.querySelector("form").onsubmit = e => {
    e.preventDefault();
    if (!e.target.reportValidity()) return;
    const patch = {...controls};
    for (const key of ["power","setpoint"]) patch[key] = dialog.querySelector(`[name="${key}"]`).checked;
    for (const key of ["modes","fans"]) patch[key] = [...dialog.querySelectorAll(`[name="${key}"]:checked`)].map(input => Number(input.value));
    for (const key of ["minimum","maximum"]) patch[key] = Number(dialog.querySelector(`[name="${key}"]`).value);
    if (dialog.querySelector('[name="wall_verified"]').checked) {
      const now = Date.now()/1000;
      patch.mode_commissioning = {wall_modes:patch.modes, checked_at:now, expires_at:now+86400,
        ventilation_hardware:dialog.querySelector('[name="ventilation_hardware"]').checked};
      for (const key of ["restrictions","physical_scope","dry_fan_effect"]) patch.mode_commissioning[key] = dialog.querySelector(`[name="${key}"]`).value;
    } else if (evidence) {
      // Editing another control never refreshes or erases wall commissioning.
      patch.mode_commissioning = evidence;
    }
    dialog.close(); mutate(card,"local-controls",patch);
  };
  dialog.querySelector(".close-local-controls").onclick = () => dialog.close();
  dialog.showModal();
}
function openDetail(card) {
  const el = document.querySelector("#detail-template").content.firstElementChild.cloneNode(true);
  document.querySelector("#detail-content").replaceChildren(el);
  detail = {el,card}; bindControls(el,card);
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
let pendingSetpoint = null;
document.querySelector("#cancel-setpoint-confirm").addEventListener("click", () => document.querySelector("#setpoint-confirm-dialog").close());
document.querySelector("#setpoint-confirm-dialog").addEventListener("close", () => { pendingSetpoint = null; });
document.querySelector("#setpoint-confirm-form").addEventListener("submit", e => {
  e.preventDefault();
  if (!pendingSetpoint) return;
  const {card,value,token} = pendingSetpoint;
  document.querySelector("#setpoint-confirm-dialog").close();
  mutate(card,"manual",{operation_id:crypto.randomUUID(),action:"setpoint",value,
    setpoint_confirmation:{token,confirmed:true}});
});
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
  document.querySelector("#history-message").textContent = nativeReadonly || nativeOffice ? "Cargando lecturas físicas…" : "Cargando muestras simuladas…";
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
    localMode = session.mode === "native-local";
    nativeOffice = session.mode === "native-office" || localMode;
    nativeReadonly = session.mode === "native-readonly";
    if (nativeOffice) {
      document.querySelector(".demo-label").textContent = localMode ? "Bluetooth local" : "Bluetooth · controles manuales";
      if (localMode) document.querySelector('[data-view="admin"]').hidden = true;
      document.querySelector('[data-view="lab"]').hidden = true;
      document.querySelector("#view-admin .small.muted").textContent = "Administración autenticada. Protección automática y encendido automático deshabilitados.";
    }
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
      else if (!card.busy && card.epoch === epochs.get(room.id)) {
        if (localMode) card.error = "";
        render(card,room);
      }
    }
    message.textContent = data.controllers.length ? "" : "No hay salas configuradas.";
  } catch (error) {
    failed = true; message.textContent = `Aplicación local no disponible: ${error.message}`;
    for (const card of cards.values()) render(card,card.data);
  } finally { polling = false; updateOverview(); }
}
function updateOverview() {
  const list = [...cards.values()];
  document.querySelector("#online-count").textContent = `${list.filter(c => localMode ? !failed && c.data.connected : !isStale(c.data)).length} de ${list.length} salas disponibles`;
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
