/* ZetaVPN — admin single-page app (framework-free). */
(function () {
  "use strict";
  var Z = window.Zeta;

  // ---------------- helpers ----------------
  function $(s, r) { return (r || document).querySelector(s); }
  function h(html) {
    var t = document.createElement("template");
    t.innerHTML = html.trim();
    return t.content.firstElementChild;
  }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function fmtBytes(n) {
    n = Number(n) || 0;
    if (n < 1024) return n + " B";
    var u = ["KB", "MB", "GB", "TB", "PB"], i = -1;
    do { n /= 1024; i++; } while (n >= 1024 && i < u.length - 1);
    return n.toFixed(n < 10 ? 2 : 1) + " " + u[i];
  }
  function fmtUptime(s) {
    s = Number(s) || 0;
    var d = Math.floor(s / 86400), hh = Math.floor((s % 86400) / 3600), mm = Math.floor((s % 3600) / 60);
    return (d ? d + "d " : "") + hh + "h " + mm + "m";
  }
  function fmtExpiry(ms) {
    if (!ms) return '<span class="muted">Never</span>';
    var days = Math.ceil((ms - Date.now()) / 86400000);
    var d = new Date(ms).toISOString().slice(0, 10);
    if (days < 0) return '<span style="color:var(--danger)">Expired</span>';
    return esc(d) + ' <span class="muted">(' + days + "d)</span>";
  }
  var timers = [];
  function clearTimers() { timers.forEach(clearInterval); timers = []; }
  function every(ms, fn) { fn(); var id = setInterval(fn, ms); timers.push(id); return id; }

  function toast(msg, type) {
    var root = $("#toast-root");
    var t = h('<div class="toast ' + (type || "ok") + '">' + esc(msg) + "</div>");
    root.appendChild(t);
    setTimeout(function () { t.style.opacity = "0"; setTimeout(function () { t.remove(); }, 300); }, 3200);
  }
  function copy(text) {
    navigator.clipboard.writeText(text).then(function () { toast("Copied to clipboard"); },
      function () { toast("Copy failed", "err"); });
  }
  function modal(opts) {
    var back = h('<div class="modal-backdrop"></div>');
    var size = opts.size === "lg" ? " lg" : "";
    var m = h(
      '<div class="modal' + size + '">' +
        '<div class="modal-head"><h3>' + esc(opts.title) + '</h3>' +
        '<button class="icon-btn" data-x>' + IC.x + "</button></div>" +
        '<div class="modal-body"></div>' +
        (opts.foot ? '<div class="modal-foot"></div>' : "") +
      "</div>"
    );
    $(".modal-body", m).innerHTML = opts.body || "";
    if (opts.foot) $(".modal-foot", m).innerHTML = opts.foot;
    back.appendChild(m);
    $("#modal-root").appendChild(back);
    function close() { back.remove(); }
    back.addEventListener("click", function (e) { if (e.target === back) close(); });
    $("[data-x]", m).addEventListener("click", close);
    return { root: m, close: close, body: $(".modal-body", m), foot: opts.foot ? $(".modal-foot", m) : null };
  }
  function confirmModal(message) {
    return new Promise(function (resolve) {
      var mo = modal({
        title: "Please confirm",
        body: '<p style="margin:0 0 6px">' + esc(message) + "</p>",
        foot: '<button class="btn ghost" data-no>Cancel</button><button class="btn danger" data-yes>Confirm</button>',
      });
      $("[data-no]", mo.foot).onclick = function () { mo.close(); resolve(false); };
      $("[data-yes]", mo.foot).onclick = function () { mo.close(); resolve(true); };
    });
  }

  // ---------------- icons ----------------
  var IC = {
    dash: '<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="9"/><rect x="14" y="3" width="7" height="5"/><rect x="14" y="12" width="7" height="9"/><rect x="3" y="16" width="7" height="5"/></svg>',
    inbound: '<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 7h16M4 12h16M4 17h16"/></svg>',
    ssh: '<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 9l3 3-3 3M13 15h4"/></svg>',
    cog: '<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M19 12a7 7 0 0 0-.1-1l2-1.5-2-3.4-2.3 1a7 7 0 0 0-1.7-1l-.3-2.6h-4l-.3 2.6a7 7 0 0 0-1.7 1l-2.3-1-2 3.4 2 1.5a7 7 0 0 0 0 2l-2 1.5 2 3.4 2.3-1a7 7 0 0 0 1.7 1l.3 2.6h4l.3-2.6a7 7 0 0 0 1.7-1l2.3 1 2-3.4-2-1.5c.1-.3.1-.7.1-1z"/></svg>',
    logout: '<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9"/></svg>',
    plus: '<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 5v14M5 12h14"/></svg>',
    trash: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6"/></svg>',
    edit: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/></svg>',
    link: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><path d="M10 13a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-1 1M14 11a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l1-1"/></svg>',
    power: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2v10M18 6a9 9 0 1 1-12 0"/></svg>',
    refresh: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><path d="M23 4v6h-6M1 20v-6h6"/><path d="M20.5 9A9 9 0 0 0 5.6 5.6L1 10m22 4l-4.6 4.4A9 9 0 0 1 3.5 15"/></svg>',
    users: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8M23 21v-2a4 4 0 0 0-3-3.9M16 3.1a4 4 0 0 1 0 7.8"/></svg>',
    back: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><path d="M19 12H5M12 19l-7-7 7-7"/></svg>',
    x: '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6L6 18M6 6l12 12"/></svg>',
  };

  // ---------------- state ----------------
  var PROTOCOLS = [];
  var state = { view: "dashboard", inbound: null };

  // ---------------- shell ----------------
  function showLogin() {
    clearTimers();
    $("#app-view").classList.add("hidden");
    $("#login-view").classList.remove("hidden");
    var err = $("#login-err");
    $("#login-form").onsubmit = async function (e) {
      e.preventDefault();
      err.textContent = "";
      try {
        await Z.login($("#l-user").value.trim(), $("#l-pass").value, $("#l-totp").value.trim());
        boot();
      } catch (ex) {
        err.textContent = ex.message;
      }
    };
  }

  async function boot() {
    if (!Z.isAuthed()) return showLogin();
    $("#login-view").classList.add("hidden");
    $("#app-view").classList.remove("hidden");
    try {
      var me = await Z.get("/auth/me");
      window.__me_totp = me.totp_enabled;
      $("#who").textContent = me.username + " · " + me.role;
    } catch (e) { return showLogin(); }
    try { PROTOCOLS = await Z.get("/system/protocols"); } catch (e) { PROTOCOLS = []; }
    navigate(state.view || "dashboard");
  }

  function navigate(view) {
    state.view = view;
    document.querySelectorAll(".nav-item").forEach(function (n) {
      n.classList.toggle("active", n.dataset.view === view);
    });
    clearTimers();
    var page = $("#page");
    page.innerHTML = '<div class="empty">Loading…</div>';
    (VIEWS[view] || VIEWS.dashboard)(page);
  }

  // ---------------- views ----------------
  var VIEWS = {};

  VIEWS.dashboard = async function (page) {
    setTitle("Dashboard", "Server overview & live traffic");
    var s;
    try { s = await Z.get("/system/stats"); } catch (e) { page.innerHTML = '<div class="empty">' + esc(e.message) + "</div>"; return; }
    var memPct = s.mem.percent, diskPct = s.disk.percent, cpu = Math.round(s.cpu_percent);
    page.innerHTML =
      '<div class="grid cols-4">' +
        statCard("CPU", cpu + "%", s.cpu_count + " cores · load " + s.load_avg[0], cpu) +
        statCard("Memory", memPct + "%", fmtBytes(s.mem.used) + " / " + fmtBytes(s.mem.total), memPct) +
        statCard("Disk", diskPct + "%", fmtBytes(s.disk.used) + " / " + fmtBytes(s.disk.total), diskPct) +
        statCard("Uptime", fmtUptime(s.uptime_seconds), s.brand + " v" + s.version, null) +
      "</div>" +
      '<div class="grid cols-3" style="margin-top:18px">' +
        miniCard("Inbounds", s.counts.active_inbounds + " / " + s.counts.inbounds) +
        miniCard("Proxy clients", s.counts.clients) +
        miniCard("SSH accounts", s.counts.ssh_accounts) +
      "</div>" +
      '<div class="grid cols-2" style="margin-top:18px">' +
        '<div class="card pad-lg"><div class="card-head"><h3>Network throughput</h3><span class="muted" id="tp-label"></span></div><canvas class="chart" id="net-chart"></canvas></div>' +
        '<div class="card pad-lg"><div class="card-head"><h3>Services</h3></div><div id="svc-list"></div></div>' +
      "</div>" +
      '<div class="card pad-lg" style="margin-top:18px"><div class="card-head"><h3>Proxy traffic (all inbounds)</h3></div>' +
        '<p style="margin:0;font-size:15px">↑ ' + fmtBytes(s.proxy_traffic.up) + ' &nbsp;·&nbsp; ↓ ' + fmtBytes(s.proxy_traffic.down) + "</p></div>";

    var svc = $("#svc-list");
    svc.innerHTML = s.services.map(function (x) {
      return '<div style="display:flex;align-items:center;justify-content:space-between;padding:9px 0;border-bottom:1px solid var(--border)">' +
        '<span><span class="dot ' + (x.running ? "up" : "down") + '"></span> &nbsp;' + esc(x.label) + "</span>" +
        '<span class="badge ' + (x.running ? "on" : "off") + '">' + esc(x.state) + "</span></div>";
    }).join("");

    // live chart
    var canvas = $("#net-chart"), rx = [], tx = [];
    every(2000, async function () {
      try {
        var t = await Z.get("/system/throughput");
        rx.push(t.rx_bps); tx.push(t.tx_bps);
        if (rx.length > 40) { rx.shift(); tx.shift(); }
        $("#tp-label").textContent = "↓ " + fmtBytes(t.rx_bps) + "/s · ↑ " + fmtBytes(t.tx_bps) + "/s";
        drawChart(canvas, rx, tx);
      } catch (e) { /* ignore transient */ }
    });
  };

  function statCard(label, value, sub, pct) {
    return '<div class="card stat"><span class="label">' + esc(label) + '</span>' +
      '<span class="value">' + value + "</span>" +
      '<span class="muted" style="font-size:12.5px">' + esc(sub) + "</span>" +
      (pct != null ? '<div class="progress"><span style="width:' + Math.min(100, pct) + '%"></span></div>' : "") +
      "</div>";
  }
  function miniCard(label, value) {
    return '<div class="card stat"><span class="label">' + esc(label) + '</span><span class="value">' + value + "</span></div>";
  }

  function drawChart(canvas, rx, tx) {
    var dpr = window.devicePixelRatio || 1;
    var w = canvas.clientWidth, hh = canvas.clientHeight;
    canvas.width = w * dpr; canvas.height = hh * dpr;
    var ctx = canvas.getContext("2d"); ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, w, hh);
    var max = Math.max.apply(null, rx.concat(tx).concat([1]));
    function line(arr, color, fill) {
      if (arr.length < 2) return;
      ctx.beginPath();
      arr.forEach(function (v, i) {
        var x = (i / (arr.length - 1)) * w;
        var y = hh - (v / max) * (hh - 12) - 6;
        i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
      });
      ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.stroke();
      if (fill) {
        ctx.lineTo(w, hh); ctx.lineTo(0, hh); ctx.closePath();
        ctx.fillStyle = fill; ctx.fill();
      }
    }
    line(rx, "#22d3ee", "rgba(34,211,238,0.10)");
    line(tx, "#6d5efc", "rgba(109,94,252,0.10)");
  }

  // -------- Inbounds --------
  VIEWS.inbounds = async function (page) {
    setTitle("Inbounds", "Proxy listeners across Xray & sing-box");
    var list;
    try { list = await Z.get("/inbounds"); } catch (e) { page.innerHTML = '<div class="empty">' + esc(e.message) + "</div>"; return; }
    var rows = list.map(function (ib) {
      return "<tr>" +
        '<td><span class="dot ' + (ib.enabled ? "up" : "down") + '"></span></td>' +
        "<td><b>" + esc(ib.remark || ib.tag) + '</b><br><span class="mono">' + esc(ib.tag) + "</span></td>" +
        '<td><span class="badge proto">' + esc(ib.protocol) + '</span></td>' +
        '<td><span class="badge core">' + esc(ib.core) + "</span></td>" +
        "<td>" + ib.port + "</td>" +
        "<td>" + esc(ib.network) + " / " + esc(ib.security) + "</td>" +
        "<td>" + ib.client_count + "</td>" +
        "<td>↑" + fmtBytes(ib.up) + "<br>↓" + fmtBytes(ib.down) + "</td>" +
        '<td class="right">' +
          '<button class="icon-btn" title="Manage clients" data-clients="' + ib.id + '">' + IC.users + "</button>" +
          '<button class="icon-btn" title="Toggle" data-toggle="' + ib.id + '">' + IC.power + "</button>" +
          '<button class="icon-btn" title="Delete" data-del="' + ib.id + '">' + IC.trash + "</button>" +
        "</td></tr>";
    }).join("");

    page.innerHTML =
      '<div class="card pad-lg"><div class="card-head"><h3>' + list.length + ' inbound(s)</h3>' +
      '<button class="btn primary" id="add-inbound">' + IC.plus + " Add inbound</button></div>" +
      (list.length ? '<table><thead><tr><th></th><th>Name / Tag</th><th>Protocol</th><th>Core</th><th>Port</th><th>Transport</th><th>Clients</th><th>Traffic</th><th></th></tr></thead><tbody>' + rows + "</tbody></table>"
                   : '<div class="empty">No inbounds yet. Click “Add inbound”.</div>') +
      "</div>";

    $("#add-inbound").onclick = inboundModal;
    page.querySelectorAll("[data-clients]").forEach(function (b) {
      b.onclick = function () { state.inbound = list.find(function (x) { return x.id == b.dataset.clients; }); navigate("clients"); };
    });
    page.querySelectorAll("[data-toggle]").forEach(function (b) {
      b.onclick = async function () { try { await Z.post("/inbounds/" + b.dataset.toggle + "/toggle"); toast("Updated"); navigate("inbounds"); } catch (e) { toast(e.message, "err"); } };
    });
    page.querySelectorAll("[data-del]").forEach(function (b) {
      b.onclick = async function () {
        if (!(await confirmModal("Delete this inbound and all its clients?"))) return;
        try { await Z.del("/inbounds/" + b.dataset.del); toast("Deleted"); navigate("inbounds"); } catch (e) { toast(e.message, "err"); }
      };
    });
  };

  function inboundModal() {
    var protoOpts = PROTOCOLS.map(function (p) { return '<option value="' + p.key + '">' + esc(p.label) + " (" + p.core + ")</option>"; }).join("");
    var mo = modal({
      title: "Add inbound",
      size: "lg",
      body:
        '<div class="row"><div class="field"><label>Protocol</label><select id="f-proto">' + protoOpts + "</select></div>" +
        '<div class="field"><label>Remark</label><input id="f-remark" placeholder="My VLESS Reality"></div></div>' +
        '<div class="row"><div class="field"><label>Tag (unique)</label><input id="f-tag" placeholder="vless-reality"></div>' +
        '<div class="field"><label>Port</label><input id="f-port" type="number" placeholder="443"></div></div>' +
        '<div class="row"><div class="field"><label>Transport</label><select id="f-net"></select></div>' +
        '<div class="field"><label>Security</label><select id="f-sec"></select></div></div>' +
        '<div id="f-dyn"></div>',
      foot: '<button class="btn ghost" data-cancel>Cancel</button><button class="btn primary" data-save>Create</button>',
    });
    var proto = $("#f-proto", mo.root), net = $("#f-net", mo.root), sec = $("#f-sec", mo.root), dyn = $("#f-dyn", mo.root);

    function spec() { return PROTOCOLS.find(function (p) { return p.key === proto.value; }); }
    function fillSelect(sel, arr, def) { sel.innerHTML = arr.map(function (v) { return '<option ' + (v === def ? "selected" : "") + ">" + v + "</option>"; }).join(""); }
    function refresh() {
      var s = spec();
      fillSelect(net, s.transports, s.default_transport);
      fillSelect(sec, s.securities, s.default_security);
      renderDyn();
    }
    function renderDyn() {
      var n = net.value, sc = sec.value, html = "";
      if (n === "ws" || n === "httpupgrade" || n === "xhttp") {
        html += field("WebSocket / HTTP path", '<input id="d-path" value="/' + (proto.value) + '">');
        html += field("Host header (optional)", '<input id="d-host" placeholder="cdn.example.com">');
      }
      if (n === "grpc") html += field("gRPC serviceName", '<input id="d-sn" value="zeta">');
      if (sc === "tls") {
        html += field("TLS SNI / domain", '<input id="d-sni" placeholder="your.domain.com">');
        html += '<div class="row">' + field("Certificate path", '<input id="d-cert" value="/etc/zetavpn/certs/fullchain.pem">') +
                field("Private key path", '<input id="d-key" value="/etc/zetavpn/certs/privkey.pem">') + "</div>";
      }
      if (sc === "reality") html += '<p class="hint">REALITY keys, shortId and camouflage SNI are generated automatically on save (dest: www.microsoft.com).</p>';
      dyn.innerHTML = html;
    }
    function field(label, input) { return '<div class="field"><label>' + esc(label) + "</label>" + input + "</div>"; }

    proto.onchange = refresh; net.onchange = renderDyn; sec.onchange = renderDyn;
    refresh();

    $("[data-cancel]", mo.foot).onclick = mo.close;
    $("[data-save]", mo.foot).onclick = async function () {
      var s = spec();
      var payload = {
        tag: $("#f-tag", mo.root).value.trim(),
        remark: $("#f-remark", mo.root).value.trim(),
        core: s.core, protocol: s.key,
        port: parseInt($("#f-port", mo.root).value, 10),
        network: net.value, security: sec.value,
        settings: {}, stream_settings: {}, sniffing: true, auto_reality: true,
      };
      var ss = payload.stream_settings;
      var dp = $("#d-path", mo.root), dh = $("#d-host", mo.root), dsn = $("#d-sn", mo.root);
      if (dp) ss[net.value] = { path: dp.value, host: dh ? dh.value : "" };
      if (dsn) ss.grpc = { serviceName: dsn.value };
      if (sec.value === "tls") ss.tls = { serverName: val("#d-sni", mo.root), certificateFile: val("#d-cert", mo.root), keyFile: val("#d-key", mo.root) };
      if (!payload.tag || !payload.port) { toast("Tag and port are required", "err"); return; }
      try { await Z.post("/inbounds", payload); mo.close(); toast("Inbound created"); navigate("inbounds"); }
      catch (e) { toast(e.message, "err"); }
    };
  }
  function val(sel, root) { var e = $(sel, root); return e ? e.value.trim() : ""; }

  // -------- Clients (per inbound) --------
  VIEWS.clients = async function (page) {
    var ib = state.inbound;
    if (!ib) return navigate("inbounds");
    setTitle("Clients · " + (ib.remark || ib.tag), ib.protocol.toUpperCase() + " on port " + ib.port);
    var list;
    try { list = await Z.get("/inbounds/" + ib.id + "/clients"); } catch (e) { page.innerHTML = '<div class="empty">' + esc(e.message) + "</div>"; return; }
    var rows = list.map(function (c) {
      var used = (c.up || 0) + (c.down || 0);
      var quota = c.total_bytes ? fmtBytes(used) + " / " + fmtBytes(c.total_bytes) : fmtBytes(used) + " / ∞";
      return "<tr>" +
        '<td><span class="dot ' + (c.enabled ? "up" : "down") + '"></span></td>' +
        "<td><b>" + esc(c.email) + "</b></td>" +
        '<td class="mono">' + esc((c.uuid || c.password || "").slice(0, 20)) + "…</td>" +
        "<td>" + quota + "</td>" +
        "<td>" + fmtExpiry(c.expiry_time) + "</td>" +
        '<td class="right">' +
          '<button class="icon-btn" title="Share link / QR" data-link="' + c.id + '">' + IC.link + "</button>" +
          '<button class="icon-btn" title="Reset traffic" data-reset="' + c.id + '">' + IC.refresh + "</button>" +
          '<button class="icon-btn" title="Delete" data-del="' + c.id + '">' + IC.trash + "</button>" +
        "</td></tr>";
    }).join("");

    page.innerHTML =
      '<button class="btn ghost sm" id="back-inb" style="margin-bottom:14px">' + IC.back + " Back to inbounds</button>" +
      '<div class="card pad-lg"><div class="card-head"><h3>' + list.length + " client(s)</h3>" +
      '<button class="btn primary" id="add-client">' + IC.plus + " Add client</button></div>" +
      (list.length ? '<table><thead><tr><th></th><th>Email</th><th>Credential</th><th>Usage</th><th>Expiry</th><th></th></tr></thead><tbody>' + rows + "</tbody></table>"
                   : '<div class="empty">No clients yet.</div>') + "</div>";

    $("#back-inb").onclick = function () { navigate("inbounds"); };
    $("#add-client").onclick = function () { clientModal(ib); };
    page.querySelectorAll("[data-link]").forEach(function (b) { b.onclick = function () { linkModal(ib, b.dataset.link); }; });
    page.querySelectorAll("[data-reset]").forEach(function (b) {
      b.onclick = async function () { try { await Z.post("/inbounds/" + ib.id + "/clients/" + b.dataset.reset + "/reset-traffic"); toast("Traffic reset"); navigate("clients"); } catch (e) { toast(e.message, "err"); } };
    });
    page.querySelectorAll("[data-del]").forEach(function (b) {
      b.onclick = async function () { if (!(await confirmModal("Delete this client?"))) return; try { await Z.del("/inbounds/" + ib.id + "/clients/" + b.dataset.del); toast("Deleted"); navigate("clients"); } catch (e) { toast(e.message, "err"); } };
    });
  };

  function clientModal(ib) {
    var mo = modal({
      title: "Add client",
      body:
        field2("Email / label", '<input id="c-email" placeholder="user01">') +
        '<div class="row">' + field2("Data limit (GB, 0 = ∞)", '<input id="c-gb" type="number" value="0">') +
        field2("Expiry (days, 0 = never)", '<input id="c-days" type="number" value="30">') + "</div>" +
        '<div class="row">' + field2("IP limit (0 = ∞)", '<input id="c-ip" type="number" value="0">') +
        field2("Comment", '<input id="c-comment" placeholder="optional">') + "</div>",
      foot: '<button class="btn ghost" data-cancel>Cancel</button><button class="btn primary" data-save>Create</button>',
    });
    $("[data-cancel]", mo.foot).onclick = mo.close;
    $("[data-save]", mo.foot).onclick = async function () {
      var payload = {
        email: val("#c-email", mo.root), total_gb: parseFloat($("#c-gb", mo.root).value) || 0,
        expiry_days: parseInt($("#c-days", mo.root).value, 10) || 0, limit_ip: parseInt($("#c-ip", mo.root).value, 10) || 0,
        comment: val("#c-comment", mo.root),
      };
      if (!payload.email) { toast("Email is required", "err"); return; }
      try { await Z.post("/inbounds/" + ib.id + "/clients", payload); mo.close(); toast("Client created"); navigate("clients"); }
      catch (e) { toast(e.message, "err"); }
    };
  }
  function field2(label, input) { return '<div class="field"><label>' + esc(label) + "</label>" + input + "</div>"; }

  async function linkModal(ib, clientId) {
    var data;
    try { data = await Z.get("/inbounds/" + ib.id + "/clients/" + clientId + "/link"); } catch (e) { toast(e.message, "err"); return; }
    var clients = await Z.get("/inbounds/" + ib.id + "/clients");
    var c = clients.find(function (x) { return x.id == clientId; });
    var subUrl = location.origin + Z.subUrl(c.sub_id);
    var portalUrl = location.origin + Z.portalUrl(c.sub_id);
    var mo = modal({
      title: "Share · " + c.email,
      body:
        (data.qr ? '<img class="qr" src="' + data.qr + '" alt="QR">' : "") +
        '<label>Config URI</label><div class="linkbox field"><input readonly value="' + esc(data.link) + '"><button class="btn sm" data-c1>Copy</button></div>' +
        '<label>Subscription URL</label><div class="linkbox field"><input readonly value="' + esc(subUrl) + '"><button class="btn sm" data-c2>Copy</button></div>' +
        '<label>User portal</label><div class="linkbox"><input readonly value="' + esc(portalUrl) + '"><button class="btn sm" data-c3>Copy</button></div>',
    });
    $("[data-c1]", mo.body).onclick = function () { copy(data.link); };
    $("[data-c2]", mo.body).onclick = function () { copy(subUrl); };
    $("[data-c3]", mo.body).onclick = function () { copy(portalUrl); };
  }

  // -------- SSH --------
  VIEWS.ssh = async function (page) {
    setTitle("SSH Accounts", "OpenSSH · Dropbear · SSH-over-WS/SSL");
    var list;
    try { list = await Z.get("/ssh"); } catch (e) { page.innerHTML = '<div class="empty">' + esc(e.message) + "</div>"; return; }
    var rows = list.map(function (a) {
      return "<tr>" +
        '<td><span class="dot ' + (a.enabled ? "up" : "down") + '"></span></td>' +
        "<td><b>" + esc(a.username) + "</b></td>" +
        "<td>" + a.max_login + '</td><td><span class="badge on">' + a.online + " online</span></td>" +
        "<td>" + (a.expiry_date ? fmtExpiry(new Date(a.expiry_date).getTime()) : '<span class="muted">Never</span>') + "</td>" +
        '<td class="right">' +
          '<button class="icon-btn" title="' + (a.enabled ? "Lock" : "Unlock") + '" data-lock="' + a.id + '" data-en="' + a.enabled + '">' + IC.power + "</button>" +
          '<button class="icon-btn" title="Renew 30d" data-renew="' + a.id + '">' + IC.refresh + "</button>" +
          '<button class="icon-btn" title="Delete" data-del="' + a.id + '">' + IC.trash + "</button>" +
        "</td></tr>";
    }).join("");
    page.innerHTML =
      '<div class="card pad-lg"><div class="card-head"><h3>' + list.length + " account(s)</h3>" +
      '<button class="btn primary" id="add-ssh">' + IC.plus + " Add account</button></div>" +
      (list.length ? '<table><thead><tr><th></th><th>Username</th><th>Max login</th><th>Online</th><th>Expiry</th><th></th></tr></thead><tbody>' + rows + "</tbody></table>"
                   : '<div class="empty">No SSH accounts yet.</div>') + "</div>";
    $("#add-ssh").onclick = sshModal;
    page.querySelectorAll("[data-lock]").forEach(function (b) {
      b.onclick = async function () { var en = b.dataset.en === "true"; try { await Z.post("/ssh/" + b.dataset.lock + (en ? "/lock" : "/unlock")); toast("Updated"); navigate("ssh"); } catch (e) { toast(e.message, "err"); } };
    });
    page.querySelectorAll("[data-renew]").forEach(function (b) { b.onclick = async function () { try { await Z.post("/ssh/" + b.dataset.renew + "/renew?days=30"); toast("Renewed 30 days"); navigate("ssh"); } catch (e) { toast(e.message, "err"); } }; });
    page.querySelectorAll("[data-del]").forEach(function (b) { b.onclick = async function () { if (!(await confirmModal("Delete this SSH account and its system user?"))) return; try { await Z.del("/ssh/" + b.dataset.del); toast("Deleted"); navigate("ssh"); } catch (e) { toast(e.message, "err"); } }; });
  };

  function sshModal() {
    var mo = modal({
      title: "Add SSH account",
      body:
        '<div class="row">' + field2("Username", '<input id="s-user" placeholder="user01">') + field2("Password", '<input id="s-pass" placeholder="••••••">') + "</div>" +
        '<div class="row">' + field2("Max login", '<input id="s-max" type="number" value="1">') + field2("Expiry (days)", '<input id="s-days" type="number" value="30">') + "</div>" +
        field2("Comment", '<input id="s-comment" placeholder="optional">'),
      foot: '<button class="btn ghost" data-cancel>Cancel</button><button class="btn primary" data-save>Create</button>',
    });
    $("[data-cancel]", mo.foot).onclick = mo.close;
    $("[data-save]", mo.foot).onclick = async function () {
      var payload = { username: val("#s-user", mo.root), password: val("#s-pass", mo.root), max_login: parseInt($("#s-max", mo.root).value, 10) || 1, expiry_days: parseInt($("#s-days", mo.root).value, 10) || 30, comment: val("#s-comment", mo.root) };
      if (!payload.username || !payload.password) { toast("Username and password required", "err"); return; }
      try { await Z.post("/ssh", payload); mo.close(); toast("Account created"); navigate("ssh"); } catch (e) { toast(e.message, "err"); }
    };
  }

  // -------- Settings --------
  VIEWS.settings = async function (page) {
    setTitle("Settings", "Server identity, security & 2FA");
    var s;
    try { s = await Z.get("/settings"); } catch (e) { page.innerHTML = '<div class="empty">' + esc(e.message) + "</div>"; return; }
    page.innerHTML =
      '<div class="grid cols-2">' +
      '<div class="card pad-lg"><div class="card-head"><h3>Server identity</h3></div>' +
        field2("Server address (IP)", '<input id="st-addr" value="' + esc(s.server_address) + '">') +
        field2("Server domain", '<input id="st-dom" value="' + esc(s.server_domain) + '">') +
        '<p class="hint">Used when building client share links & subscriptions.</p>' +
        '<button class="btn primary" id="save-srv">Save</button></div>' +
      '<div class="card pad-lg"><div class="card-head"><h3>Change password</h3></div>' +
        field2("Current password", '<input id="pw-cur" type="password">') +
        field2("New password", '<input id="pw-new" type="password">') +
        '<button class="btn primary" id="save-pw">Update password</button></div>' +
      "</div>" +
      '<div class="card pad-lg" style="margin-top:18px"><div class="card-head"><h3>Two-factor authentication (TOTP)</h3></div>' +
        '<p class="muted" id="totp-state">' + (window.__me_totp ? "Enabled" : "Add an authenticator app for stronger login security.") + "</p>" +
        '<button class="btn" id="totp-setup">Set up 2FA</button> <button class="btn danger" id="totp-disable">Disable 2FA</button>' +
        '<div id="totp-area" style="margin-top:16px"></div></div>' +
      '<div class="card pad-lg" style="margin-top:18px"><div class="card-head"><h3>Cores</h3><button class="btn sm" id="apply-all">' + IC.refresh + " Reload all cores</button></div>" +
        '<p class="muted">Regenerate Xray & sing-box configs from the panel state and restart both cores.</p></div>';

    $("#save-srv").onclick = async function () {
      try { await Z.put("/settings", { server_address: val("#st-addr", page), server_domain: val("#st-dom", page) }); toast("Saved"); } catch (e) { toast(e.message, "err"); }
    };
    $("#save-pw").onclick = async function () {
      try { await Z.post("/auth/change-password", { current_password: $("#pw-cur", page).value, new_password: $("#pw-new", page).value }); toast("Password updated"); $("#pw-cur", page).value = ""; $("#pw-new", page).value = ""; } catch (e) { toast(e.message, "err"); }
    };
    $("#apply-all").onclick = async function () { try { await Z.post("/inbounds/apply/all"); toast("Cores reloaded"); } catch (e) { toast(e.message, "err"); } };
    $("#totp-setup").onclick = async function () {
      try {
        var d = await Z.post("/auth/totp/setup");
        $("#totp-area", page).innerHTML = '<img class="qr" src="' + d.qr + '"><p class="hint center">Scan, then enter a code to enable.</p>' +
          '<div class="linkbox" style="max-width:320px;margin:auto"><input id="totp-code" placeholder="123456"><button class="btn primary sm" id="totp-enable">Enable</button></div>';
        $("#totp-enable", page).onclick = async function () { try { await Z.post("/auth/totp/enable", { code: val("#totp-code", page) }); toast("2FA enabled"); $("#totp-area", page).innerHTML = ""; $("#totp-state", page).textContent = "Enabled"; } catch (e) { toast(e.message, "err"); } };
      } catch (e) { toast(e.message, "err"); }
    };
    $("#totp-disable").onclick = async function () {
      var code = prompt("Enter a current 2FA code to disable (or leave blank if not enabled):") || "";
      try { await Z.post("/auth/totp/disable", { code: code }); toast("2FA disabled"); $("#totp-state", page).textContent = "Disabled"; } catch (e) { toast(e.message, "err"); }
    };
  };

  function setTitle(t, sub) { $("#page-title").textContent = t; $("#page-sub").textContent = sub || ""; }

  // ---------------- wire up ----------------
  document.addEventListener("zeta:unauthorized", showLogin);
  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll(".nav-item[data-view]").forEach(function (n) {
      n.onclick = function (e) { e.preventDefault(); navigate(n.dataset.view); };
    });
    $("#logout").onclick = function () { Z.logout(); showLogin(); };
    $("#menu-toggle") && ($("#menu-toggle").onclick = function () { $(".sidebar").classList.toggle("open"); });
    boot();
  });
})();
