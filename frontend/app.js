/* SmartPark web client (boundary classes of the Phase 3 class diagram).
   Plain JavaScript, no build step: served by the FastAPI backend at /static. */
"use strict";

// ------------------------------------------------------------------ helpers
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const aed = n => "AED " + Number(n || 0).toFixed(2);
const pad = n => String(n).padStart(2, "0");
const fmtTime = iso => iso ? iso.slice(11, 16) : "";
const fmtDate = iso => { if (!iso) return ""; const d = new Date(iso); return d.toLocaleDateString("en-GB", { day: "2-digit", month: "short" }); };
const fmtDT = iso => iso ? `${fmtDate(iso)} ${fmtTime(iso)}` : "";
const lvlClass = l => "lvl-" + String(l || "").replace(" ", "-");
const store = {
  get(k) { try { return sessionStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { v == null ? sessionStorage.removeItem(k) : sessionStorage.setItem(k, v); } catch { } },
};

const S = { token: store.get("sp_token"), user: JSON.parse(store.get("sp_user") || "null"), serverNow: null,
            garages: [], selGarage: null, filterType: "standard", sort: "distance", holdTimer: null };

function toast(msg, bad = false) {
  const t = $("#toast"); t.textContent = msg; t.className = "toast" + (bad ? " bad" : ""); t.hidden = false;
  clearTimeout(t._h); t._h = setTimeout(() => (t.hidden = true), 4200);
}

async function api(path, opts = {}) {
  const headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
  if (S.token) headers.Authorization = "Bearer " + S.token;
  const res = await fetch(path, { ...opts, headers, body: opts.body ? JSON.stringify(opts.body) : undefined });
  const isJson = (res.headers.get("content-type") || "").includes("json");
  const data = isJson ? await res.json() : await res.text();
  if (res.status === 401 && S.token && !path.startsWith("/api/auth")) { logout(true); throw new Error("Session expired"); }
  if (!res.ok) {
    const d = data && data.detail;
    const msg = typeof d === "string" ? d : d && d.message ? d.message : Array.isArray(d) ? d.map(x => x.msg).join("; ") : "Request failed";
    const err = new Error(msg); err.status = res.status; err.detail = d; throw err;
  }
  return data;
}

function safe(fn) { return async (...a) => { try { await fn(...a); } catch (e) { if (e.message !== "Session expired") toast(e.message, true); } }; }

// ------------------------------------------------------------------ charts
function barChart(items, { value, label, color, height = 170, unit = "", max, title = "" }) {
  const w = 720, h = height, padL = 34, padB = 26, n = items.length;
  const mx = max ?? Math.max(1, ...items.map(value));
  const bw = (w - padL - 6) / n;
  let s = `<svg class="chart" viewBox="0 0 ${w} ${h}" role="img" aria-label="${esc(title)}">`;
  for (let g = 0; g <= 4; g++) {
    const y = 6 + (h - padB - 6) * (1 - g / 4);
    s += `<line x1="${padL}" x2="${w}" y1="${y}" y2="${y}" stroke="#eef1f5"/><text x="${padL - 6}" y="${y + 4}" font-size="10" fill="#7b8494" text-anchor="end">${Math.round(mx * g / 4)}${unit}</text>`;
  }
  items.forEach((it, i) => {
    const v = value(it) || 0, bh = (h - padB - 6) * v / mx, x = padL + i * bw + 2, y = h - padB - bh;
    s += `<rect x="${x}" y="${y}" width="${Math.max(2, bw - 4)}" height="${bh}" rx="2" fill="${color(it)}"><title>${esc(label(it))}: ${v}${unit}</title></rect>`;
    if (n <= 24 || i % Math.ceil(n / 16) === 0) s += `<text x="${x + bw / 2 - 2}" y="${h - 9}" font-size="10" fill="#7b8494" text-anchor="middle">${esc(label(it))}</text>`;
  });
  return s + "</svg>";
}

function lineChart(items, { value, label, height = 170, color = "#2563eb", unit = "" }) {
  const w = 720, h = height, padL = 46, padB = 26, n = items.length;
  const mx = Math.max(1, ...items.map(value)) * 1.1;
  const pt = (it, i) => [padL + i * (w - padL - 10) / Math.max(1, n - 1), 6 + (h - padB - 6) * (1 - value(it) / mx)];
  let s = `<svg class="chart" viewBox="0 0 ${w} ${h}">`;
  for (let g = 0; g <= 4; g++) {
    const y = 6 + (h - padB - 6) * (1 - g / 4);
    s += `<line x1="${padL}" x2="${w}" y1="${y}" y2="${y}" stroke="#eef1f5"/><text x="${padL - 6}" y="${y + 4}" font-size="10" fill="#7b8494" text-anchor="end">${Math.round(mx * g / 4)}${unit}</text>`;
  }
  const pts = items.map(pt);
  s += `<polyline fill="none" stroke="${color}" stroke-width="2.5" points="${pts.map(p => p.join(",")).join(" ")}"/>`;
  pts.forEach((p, i) => { s += `<circle cx="${p[0]}" cy="${p[1]}" r="3" fill="${color}"><title>${esc(label(items[i]))}: ${value(items[i])}</title></circle>`;
    if (i % 2 === 0) s += `<text x="${p[0]}" y="${h - 9}" font-size="10" fill="#7b8494" text-anchor="middle">${esc(label(items[i]))}</text>`; });
  return s + "</svg>";
}
const LVL_COLOR = { quiet: "#16a34a", normal: "#2563eb", busy: "#d97706", "very busy": "#c2410c", unknown: "#94a3b8" };
const TYPE_COLOR = { standard: "#16324f", ev: "#0f9d8a", accessible: "#2563eb" };

// ------------------------------------------------------------------ shell
const NAV = {
  driver: [["find", "Find Parking"], ["bookings", "My Bookings"], ["payments", "Payments"], ["vehicles", "Vehicles & Settings"]],
  corporate: [["find", "Find Parking"], ["bookings", "My Bookings"], ["payments", "Payments"], ["invoice", "Company Invoice"], ["vehicles", "Fleet & Settings"]],
  attendant: [["lane", "Lane Console"]],
  admin: [["dashboard", "Dashboard"], ["garages", "Garages & Spots"], ["pricing", "Pricing Rules"], ["model", "Forecast Model"], ["users", "Users & Audit"], ["lane", "Lane Console"], ["demo", "Demo Tools"]],
};

function setUser(token, user) {
  S.token = token; S.user = user; store.set("sp_token", token); store.set("sp_user", user ? JSON.stringify(user) : null);
}
function logout(expired = false) {
  if (S.token && !expired) api("/api/auth/logout", { method: "POST" }).catch(() => {});
  setUser(null, null); clearInterval(S.poll); location.hash = "#login";
  if (expired) toast("Your session expired after 15 minutes of inactivity. Please log in again.", true);
}

function shell() {
  const logged = !!S.user;
  $("#topbar").hidden = !logged;
  if (!logged) return;
  const page = location.hash.slice(1).split("/")[0];
  $("#nav").innerHTML = NAV[S.user.role].map(([k, t]) => `<a href="#${k}" class="${page === k ? "active" : ""}">${t}</a>`).join("");
  $("#who").textContent = `${S.user.name} · ${S.user.role}`;
}

async function tick() {
  if (!S.user) return;
  try {
    const h = await fetch("/api/health").then(r => r.json());
    S.serverNow = h.time;
    $("#clock").textContent = "GST " + fmtDT(h.time);
    if (["driver", "corporate"].includes(S.user.role)) {
      const notes = await api("/api/notifications");
      const unread = notes.filter(n => !n.is_read).length;
      $("#badge").hidden = !unread; $("#badge").textContent = unread;
      S.notes = notes;
    } else { $("#bell").hidden = true; }
  } catch { }
}

function openDrawer() {
  $("#drawer").hidden = false;
  $("#notes").innerHTML = (S.notes || []).map(n => `<div class="note ${n.is_read ? "" : "unread"}"><div class="t">${esc(n.type.replace(/_/g, " "))} · ${esc(n.channel)} · ${fmtDT(n.created_at)}</div>${esc(n.message)}</div>`).join("") || `<p class="muted" style="padding:16px">No notifications yet.</p>`;
  api("/api/notifications/read", { method: "POST" }).then(tick);
}

const ROUTES = {};
async function router() {
  const [page, arg] = location.hash.slice(1).split("/");
  if (!S.user && page !== "register") { location.hash = "#login"; }
  shell();
  const p = S.user ? (page && ROUTES[page] && NAV[S.user.role].some(n => n[0] === page) ? page : NAV[S.user.role][0][0]) : (page === "register" ? "register" : "login");
  if (S.user && p !== page) { location.hash = "#" + p; return; }
  clearInterval(S.holdTimer);
  $("#app").innerHTML = "";
  await safe(ROUTES[p])(arg);
}

// ================================================================= UC-01
ROUTES.login = async () => {
  $("#app").innerHTML = `<div class="login-wrap">
    <section class="hero"><h1>SmartPark</h1><p>AI-powered smart city parking network for Abu Dhabi.</p>
      <ul><li>See free spots by floor in real time</li><li>Reserve and pay before you leave home</li>
      <li>Fair, demand-based prices from our AI forecast</li><li>Ticketless entry with plate recognition or QR</li></ul></section>
    <section class="login-card"><h2>Log in</h2>
      <form id="lf"><div><label for="em">Email</label><input id="em" type="email" required autocomplete="username"></div>
      <div class="mt"><label for="pw">Password</label><input id="pw" type="password" required autocomplete="current-password"></div>
      <button class="btn mt" style="width:100%">Log in</button><div class="err" id="lerr"></div></form>
      <p class="mt muted">New driver? <a href="#register">Create an account</a></p>
      <div class="demo-acc"><b>Demo accounts</b> (click to fill):<br>
      ${[["driver@smartpark.ae", "Driver@1234", "Driver"], ["corporate@smartpark.ae", "Corp@1234", "Corporate"], ["attendant@smartpark.ae", "Attend@1234", "Attendant"], ["admin@smartpark.ae", "Admin@1234", "Admin"]]
        .map(([e, p, r]) => `<button class="btn ghost small" data-e="${e}" data-p="${p}">${r}</button>`).join("")}</div>
    </section></div>`;
  $$(".demo-acc button").forEach(b => b.onclick = () => { $("#em").value = b.dataset.e; $("#pw").value = b.dataset.p; });
  $("#lf").onsubmit = async e => {
    e.preventDefault(); $("#lerr").textContent = "";
    try {
      const r = await api("/api/auth/login", { method: "POST", body: { email: $("#em").value, password: $("#pw").value } });
      setUser(r.token, r.user); startPolling(); location.hash = "#" + NAV[r.user.role][0][0];
    } catch (err) { $("#lerr").textContent = err.message; }
  };
};

ROUTES.register = async () => {
  $("#app").innerHTML = `<div class="card" style="max-width:520px;margin:30px auto"><h2>Create a driver account</h2>
    <form id="rf"><div class="grid g2"><div><label>Full name</label><input id="rn" required></div><div><label>Mobile (UAE)</label><input id="rp" placeholder="05XXXXXXXX" required></div></div>
    <div class="mt"><label>Email</label><input id="re" type="email" required></div>
    <div class="mt"><label>Password (8+ characters, letters and numbers)</label><input id="rw" type="password" required></div>
    <button class="btn mt">Send verification code</button></form>
    <form id="vf" class="hide mt"><div class="ok-box" id="otpmsg"></div><div class="mt"><label>6-digit code</label><input id="code" maxlength="6" required></div>
    <button class="btn teal mt">Verify account</button></form><div class="err" id="rerr"></div>
    <p class="mt"><a href="#login">Back to log in</a></p></div>`;
  $("#rf").onsubmit = async e => {
    e.preventDefault(); $("#rerr").textContent = "";
    try {
      const r = await api("/api/auth/register", { method: "POST", body: { name: $("#rn").value, phone: $("#rp").value, email: $("#re").value, password: $("#rw").value } });
      $("#rf").classList.add("hide"); $("#vf").classList.remove("hide");
      $("#otpmsg").innerHTML = esc(r.message) + (r.demo_otp ? `<br><small>Demo mode – your code is <b>${r.demo_otp}</b></small>` : "");
    } catch (err) { $("#rerr").textContent = err.message; }
  };
  $("#vf").onsubmit = async e => {
    e.preventDefault();
    try { await api("/api/auth/verify", { method: "POST", body: { email: $("#re").value, code: $("#code").value } });
      toast("Account verified – please log in"); location.hash = "#login"; }
    catch (err) { $("#rerr").textContent = err.message; }
  };
};

// ================================================================= UC-02/03/04/05
ROUTES.find = async () => {
  const vehicles = await api("/api/vehicles");
  const permit = vehicles.some(v => v.has_accessible_permit);
  $("#app").innerHTML = `<h1>Find parking</h1><p class="sub">Live availability, AI demand forecast and fair prices across SmartPark garages.</p>
  <div class="card"><div class="row">
    <div style="flex:2"><label for="q">Search garage or area</label><input id="q" placeholder="e.g. Yas, Al Maryah, Khalifa City"></div>
    <div><label>Spot type</label><div class="seg" id="types">
      <button data-t="standard">Standard</button><button data-t="ev">EV charging</button>${permit ? `<button data-t="accessible">Accessible</button>` : ""}</div></div>
    <div><label for="sort">Sort by</label><select id="sort"><option value="distance">Nearest</option><option value="price">Cheapest now</option><option value="free">Most free spots</option></select></div>
  </div></div>
  <div class="grid split"><div id="glist"></div><div id="gdetail"><div class="card muted">Select a garage to see floors, the 24-hour forecast and to book a spot.</div></div></div>`;
  $$("#types button").forEach(b => { b.classList.toggle("on", b.dataset.t === S.filterType); b.onclick = () => { S.filterType = b.dataset.t; $$("#types button").forEach(x => x.classList.toggle("on", x === b)); loadGarages(); }; });
  $("#sort").value = S.sort; $("#sort").onchange = () => { S.sort = $("#sort").value; loadGarages(); };
  let deb; $("#q").oninput = () => { clearTimeout(deb); deb = setTimeout(() => loadGarages(), 250); };
  S.vehicles = vehicles;
  await loadGarages();
};

const loadGarages = safe(async (keepDetail = false) => {
  const gs = await api(`/api/garages?q=${encodeURIComponent($("#q")?.value || "")}&spot_type=${S.filterType}&sort=${S.sort}`);
  S.garages = gs;
  $("#glist").innerHTML = gs.map(g => `<div class="garage ${S.selGarage === g.garage_id ? "sel" : ""}" data-id="${g.garage_id}">
    <div class="top"><div><div class="name">${esc(g.name)}</div><div class="meta">${esc(g.area)} · ${g.distance_km} km away</div></div>
    <div class="price">${aed(g.current_rate)}<small>per hour now</small></div></div>
    <div class="chips"><span class="chip">${g.free_selected} ${S.filterType} free</span>
      ${Object.entries(g.free).map(([t, n]) => `<span class="chip" style="background:#f6f8fb;color:#475569">${t}: ${n}/${g.total[t]}</span>`).join("")}
      <span class="chip ${lvlClass(g.forecast_now.level)}">${g.forecast_now.level}</span></div></div>`).join("") || `<div class="card muted">No garage matches your search.</div>`;
  $$(".garage").forEach(el => el.onclick = () => { S.selGarage = +el.dataset.id; $$(".garage").forEach(x => x.classList.toggle("sel", x === el)); showGarage(S.selGarage); });
  if (!keepDetail && S.selGarage && gs.some(g => g.garage_id === S.selGarage)) showGarage(S.selGarage);
});

function defaultStart() {
  const now = S.serverNow ? new Date(S.serverNow) : new Date();
  now.setMinutes(now.getMinutes() + 30); now.setMinutes(Math.ceil(now.getMinutes() / 15) * 15, 0, 0);
  return now;
}
const localISO = d => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;

const showGarage = safe(async id => {
  const [g, fc] = await Promise.all([api(`/api/garages/${id}`), api(`/api/garages/${id}/forecast`)]);
  const st = defaultStart();
  const veh = S.vehicles.filter(v => S.filterType !== "accessible" || v.has_accessible_permit);
  $("#gdetail").innerHTML = `<div class="card"><h2>${esc(g.name)}</h2><p class="muted" style="margin-top:-8px">${esc(g.address)} · ${g.free_total} of ${g.capacity} spots free now · updated ${fmtTime(g.as_of)}</p>
    <table><thead><tr><th>Floor</th>${Object.keys(g.free).map(t => `<th class="num">${t}</th>`).join("")}</tr></thead>
    <tbody>${g.floors.map(f => `<tr><td>${esc(f.name)}</td>${Object.keys(g.free).map(t => `<td class="num">${f.free[t]} / ${f.total[t]}</td>`).join("")}</tr>`).join("")}</tbody></table></div>
  <div class="card"><h3>AI demand forecast – next 24 hours</h3>
    ${barChart(fc.hours, { value: h => h.occupancy_pct, label: h => fmtTime(h.hour).slice(0, 2), color: h => LVL_COLOR[h.level], unit: "%", max: 100, title: "Predicted occupancy" })}
    <div class="legend">${Object.entries(LVL_COLOR).slice(0, 4).map(([k, c]) => `<span style="--c:${c}">${k}</span>`).join("")}</div>
    <p class="mt" style="margin-bottom:0"><b>Quietest &amp; cheapest times:</b> ${fc.best_times.map(b => `<span class="chip ${lvlClass(b.level)}">${fmtTime(b.hour)} · ${aed(b.price_standard)}/h</span>`).join(" ")}</p></div>
  <div class="card" id="bookcard"><h3>Book a ${esc(S.filterType)} spot</h3>
    <div class="row"><div><label>Arrival</label><input type="datetime-local" id="bstart" value="${localISO(st)}" step="900"></div>
      <div><label>Duration</label><select id="bdur">${[0.5, 1, 1.5, 2, 3, 4, 6, 8, 12, 24].map(h => `<option value="${h}" ${h === 2 ? "selected" : ""}>${h < 1 ? "30 min" : h + " h"}</option>`).join("")}</select></div>
      <div><label>Vehicle</label><select id="bveh">${veh.map(v => `<option value="${v.vehicle_id}">${esc(v.plate_number)} (${v.vehicle_type}${v.has_accessible_permit ? ", permit" : ""})</option>`).join("")}</select></div>
      <div><label>Floor (optional)</label><select id="bfloor"><option value="">Any floor</option>${g.floors.map(f => `<option value="${f.floor_id}">${esc(f.name)}</option>`).join("")}</select></div></div>
    <button class="btn mt" id="bquote">Get price</button><div id="quote"></div></div>`;
  $("#bquote").onclick = safe(() => getQuote(g));
});

function quoteTable(q) {
  return `<table class="mt"><tbody>
    <tr><td>Base price (${q.hours} h × ${aed(q.base_rate)})</td><td class="num">${aed(q.base_amount)}</td></tr>
    <tr><td>Demand adjustment (avg ×${q.avg_multiplier}${q.forecast_used === false ? ", no forecast" : ""})</td><td class="num">${aed(q.demand_adjustment)}</td></tr>
    <tr><td>VAT 5%</td><td class="num">${aed(q.vat)}</td></tr><tr><td>Service fee</td><td class="num">${aed(q.fees)}</td></tr>
    <tr class="total"><td>Total</td><td class="num">${aed(q.total)}</td></tr></tbody></table>`;
}

function readBooking() {
  const start = $("#bstart").value, hrs = +$("#bdur").value;
  const s = new Date(start); const e = new Date(s.getTime() + hrs * 3600e3);
  return { garage_id: S.selGarage, spot_type: S.filterType, start_time: localISO(s) + ":00", end_time: localISO(e) + ":00",
           vehicle_id: +$("#bveh").value, floor_id: $("#bfloor").value ? +$("#bfloor").value : null };
}

async function getQuote(g) {
  const b = readBooking();
  const q = await api("/api/quote", { method: "POST", body: b });
  $("#quote").innerHTML = quoteTable(q) + `<p class="muted">${q.free_spots} matching spot(s) free for the whole period. Quote valid 5 minutes.</p>
    <button class="btn teal" id="bhold" ${q.free_spots ? "" : "disabled"}>Reserve &amp; continue to payment</button>`;
  $("#bhold").onclick = safe(async () => {
    try {
      const res = await api("/api/reservations", { method: "POST", body: b });
      payStep(res);
    } catch (err) {
      if (err.status === 409 && err.detail && err.detail.other_times) {
        const d = err.detail;
        $("#quote").innerHTML = `<div class="err">${esc(d.message)}</div><p><b>Try another time:</b> ${d.other_times.map(o => `<span class="chip ${lvlClass(o.level)}">${fmtTime(o.start_time)}</span>`).join(" ") || "none"}</p>
          <p><b>Or another garage:</b> ${d.other_garages.map(o => `<span class="chip">${esc(o.name)} (${o.free} free)</span>`).join(" ") || "none"}</p>`;
      } else throw err;
    }
  });
}

function payStep(res) {
  $("#bookcard").innerHTML = `<h3>Pay for booking ${esc(res.booking_ref)}</h3>
    <p class="muted">${esc(res.garage)} · ${esc(res.floor)}, spot <b>${esc(res.spot_code)}</b> · ${fmtDT(res.start_time)}–${fmtTime(res.end_time)} · ${esc(res.plate)}</p>
    <div class="hold" id="holdbox">Spot held for you: <span id="cd">5:00</span></div>${quoteTable({ ...res.quote, hours: "", base_rate: 0 }).replace(" ( h × AED 0.00)", "")}
    <div class="row mt"><div><label>Payment method (secure gateway – card details never reach SmartPark)</label><select id="ptok">
      <option value="pm_card_visa">Visa •••• 4242</option><option value="pm_card_mastercard">Mastercard •••• 4444</option>
      <option value="pm_wallet_applepay">Apple Pay</option><option value="pm_card_declined">Test card – always declined</option>
      <option value="pm_card_timeout">Test – gateway not responding</option></select></div>
      <div style="flex:0"><button class="btn teal" id="paybtn">Pay ${aed(res.quote.total)}</button></div></div><div id="paymsg"></div>`;
  const until = new Date(res.hold_expires_at).getTime(), base = S.serverNow ? new Date(S.serverNow).getTime() : Date.now(), t0 = Date.now();
  S.holdTimer = setInterval(() => {
    const left = Math.max(0, until - (base + Date.now() - t0));
    $("#cd") && ($("#cd").textContent = `${Math.floor(left / 60000)}:${pad(Math.floor(left / 1000) % 60)}`);
    if (!left) { clearInterval(S.holdTimer); $("#holdbox").textContent = "Hold expired – the spot was released. Please search again."; $("#paybtn").disabled = true; }
  }, 500);
  $("#paybtn").onclick = safe(async () => {
    const r = await api(`/api/reservations/${res.reservation_id}/pay`, { method: "POST", body: { payment_token: $("#ptok").value } });
    if (r.status === "confirmed") { clearInterval(S.holdTimer); confirmed(res.reservation_id, r.receipt_no); tick(); }
    else { $("#paymsg").innerHTML = `<div class="err">${esc(r.message)}</div>`; if (r.status === "failed") { clearInterval(S.holdTimer); $("#paybtn").disabled = true; } }
  });
}

async function confirmed(id, receipt) {
  const [res, qr] = await Promise.all([api(`/api/reservations/${id}`), api(`/api/reservations/${id}/qr`)]);
  $("#bookcard").innerHTML = `<div class="ok-box"><h3 style="color:#166534">Booking confirmed ✓</h3>
    <div class="row" style="align-items:start"><img class="qr" src="${qr.image}" alt="QR pass"><div>
    <p><b>${esc(res.booking_ref)}</b> · receipt ${esc(receipt)}</p><p>${esc(res.garage)}<br>${esc(res.floor)}, spot <b>${esc(res.spot_code)}</b><br>
    ${fmtDT(res.start_time)} – ${fmtTime(res.end_time)} · ${esc(res.plate)}</p><p>Paid <b>${aed(res.quote.total)}</b></p>
    <p class="muted">Your plate opens the barrier automatically. Show this QR pass if the camera cannot read it.</p>
    <a class="btn small" href="#bookings">My bookings</a> <a class="btn ghost small" href="#nav/${id}">Navigate to spot</a></div></div></div>`;
  loadGarages(true);
}

// ================================================================= UC-06 / UC-07
ROUTES.bookings = async () => {
  const rows = await api("/api/reservations");
  const up = rows.filter(r => ["Confirmed", "InUse", "Held"].includes(r.status));
  const past = rows.filter(r => !up.includes(r));
  const card = r => `<div class="card"><div class="row" style="justify-content:space-between;align-items:start">
    <div><div class="chips" style="margin:0 0 6px"><span class="chip st-${r.status}">${r.status}</span><span class="chip">${esc(r.spot_type)}</span></div>
      <b>${esc(r.garage)}</b> · ${esc(r.floor)}, spot <b>${esc(r.spot_code)}</b><br>${fmtDT(r.start_time)} – ${fmtTime(r.end_time)} · ${esc(r.plate)} · ${esc(r.booking_ref)}</div>
    <div class="right"><div class="price">${aed(r.quote.total)}</div>
      ${r.status === "Confirmed" || r.status === "InUse" ? `<button class="btn small" data-qr="${r.reservation_id}">QR pass</button> <a class="btn ghost small" href="#nav/${r.reservation_id}">Navigate</a>` : ""}
      ${r.status === "Confirmed" ? ` <button class="btn ghost small" data-mod="${r.reservation_id}">Change</button> <button class="btn red small" data-cancel="${r.reservation_id}">Cancel</button>` : ""}</div></div>
    <div id="x${r.reservation_id}"></div></div>`;
  $("#app").innerHTML = `<h1>My bookings</h1><p class="sub">Cancel free of charge up to 60 minutes before arrival; later cancellations get 50% back.</p>
    <h2>Upcoming</h2>${up.map(card).join("") || `<div class="card muted">No upcoming bookings. <a href="#find">Find parking</a></div>`}
    <h2 class="mt">History</h2>${past.slice(0, 15).map(card).join("") || `<div class="card muted">No past bookings.</div>`}`;
  $$("[data-qr]").forEach(b => b.onclick = safe(async () => {
    const q = await api(`/api/reservations/${b.dataset.qr}/qr`);
    $("#x" + b.dataset.qr).innerHTML = `<div class="mt row" style="align-items:center"><img class="qr" src="${q.image}" alt="QR pass"><div class="muted">Scan at the lane reader if your plate is not recognised.<br><small>Token: ${esc(q.qr_token)}</small></div></div>`;
  }));
  $$("[data-cancel]").forEach(b => b.onclick = safe(async () => {
    if (!confirm("Cancel this booking?")) return;
    const r = await api(`/api/reservations/${b.dataset.cancel}/cancel`, { method: "POST" });
    toast(`Booking cancelled. Refund ${aed(r.refund)} to your payment method.`); ROUTES.bookings(); tick();
  }));
  $$("[data-mod]").forEach(b => b.onclick = () => {
    const r = rows.find(x => x.reservation_id === +b.dataset.mod);
    const hrs = (new Date(r.end_time) - new Date(r.start_time)) / 3600e3;
    $("#x" + r.reservation_id).innerHTML = `<div class="row mt"><div><label>New arrival</label><input type="datetime-local" id="ms${r.reservation_id}" value="${r.start_time.slice(0, 16)}" step="900"></div>
      <div><label>Duration (h)</label><input type="number" id="md${r.reservation_id}" value="${hrs}" min="0.5" max="24" step="0.5"></div>
      <div style="flex:0"><button class="btn" id="mb${r.reservation_id}">Save change</button></div></div>`;
    $("#mb" + r.reservation_id).onclick = safe(async () => {
      const s = new Date($("#ms" + r.reservation_id).value), e = new Date(s.getTime() + $("#md" + r.reservation_id).value * 3600e3);
      const x = await api(`/api/reservations/${r.reservation_id}`, { method: "PUT", body: { start_time: localISO(s) + ":00", end_time: localISO(e) + ":00" } });
      toast(`Booking changed. New total ${aed(x.total)} (difference ${aed(x.difference)}).`); ROUTES.bookings(); tick();
    });
  });
};

ROUTES.nav = async id => {
  const m = await api(`/api/reservations/${id}/map`);
  const cols = Math.max(...m.spots.map(s => s.x)) + 1, cw = 54, top = 40, rowY = [top, top + 140];
  let svg = `<svg class="chart" viewBox="0 0 ${cols * cw + 120} ${top + 200}">
    <rect x="70" y="${top + 52}" width="${cols * cw + 40}" height="44" fill="#e2e8f0"/><text x="${70 + (cols * cw + 40) / 2}" y="${top + 79}" font-size="12" text-anchor="middle" fill="#64748b">DRIVING AISLE →</text>
    <rect x="4" y="${top + 50}" width="62" height="48" rx="6" fill="#16324f"/><text x="35" y="${top + 72}" fill="#fff" font-size="11" text-anchor="middle">ENTRY</text><text x="35" y="${top + 86}" fill="#fff" font-size="10" text-anchor="middle">ramp</text>`;
  m.spots.forEach(s => {
    const x = 90 + s.x * cw, y = rowY[s.y] - (s.y ? 0 : 0);
    const fill = s.mine ? "#16a34a" : s.type === "ev" ? "#ccfbf1" : s.type === "accessible" ? "#dbeafe" : "#fff";
    svg += `<rect x="${x}" y="${y}" width="${cw - 8}" height="48" rx="5" fill="${fill}" stroke="${s.mine ? "#166534" : "#94a3b8"}" stroke-width="${s.mine ? 3 : 1}"/>
      <text x="${x + (cw - 8) / 2}" y="${y + 28}" font-size="11" font-weight="700" text-anchor="middle" fill="${s.mine ? "#fff" : "#334155"}">${s.code}</text>
      ${s.type !== "standard" ? `<text x="${x + (cw - 8) / 2}" y="${y + 42}" font-size="9" text-anchor="middle" fill="${s.mine ? "#fff" : "#64748b"}">${s.type === "ev" ? "EV" : "♿"}</text>` : ""}`;
  });
  const t = m.spots.find(s => s.mine), tx = 90 + t.x * cw + (cw - 8) / 2;
  svg += `<polyline points="66,${top + 74} ${tx},${top + 74} ${tx},${t.y ? rowY[1] : rowY[0] + 48}" fill="none" stroke="#16a34a" stroke-width="4" stroke-dasharray="8 6" marker-end="url(#arr)"/>
    <defs><marker id="arr" markerWidth="8" markerHeight="8" refX="4" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="#16a34a"/></marker></defs>
    <text x="90" y="22" font-size="13" font-weight="700" fill="#16324f">Zone A</text><text x="90" y="${top + 212}" font-size="13" font-weight="700" fill="#16324f">Zone B</text></svg>`;
  $("#app").innerHTML = `<h1>Navigate to your spot</h1><p class="sub">${esc(m.garage)} · ${esc(m.floor)} · spot <b>${esc(m.spot)}</b></p>
    <div class="card"><div class="map-wrap">${svg}</div><ol class="mt">${m.directions.map(d => `<li>${esc(d)}</li>`).join("")}</ol>
    <a class="btn ghost small" href="#bookings">Back to my bookings</a></div>`;
};
ROUTES.nav.hidden = true;

// ================================================================= FR-15 / FR-30
ROUTES.payments = async () => {
  const p = await api("/api/payments");
  $("#app").innerHTML = `<h1>Payments &amp; receipts</h1><p class="sub">Every payment has an e-receipt; refunds go back to the original payment method.</p>
  <div class="card"><table><thead><tr><th>Date</th><th>Receipt</th><th>Booking</th><th>Garage</th><th>Type</th><th>Method</th><th class="num">Amount</th><th class="num">Refunded</th><th>Status</th></tr></thead>
  <tbody>${p.map(x => `<tr><td>${fmtDT(x.date)}</td><td>${esc(x.receipt_no)}</td><td>${esc(x.booking_ref)}</td><td>${esc(x.garage)}</td><td>${esc(x.kind)}</td><td>${esc(x.method)}</td>
    <td class="num">${aed(x.amount)}</td><td class="num">${x.refunded ? aed(x.refunded) : "–"}</td><td><span class="chip ${x.status === "succeeded" ? "st-InUse" : "st-Cancelled"}">${x.status}</span></td></tr>`).join("") || `<tr><td colspan="9" class="muted">No payments yet.</td></tr>`}</tbody></table></div>`;
};

ROUTES.invoice = async () => {
  const now = S.serverNow ? S.serverNow.slice(0, 7) : new Date().toISOString().slice(0, 7);
  const inv = await api(`/api/corporate/invoice?month=${now}`);
  $("#app").innerHTML = `<h1>Company invoice</h1><p class="sub">One consolidated monthly invoice for every vehicle and driver on the corporate account.</p>
  <div class="card"><div class="row" style="justify-content:space-between"><div><h2>${esc(inv.company)}</h2><div class="muted">${esc(inv.invoice_no)} · ${esc(inv.month)} · billed to ${esc(inv.billing_email)}</div></div>
  <div class="price">${aed(inv.total_aed)}<small>total this month</small></div></div>
  <table class="mt"><thead><tr><th>Date</th><th>Receipt</th><th>Booking</th><th>Vehicle</th><th>Type</th><th class="num">Amount</th></tr></thead>
  <tbody>${inv.lines.map(l => `<tr><td>${esc(l.date)}</td><td>${esc(l.receipt_no)}</td><td>${esc(l.booking_ref)}</td><td>${esc(l.plate)}</td><td>${esc(l.kind)}</td><td class="num">${aed(l.amount)}</td></tr>`).join("") || `<tr><td colspan="6" class="muted">No charges this month.</td></tr>`}
  <tr class="total"><td colspan="5">Total due</td><td class="num">${aed(inv.total_aed)}</td></tr></tbody></table></div>`;
};

ROUTES.vehicles = async () => {
  const [v, me] = await Promise.all([api("/api/vehicles"), api("/api/me")]);
  $("#app").innerHTML = `<h1>${S.user.role === "corporate" ? "Fleet" : "Vehicles"} &amp; settings</h1><p class="sub">Your saved vehicles are matched by plate recognition at the gate.</p>
  <div class="grid g2"><div class="card"><h2>Saved vehicles</h2><table><thead><tr><th>Plate</th><th>Emirate</th><th>Type</th><th>Accessible permit</th><th></th></tr></thead>
    <tbody>${v.map(x => `<tr><td><b>${esc(x.plate_number)}</b></td><td>${esc(x.plate_source)}</td><td>${esc(x.vehicle_type)}</td><td>${x.has_accessible_permit ? "Yes" : "No"}</td><td class="right"><button class="btn ghost small" data-del="${x.vehicle_id}">Remove</button></td></tr>`).join("")}</tbody></table>
    <h3 class="mt">Add a vehicle</h3><div class="row"><div><label>Plate number</label><input id="vp" placeholder="AD 1 12345"></div>
    <div><label>Emirate</label><select id="vs">${["Abu Dhabi", "Dubai", "Sharjah", "Ajman", "Ras Al Khaimah", "Fujairah", "Umm Al Quwain"].map(e => `<option>${e}</option>`).join("")}</select></div>
    <div><label>Type</label><select id="vt"><option value="car">Car</option><option value="ev">Electric (EV)</option></select></div></div>
    <label class="mt"><input type="checkbox" id="vpm" style="width:auto"> Vehicle has an accessible parking permit</label>
    <button class="btn mt" id="vadd">Add vehicle</button></div>
  <div class="card"><h2>Notifications</h2><label>Send my booking messages by</label><select id="ch">
    <option value="push">Push notification</option><option value="sms">SMS</option><option value="email">Email</option></select>
    <button class="btn mt" id="chsave">Save</button><p class="muted mt">${esc(me.email)} · ${esc(me.phone)}</p></div></div>`;
  $("#ch").value = me.notify_channel;
  $("#chsave").onclick = safe(async () => { await api("/api/me", { method: "PATCH", body: { notify_channel: $("#ch").value } }); toast("Saved"); });
  $("#vadd").onclick = safe(async () => { await api("/api/vehicles", { method: "POST", body: { plate_number: $("#vp").value, plate_source: $("#vs").value, vehicle_type: $("#vt").value, has_accessible_permit: $("#vpm").checked } }); toast("Vehicle added"); ROUTES.vehicles(); });
  $$("[data-del]").forEach(b => b.onclick = safe(async () => { await api(`/api/vehicles/${b.dataset.del}`, { method: "DELETE" }); toast("Vehicle removed"); ROUTES.vehicles(); }));
};

// ================================================================= UC-08/09/10
ROUTES.lane = async () => {
  const gs = await api("/api/admin/garages");
  S.laneGarage = S.laneGarage || gs[0].garage_id;
  $("#app").innerHTML = `<h1>Lane console</h1><p class="sub">Entry/exit decisions from the ANPR camera or QR reader, expected arrivals and manual override.</p>
  <div class="card row"><div style="max-width:320px"><label>Garage</label><select id="lg">${gs.map(g => `<option value="${g.garage_id}">${esc(g.name)}</option>`).join("")}</select></div>
    <div class="muted" style="flex:3">Camera events are simulated in the prototype: enter a plate and the recognition confidence, or a QR token.</div></div>
  <div class="grid g2"><div class="card"><h2>Lane simulator</h2>
      <div class="row"><div><label>Plate read by camera</label><input id="lp" placeholder="AD 1 23456"></div>
      <div><label>Confidence: <span id="lcv">0.97</span></label><input type="range" id="lc" min="0.5" max="1" step="0.01" value="0.97"></div></div>
      <div class="row mt"><div><label>…or QR token scanned</label><input id="lq" placeholder="from the driver's QR pass"></div></div>
      <div class="row mt"><button class="btn teal" id="len">Vehicle at ENTRY</button><button class="btn amber" id="lex">Vehicle at EXIT</button></div>
      <div id="lres" class="mt"><div class="lane-result"><div class="big">WAITING</div><div>No vehicle at the lane</div></div></div></div>
    <div class="card"><h2>Manual override</h2><p class="muted">Use when the plate and QR both fail. Every override is recorded in the audit log.</p>
      <div class="row"><div><label>Plate</label><input id="op"></div><div><label>Decision</label><select id="od"><option>ADMIT</option><option>DENY</option></select></div></div>
      <div class="mt"><label>Reason (required)</label><input id="or" placeholder="e.g. Plate damaged, booking shown on phone"></div>
      <button class="btn red mt" id="obtn">Record override</button><div id="ores"></div></div></div>
  <div class="grid g2"><div class="card"><h2>Expected arrivals (next 3 h)</h2><div id="exp"></div></div><div class="card"><h2>Vehicles inside</h2><div id="ins"></div></div></div>
  <div class="card"><h2>Recent lane events</h2><div id="evs"></div></div>`;
  $("#lg").value = S.laneGarage; $("#lg").onchange = () => { S.laneGarage = +$("#lg").value; refreshLane(); };
  $("#lc").oninput = () => ($("#lcv").textContent = $("#lc").value);
  const send = dir => safe(async () => {
    const body = { garage_id: S.laneGarage, lane: dir === "entry" ? "E1" : "X1", plate: $("#lp").value || null, confidence: +$("#lc").value, qr_token: $("#lq").value || null };
    const r = await api(`/api/gate/${dir}`, { method: "POST", body });
    $("#lres").innerHTML = `<div class="lane-result ${r.decision}"><div class="big">${r.decision === "ADMIT" ? (dir === "entry" ? "ADMIT" : "EXIT OK") : "DENY"}</div>
      <div>${esc(r.lane_message)}</div><div class="barrier">Barrier ${r.barrier}</div>
      ${r.spot ? `<div class="mt">Booking ${esc(r.booking_ref)} → ${esc(r.floor)}, spot <b>${esc(r.spot)}</b></div>` : ""}
      ${r.overstay_fee ? `<div class="mt">Overstay ${r.overstay_minutes} min – charged ${aed(r.overstay_fee)}</div>` : ""}
      ${r.attendant_alerted ? `<div class="mt"><small>Reason code ${esc(r.reason)} – attendant alerted</small></div>` : ""}</div>`;
    $("#lq").value = ""; refreshLane();
  });
  $("#len").onclick = send("entry"); $("#lex").onclick = send("exit");
  $("#obtn").onclick = safe(async () => {
    const r = await api("/api/gate/override", { method: "POST", body: { garage_id: S.laneGarage, plate: $("#op").value, decision: $("#od").value, reason: $("#or").value } });
    $("#ores").innerHTML = `<p class="mt"><b>Override #${r.override_id} recorded</b> – barrier ${r.barrier}${r.matched_booking ? `, matched booking ${esc(r.matched_booking)}` : ""}.</p>`; refreshLane();
  });
  refreshLane();
};

const refreshLane = safe(async () => {
  const [a, ev] = await Promise.all([api(`/api/gate/${S.laneGarage}/arrivals`), api(`/api/gate/${S.laneGarage}/events`)]);
  const tbl = (rows, cols) => rows.length ? `<table><thead><tr>${cols.map(c => `<th>${c[0]}</th>`).join("")}</tr></thead><tbody>${rows.map(r => `<tr>${cols.map(c => `<td>${c[1](r)}</td>`).join("")}</tr>`).join("")}</tbody></table>` : `<p class="muted">None.</p>`;
  $("#exp").innerHTML = tbl(a.expected, [["Plate", r => `<b>${esc(r.plate)}</b>`], ["Spot", r => `${esc(r.floor)} ${esc(r.spot)}`], ["Time", r => `${fmtTime(r.start_time)}–${fmtTime(r.end_time)}`], ["Driver", r => esc(r.driver)],
    ["", r => `<button class="btn ghost small" data-sim="${esc(r.plate)}">Simulate</button>`]]);
  $("#ins").innerHTML = tbl(a.inside, [["Plate", r => `<b>${esc(r.plate)}</b>`], ["Spot", r => `${esc(r.floor)} ${esc(r.spot)}`], ["Until", r => fmtTime(r.end_time)],
    ["", r => `<button class="btn ghost small" data-simx="${esc(r.plate)}">Simulate exit</button>`]]);
  $("#evs").innerHTML = tbl(ev, [["Time", r => fmtDT(r.time)], ["Lane", r => esc(r.lane)], ["Dir.", r => esc(r.direction)], ["Plate", r => esc(r.plate || "—")], ["Conf.", r => r.confidence ?? "—"],
    ["QR", r => r.qr ? "yes" : ""], ["Decision", r => `<span class="chip ${r.decision === "DENY" ? "st-Cancelled" : "st-InUse"}">${r.decision}</span>`], ["Reason", r => esc(r.reason)]]);
  $$("[data-sim]").forEach(b => b.onclick = () => { $("#lp").value = b.dataset.sim; $("#lc").value = 0.97; $("#lcv").textContent = "0.97"; $("#len").click(); });
  $$("[data-simx]").forEach(b => b.onclick = () => { $("#lp").value = b.dataset.simx; $("#lc").value = 0.97; $("#lcv").textContent = "0.97"; $("#lex").click(); });
});

// ================================================================= UC-13
ROUTES.dashboard = async () => {
  const gs = await api("/api/admin/garages");
  $("#app").innerHTML = `<div class="row" style="justify-content:space-between;align-items:center"><div><h1>Operations dashboard</h1><p class="sub">Occupancy, revenue, no-shows and peak hours for the last 14 days.</p></div>
    <div class="row" style="flex:0 0 auto"><select id="dg" style="width:240px"><option value="">All garages</option>${gs.map(g => `<option value="${g.garage_id}">${esc(g.name)}</option>`).join("")}</select>
    <button class="btn ghost" id="csv">Export CSV</button></div></div><div id="dash"></div>`;
  const load = safe(async () => {
    const gid = $("#dg").value, r = await api(`/api/admin/reports/summary${gid ? "?garage_id=" + gid : ""}`);
    const k = r.kpis;
    $("#dash").innerHTML = `<div class="grid g5">${[["Reservations", k.reservations], ["Revenue", aed(k.revenue_aed)], ["Average booking", aed(k.avg_booking_aed)], ["No-show rate", k.no_show_rate_pct + "%"], ["Cancellations", k.cancellations]]
      .map(([l, v]) => `<div class="kpi"><div class="l">${l}</div><div class="v">${v}</div></div>`).join("")}</div>
      <div class="grid g2 mt"><div class="card"><h3>Average occupancy by hour of day (occupancy sensors)</h3>${barChart(r.occupancy_by_hour, { value: o => o.occupancy_pct, label: o => pad(o.hour), color: o => r.peak_hours.includes(o.hour) ? "#c2410c" : "#1f4672", unit: "%", title: "Occupancy" })}
        <div class="legend"><span style="--c:#c2410c">peak hours: ${r.peak_hours.map(h => pad(h) + ":00").join(", ")}</span><span style="--c:#1f4672">other hours</span></div></div>
      <div class="card"><h3>Net revenue per day (AED)</h3>${lineChart(r.revenue_by_day, { value: d => d.revenue, label: d => d.date.slice(8) + "/" + d.date.slice(5, 7) })}</div></div>
      <div class="grid g2"><div class="card"><h3>Bookings by spot type</h3>${barChart(Object.entries(r.bookings_by_type).map(([t, n]) => ({ t, n })), { value: x => x.n, label: x => x.t, color: x => TYPE_COLOR[x.t], height: 150, title: "Bookings by type" })}</div>
      <div class="card"><h3>Report details</h3><table><tbody><tr><td>Scope</td><td>${esc(r.garage)}</td></tr><tr><td>Period</td><td>${fmtDate(r.period.from)} – ${fmtDate(r.period.to)}</td></tr>
        <tr><td>Capacity</td><td>${k.capacity} spots</td></tr><tr><td>Busiest hour</td><td>${pad(r.peak_hours[0])}:00 (${r.occupancy_by_hour[r.peak_hours[0]].occupancy_pct}%)</td></tr></tbody></table></div></div>`;
  });
  $("#dg").onchange = load;
  $("#csv").onclick = safe(async () => {
    const gid = $("#dg").value;
    const txt = await api(`/api/admin/reports/export.csv${gid ? "?garage_id=" + gid : ""}`);
    const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([txt], { type: "text/csv" })); a.download = "smartpark_report.csv"; a.click();
  });
  load();
};

// ================================================================= UC-11
ROUTES.garages = async () => {
  const gs = await api("/api/admin/garages");
  $("#app").innerHTML = `<h1>Garages &amp; spots</h1><p class="sub">Click a spot to switch it on or off. Orange line = occupied (sensor).</p>
    <div class="legend" style="margin-bottom:12px"><span style="--c:#fff;outline:1px solid #cbd5e1">standard</span><span style="--c:#ecfeff">EV</span><span style="--c:#eff6ff">accessible</span><span style="--c:#e5e7eb">inactive</span></div>
    ${gs.map(g => `<div class="card"><div class="row" style="justify-content:space-between"><h2>${esc(g.name)} <span class="chip ${g.is_active ? "st-InUse" : "st-Cancelled"}">${g.is_active ? "active" : "inactive"}</span></h2>
      <div style="flex:0 0 auto"><button class="btn ghost small" data-tg="${g.garage_id}">${g.is_active ? "Deactivate" : "Activate"}</button> <button class="btn ghost small" data-fl="${g.garage_id}">+ Floor</button></div></div>
      <p class="muted" style="margin-top:-6px">${esc(g.address)}</p>
      ${g.floors.map(f => `<div><b>${esc(f.name)}</b> <button class="btn ghost small" data-zn="${f.floor_id}">+ Zone</button>${f.zones.map(z => `<div class="muted" style="font-size:12px;margin-top:4px">Zone ${esc(z.name)}</div><div class="spots">${z.spots.map(s => `<div class="spot ${s.type} ${s.active ? "" : "off"} ${s.occupied ? "occ" : ""}" data-sp="${s.spot_id}" data-on="${s.active}" title="${s.code} (${s.type})">${s.code}</div>`).join("")}</div>`).join("")}</div>`).join("")}</div>`).join("")}
    <div class="card"><h2>Add a garage</h2><div class="row"><div><label>Name</label><input id="ng"></div><div><label>Area</label><input id="na"></div><div style="flex:2"><label>Address</label><input id="nd"></div></div>
    <div class="row mt"><div><label>Latitude</label><input id="nla" value="24.49"></div><div><label>Longitude</label><input id="nlo" value="54.40"></div><div><label>Base rate (AED/h)</label><input id="nb" value="8"></div>
    <div style="flex:0"><button class="btn" id="ngb">Create garage</button></div></div></div>`;
  $$("[data-sp]").forEach(el => el.onclick = safe(async () => { await api(`/api/admin/spots/${el.dataset.sp}`, { method: "PATCH", body: { is_active: el.dataset.on !== "true" } }); ROUTES.garages(); }));
  $$("[data-tg]").forEach(b => b.onclick = safe(async () => { await api(`/api/admin/garages/${b.dataset.tg}`, { method: "PATCH" }); ROUTES.garages(); }));
  $$("[data-fl]").forEach(b => b.onclick = safe(async () => { const n = prompt("Level number of the new floor:"); if (!n) return; await api(`/api/admin/garages/${b.dataset.fl}/floors`, { method: "POST", body: { level_no: +n, name: "Level " + n } }); ROUTES.garages(); }));
  $$("[data-zn]").forEach(b => b.onclick = safe(async () => {
    const name = prompt("Zone name (e.g. C):"); if (!name) return;
    const st = +prompt("Number of standard spots:", "10"), ev = +prompt("Number of EV spots:", "2"), ac = +prompt("Number of accessible spots:", "1");
    await api(`/api/admin/floors/${b.dataset.zn}/zones`, { method: "POST", body: { name, standard: st, ev, accessible: ac } }); ROUTES.garages();
  }));
  $("#ngb").onclick = safe(async () => { await api("/api/admin/garages", { method: "POST", body: { name: $("#ng").value, area: $("#na").value, address: $("#nd").value, latitude: +$("#nla").value, longitude: +$("#nlo").value, base_standard: +$("#nb").value } }); toast("Garage created – add floors and zones next"); ROUTES.garages(); });
};

// ================================================================= UC-12
const RULE_LABELS = [["base_standard", "Standard rate (AED/h)"], ["base_ev", "EV rate (AED/h)"], ["base_accessible", "Accessible rate (AED/h)"],
  ["mult_low", "Multiplier < 40% occupancy"], ["mult_normal", "Multiplier 40–69%"], ["mult_high", "Multiplier 70–84%"], ["mult_peak", "Multiplier ≥ 85%"],
  ["floor_multiplier", "Minimum multiplier (floor)"], ["cap_multiplier", "Maximum multiplier (cap)"], ["grace_minutes", "Grace period (min)"],
  ["free_cancel_minutes", "Free cancellation until (min before)"], ["late_refund_pct", "Late cancellation refund (%)"], ["overstay_multiplier", "Overstay multiplier"]];

ROUTES.pricing = async () => {
  const gs = await api("/api/admin/garages");
  S.priceGarage = S.priceGarage || gs[0].garage_id;
  $("#app").innerHTML = `<h1>Pricing rules</h1><p class="sub">Base rates and demand multipliers. Accessible spots never get a surcharge; the platform cap is ×2.0.</p>
  <div class="card" style="max-width:420px"><label>Garage</label><select id="pg">${gs.map(g => `<option value="${g.garage_id}">${esc(g.name)}</option>`).join("")}</select></div><div id="pbody"></div>`;
  $("#pg").value = S.priceGarage; $("#pg").onchange = () => { S.priceGarage = +$("#pg").value; loadPricing(); };
  loadPricing();
};

const previewTable = rows => `<table><thead><tr><th>Predicted occupancy</th><th class="num">Standard</th><th class="num">EV</th><th class="num">Accessible</th></tr></thead>
  <tbody>${rows.map(r => `<tr><td>${r.occupancy_pct}%</td><td class="num">${aed(r.standard)}</td><td class="num">${aed(r.ev)}</td><td class="num">${aed(r.accessible)}</td></tr>`).join("")}</tbody></table>`;

const loadPricing = safe(async () => {
  const p = await api(`/api/admin/garages/${S.priceGarage}/pricing`), a = p.active;
  $("#pbody").innerHTML = `<div class="grid g2"><div class="card"><h2>Active rules – version ${a.version}</h2><div class="grid g2">
    ${RULE_LABELS.map(([k, l]) => `<div><label for="r_${k}">${l}</label><input id="r_${k}" type="number" step="0.01" value="${a[k]}"></div>`).join("")}
    <div><label>Takes effect</label><input type="datetime-local" id="r_eff"></div></div>
    <div class="row mt"><button class="btn ghost" id="pprev">Preview prices</button><button class="btn" id="psave">Save new version</button></div><div id="perr"></div></div>
    <div><div class="card"><h2>Price preview (per hour)</h2><div id="ptab">${previewTable(p.preview)}</div></div>
    <div class="card"><h2>Version history</h2><table><thead><tr><th>Ver.</th><th>Effective from</th><th class="num">Std</th><th class="num">EV</th><th class="num">Cap</th></tr></thead>
      <tbody>${p.versions.map(v => `<tr><td>${v.version}</td><td>${fmtDT(v.effective_from)}</td><td class="num">${v.base_standard}</td><td class="num">${v.base_ev}</td><td class="num">×${v.cap_multiplier}</td></tr>`).join("")}</tbody></table></div></div></div>`;
  const body = () => { const b = { expected_version: a.version }; RULE_LABELS.forEach(([k]) => (b[k] = +$("#r_" + k).value)); if ($("#r_eff").value) b.effective_from = $("#r_eff").value + ":00"; return b; };
  $("#pprev").onclick = safe(async () => {
    const r = await api(`/api/admin/garages/${S.priceGarage}/pricing/preview`, { method: "POST", body: body() });
    $("#perr").innerHTML = r.errors.map(e => `<div class="err">${esc(e)}</div>`).join(""); if (!r.errors.length) $("#ptab").innerHTML = previewTable(r.preview);
  });
  $("#psave").onclick = safe(async () => {
    try { const r = await api(`/api/admin/garages/${S.priceGarage}/pricing`, { method: "PUT", body: body() }); toast(`${r.message} – version ${r.version}`); loadPricing(); }
    catch (e) { $("#perr").innerHTML = (e.detail && e.detail.errors ? e.detail.errors : [e.message]).map(x => `<div class="err">${esc(x)}</div>`).join(""); }
  });
});

// ================================================================= UC-14
ROUTES.model = async () => {
  const m = await api("/api/admin/forecast/models");
  const act = m.find(x => x.status === "Active");
  $("#app").innerHTML = `<h1>Forecast model</h1><p class="sub">Random-forest model that predicts hourly occupancy from garage, hour of day and day of week.</p>
  <div class="grid g4"><div class="kpi"><div class="l">Algorithm</div><div class="v" style="font-size:16px">${esc(act?.algorithm || "–")}</div></div>
    <div class="kpi"><div class="l">Hold-out error (MAE)</div><div class="v">${act?.mae_pct ?? "–"} pts</div></div>
    <div class="kpi"><div class="l">Training samples</div><div class="v">${act?.n_samples ?? "–"}</div></div>
    <div class="kpi"><div class="l">Last trained</div><div class="v" style="font-size:16px">${fmtDT(act?.trained_at)}</div></div></div>
  <div class="grid g2 mt"><div class="card"><h2>Retrain now</h2><p class="muted">Retrains on all stored occupancy history and replaces the active model.</p><button class="btn" id="rt">Retrain model</button></div>
  <div class="card"><h2>Schedule retraining</h2><div class="row"><div><label>Run at</label><input type="datetime-local" id="sat"></div><div style="flex:0"><button class="btn ghost" id="sch">Schedule</button></div></div></div></div>
  <div class="card"><h2>Model history</h2><table><thead><tr><th>#</th><th>Status</th><th>Trained</th><th>Scheduled for</th><th class="num">Samples</th><th class="num">MAE (pts)</th></tr></thead>
  <tbody>${m.map(x => `<tr><td>${x.model_id}</td><td><span class="chip ${x.status === "Active" ? "st-InUse" : x.status === "Scheduled" ? "st-Held" : "st-Completed"}">${x.status}</span></td><td>${fmtDT(x.trained_at)}</td><td>${fmtDT(x.scheduled_for)}</td><td class="num">${x.n_samples || ""}</td><td class="num">${x.mae_pct ?? ""}</td></tr>`).join("")}</tbody></table></div>`;
  $("#rt").onclick = safe(async () => { $("#rt").disabled = true; $("#rt").textContent = "Training…"; const r = await api("/api/admin/forecast/retrain", { method: "POST" }); toast(`Model retrained – MAE ${r.mae_pct} points`); ROUTES.model(); });
  $("#sch").onclick = safe(async () => { await api("/api/admin/forecast/schedule", { method: "POST", body: { run_at: $("#sat").value + ":00" } }); toast("Retraining scheduled"); ROUTES.model(); });
};

// ================================================================= FR-29
ROUTES.users = async () => {
  const [u, a] = await Promise.all([api("/api/admin/users"), api("/api/admin/audit")]);
  $("#app").innerHTML = `<h1>Users &amp; audit log</h1><p class="sub">Change roles or disable accounts. Every administrator action is recorded.</p>
  <div class="card"><table><thead><tr><th>Name</th><th>Email</th><th>Phone</th><th>Role</th><th>Status</th><th></th></tr></thead><tbody>
  ${u.map(x => `<tr><td>${esc(x.name)}</td><td>${esc(x.email)}</td><td>${esc(x.phone)}</td><td><select data-role="${x.user_id}" style="width:130px" ${x.user_id === S.user.user_id ? "disabled" : ""}>${["driver", "corporate", "attendant", "admin"].map(r => `<option ${r === x.role ? "selected" : ""}>${r}</option>`).join("")}</select></td>
    <td><span class="chip ${x.is_active ? "st-InUse" : "st-Cancelled"}">${x.is_active ? "active" : "disabled"}</span></td>
    <td>${x.user_id === S.user.user_id ? "" : `<button class="btn ghost small" data-act="${x.user_id}" data-on="${x.is_active}">${x.is_active ? "Disable" : "Enable"}</button>`}</td></tr>`).join("")}</tbody></table></div>
  <div class="card"><h2>Audit log</h2><table><thead><tr><th>Time</th><th>User</th><th>Action</th><th>Entity</th><th>Change</th></tr></thead>
  <tbody>${a.map(x => `<tr><td>${fmtDT(x.time)}</td><td>${esc(x.user)}</td><td>${esc(x.action)}</td><td>${esc(x.entity)} ${esc(x.entity_id || "")}</td><td style="font-size:11px;max-width:520px;word-break:break-word">${esc((x.new || "").slice(0, 220))}</td></tr>`).join("") || `<tr><td colspan="5" class="muted">No actions yet.</td></tr>`}</tbody></table></div>`;
  $$("[data-role]").forEach(s => s.onchange = safe(async () => { await api(`/api/admin/users/${s.dataset.role}`, { method: "PATCH", body: { role: s.value } }); toast("Role updated"); ROUTES.users(); }));
  $$("[data-act]").forEach(b => b.onclick = safe(async () => { await api(`/api/admin/users/${b.dataset.act}`, { method: "PATCH", body: { is_active: b.dataset.on !== "true" } }); ROUTES.users(); }));
};

// ================================================================= demo tools
ROUTES.demo = async () => {
  const c = await api("/api/admin/demo/clock");
  $("#app").innerHTML = `<h1>Demo tools</h1><p class="sub">For the live demonstration: move the system clock to show timed behaviour (reminders, no-show release, overstay fees).</p>
  <div class="grid g2"><div class="card"><h2>System clock</h2><p>Now: <b>${fmtDT(c.now)}</b> (offset ${c.offset_minutes} min)</p>
    <div class="row">${[15, 30, 60, 120].map(m => `<button class="btn ghost" data-adv="${m}">+${m} min</button>`).join("")}<button class="btn ghost" id="crs">Reset</button></div><div id="jobres" class="mt"></div></div>
  <div class="card"><h2>Scheduled jobs</h2><p class="muted">Runs automatically every 30 seconds; run once now:</p><button class="btn" id="jr">Run jobs now</button></div>
  <div class="card"><h2>Occupancy sensor</h2><div class="row"><div><label>Garage ID</label><input id="sg" value="1"></div><div><label>Spot code</label><input id="ss" value="1A01"></div>
    <div><label>State</label><select id="so"><option value="true">Occupied</option><option value="false">Free</option></select></div><div style="flex:0"><button class="btn" id="sb">Send event</button></div></div></div></div>`;
  const show = j => ($("#jobres").innerHTML = `<table><tbody>${Object.entries(j).map(([k, v]) => `<tr><td>${k.replace(/_/g, " ")}</td><td class="num">${v}</td></tr>`).join("")}</tbody></table>`);
  $$("[data-adv]").forEach(b => b.onclick = safe(async () => { const r = await api("/api/admin/demo/clock", { method: "POST", body: { minutes: +b.dataset.adv } }); toast("Clock moved to " + fmtDT(r.now)); await ROUTES.demo(); show(r.jobs); tick(); }));
  $("#crs").onclick = safe(async () => { await api("/api/admin/demo/clock/reset", { method: "POST" }); ROUTES.demo(); tick(); });
  $("#jr").onclick = safe(async () => show(await api("/api/admin/jobs/run", { method: "POST" })));
  $("#sb").onclick = safe(async () => { const r = await api("/api/sensors/event", { method: "POST", body: { garage_id: +$("#sg").value, spot_code: $("#ss").value, occupied: $("#so").value === "true" } }); toast(`Spot ${r.spot} is now ${r.occupied ? "occupied" : "free"}`); });
};

// ------------------------------------------------------------------ boot
NAV.driver.push(["nav", ""]); NAV.corporate.push(["nav", ""]);
const _shell = shell;
shell = function () { _shell(); $$("#nav a").forEach(a => { if (!a.textContent) a.remove(); }); };
function startPolling() { clearInterval(S.poll); tick(); S.poll = setInterval(tick, 20000); }

$("#logout").onclick = () => logout();
$("#bell").onclick = openDrawer;
$("#closeDrawer").onclick = () => ($("#drawer").hidden = true);
window.addEventListener("hashchange", router);
if (S.user) startPolling();
router();
