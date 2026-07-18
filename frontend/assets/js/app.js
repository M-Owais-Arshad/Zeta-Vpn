/* ZetaVPN — admin single-page app (framework-free).
 * ZetaVPN by Muhammad Owais · © 2026 · AGPL-3.0. */
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
  function daysLeft(ms) { return Math.ceil((ms - Date.now()) / 86400000); }
  function fmtExpiry(ms) {
    if (!ms) return '<span class="muted">Never</span>';
    var days = daysLeft(ms);
    var d = new Date(ms).toISOString().slice(0, 10);
    if (days < 0) return '<span class="t-danger">Expired</span>';
    if (days <= 3) return '<span class="t-warn">' + esc(d) + " (" + days + "d left)</span>";
    return esc(d) + ' <span class="muted">(' + days + "d)</span>";
  }
  function plural(n, word) { return n + " " + word + (n === 1 ? "" : "s"); }

  var timers = [];
  function clearTimers() { timers.forEach(clearInterval); timers = []; }
  // Timers skip ticks while the tab is hidden — no point polling a dashboard
  // nobody is looking at (saves phone battery and server CPU alike).
  function every(ms, fn) {
    function tick() { if (!document.hidden) fn(); }
    tick();
    var id = setInterval(tick, ms);
    timers.push(id);
    return id;
  }
  function everyLater(ms, fn) {
    var id = setInterval(function () { if (!document.hidden) fn(); }, ms);
    timers.push(id);
    return id;
  }

  function toast(msg, type) {
    var root = $("#toast-root");
    var isErr = type === "err";
    var t = h('<div class="toast ' + (isErr ? "err" : "ok") + '">' +
      '<span class="t-ic">' + (isErr ? "!" : "✓") + "</span><span></span></div>");
    t.lastElementChild.textContent = msg;
    root.appendChild(t);
    // Errors stay long enough to actually read; successes get out of the way.
    setTimeout(function () { t.style.opacity = "0"; setTimeout(function () { t.remove(); }, 320); }, isErr ? 7000 : 3200);
  }

  function copy(text) {
    function ok() { toast("Copied to clipboard"); }
    function fail() { toast("Couldn't auto-copy — select the text and copy manually", "err"); }
    // The Clipboard API only works in a "secure context" (HTTPS, or
    // localhost) — it's silently unavailable on plain http://<ip>/, which is
    // exactly how this panel is often reached before a domain + TLS cert is
    // set up. Fall back to the classic hidden-textarea + execCommand trick,
    // which still works over plain HTTP.
    if (window.isSecureContext && navigator.clipboard) {
      navigator.clipboard.writeText(text).then(ok, fail);
      return;
    }
    try {
      var ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.left = "-9999px";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.focus();
      ta.select();
      var success = document.execCommand("copy");
      document.body.removeChild(ta);
      success ? ok() : fail();
    } catch (e) {
      fail();
    }
  }

  // Wrap an async click handler: spinner on, double-clicks impossible.
  function busy(btn, fn) {
    if (btn.classList.contains("loading")) return Promise.resolve();
    btn.classList.add("loading");
    return Promise.resolve()
      .then(fn)
      .finally(function () { btn.classList.remove("loading"); });
  }

  var openModals = 0;
  function modal(opts) {
    var back = h('<div class="modal-backdrop"></div>');
    var size = opts.size === "lg" ? " lg" : "";
    var m = h(
      '<div class="modal' + size + '" role="dialog" aria-modal="true" aria-label="' + esc(opts.title) + '">' +
        '<div class="modal-head"><h3>' + esc(opts.title) + "</h3>" +
        (opts.locked ? "" : '<button class="icon-btn" data-x aria-label="Close">' + IC.x + "</button>") +
        "</div>" +
        '<div class="modal-body"></div>' +
        (opts.foot ? '<div class="modal-foot"></div>' : "") +
      "</div>"
    );
    $(".modal-body", m).innerHTML = opts.body || "";
    if (opts.foot) $(".modal-foot", m).innerHTML = opts.foot;
    back.appendChild(m);
    $("#modal-root").appendChild(back);
    // Keep Tab inside the dialog layer: the page behind goes inert while any
    // modal is open (counter handles confirm-on-top-of-modal stacking).
    openModals++;
    $("#app-view").inert = true;
    var prevFocus = document.activeElement;
    var closed = false;
    function close() {
      if (closed) return;
      closed = true;
      document.removeEventListener("keydown", onKey);
      back.remove();
      if (--openModals <= 0) { openModals = 0; $("#app-view").inert = false; }
      if (prevFocus && prevFocus.focus) prevFocus.focus();
    }
    function onKey(e) { if (e.key === "Escape" && !opts.locked) close(); }
    document.addEventListener("keydown", onKey);
    if (!opts.locked) {
      back.addEventListener("click", function (e) { if (e.target === back) close(); });
      $("[data-x]", m).addEventListener("click", close);
    }
    // Enter in a text field submits via the footer's primary button — the
    // modal bodies aren't <form>s, so this is the whole keyboard story.
    m.addEventListener("keydown", function (e) {
      if (e.key !== "Enter") return;
      var t = e.target.tagName;
      if (t !== "INPUT" && t !== "SELECT") return;
      var foot = $(".modal-foot", m);
      var prim = foot && foot.querySelector("[data-save],[data-yes],[data-ok],[data-close]");
      if (prim && !prim.classList.contains("loading")) { e.preventDefault(); prim.click(); }
    });
    // Focus the first form control so keyboard users land inside the dialog.
    var first = m.querySelector("input:not([readonly]):not([disabled]), select, textarea:not([readonly])");
    if (first) setTimeout(function () { first.focus(); }, 30);
    return { root: m, close: close, body: $(".modal-body", m), foot: opts.foot ? $(".modal-foot", m) : null };
  }

  // confirmModal("msg") or confirmModal({title, message, confirm, danger})
  function confirmModal(opts) {
    if (typeof opts === "string") opts = { message: opts };
    return new Promise(function (resolve) {
      var mo = modal({
        title: opts.title || "Please confirm",
        body: '<p class="confirm-msg">' + esc(opts.message) + "</p>",
        foot:
          '<button class="btn ghost" data-no>Cancel</button>' +
          '<button class="btn ' + (opts.danger === false ? "primary" : "danger") + '" data-yes>' + esc(opts.confirm || "Confirm") + "</button>",
      });
      var no = $("[data-no]", mo.foot);
      no.onclick = function () { mo.close(); resolve(false); };
      $("[data-yes]", mo.foot).onclick = function () { mo.close(); resolve(true); };
      setTimeout(function () { no.focus(); }, 30); // safe default for Enter
    });
  }

  function field(label, input, hint) {
    return '<div class="field"><label>' + esc(label) + "</label>" + input +
      (hint ? '<p class="hint">' + hint + "</p>" : "") + "</div>";
  }

  function skelTable(rows) {
    var out = "";
    for (var i = 0; i < rows; i++) out += '<div class="skel line"></div>';
    return '<div class="card pad-lg">' + out + "</div>";
  }
  function skelDash() {
    return '<div class="grid cols-4">' +
      '<div class="skel card-blk"></div><div class="skel card-blk"></div>' +
      '<div class="skel card-blk"></div><div class="skel card-blk"></div></div>';
  }
  function emptyState(icon, title, sub, cta) {
    return '<div class="empty"><span class="e-ic">' + icon + "</span>" +
      "<h4>" + esc(title) + "</h4><p>" + esc(sub) + "</p>" + (cta || "") + "</div>";
  }

  function hexRgba(hex, a) {
    var v = hex.replace("#", "");
    if (v.length === 3) v = v[0] + v[0] + v[1] + v[1] + v[2] + v[2];
    var n = parseInt(v, 16);
    return "rgba(" + ((n >> 16) & 255) + "," + ((n >> 8) & 255) + "," + (n & 255) + "," + a + ")";
  }

  // ---------------- icons ----------------
  var IC = {
    plus: '<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 5v14M5 12h14"/></svg>',
    trash: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6"/></svg>',
    edit: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/></svg>',
    link: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><path d="M10 13a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-1 1M14 11a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l1-1"/></svg>',
    power: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2v10M18 6a9 9 0 1 1-12 0"/></svg>',
    refresh: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><path d="M23 4v6h-6M1 20v-6h6"/><path d="M20.5 9A9 9 0 0 0 5.6 5.6L1 10m22 4l-4.6 4.4A9 9 0 0 1 3.5 15"/></svg>',
    users: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8M23 21v-2a4 4 0 0 0-3-3.9M16 3.1a4 4 0 0 1 0 7.8"/></svg>',
    back: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><path d="M19 12H5M12 19l-7-7 7-7"/></svg>',
    x: '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6L6 18M6 6l12 12"/></svg>',
    copy: '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>',
    info: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/></svg>',
    search: '<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg>',
    eye: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>',
    cal: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18M12 14v4M10 16h4"/></svg>',
    bolt: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M13 2L3 14h7l-1 8 11-14h-7l1-6z"/></svg>',
    layers: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"/></svg>',
    terminal: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 9l3 3-3 3M13 15h4"/></svg>',
  };

  // ---------------- state / router ----------------
  var PROTOCOLS = [];
  var state = { autoOpen: null };

  // Hash routes: "#/dashboard", "#/inbounds", "#/clients", "#/ssh",
  // "#/settings". "#/inbounds/3/clients" is kept for old links — it opens the
  // unified Clients page focused on that inbound. Refresh/back/forward work and
  // any page can be bookmarked.
  function parseRoute() {
    var hash = (location.hash || "#/dashboard").replace(/^#\/?/, "");
    var parts = hash.split("/");
    var m = hash.match(/^inbounds\/(\d+)\/clients$/);
    if (m) return { view: "clients", focusIbId: parseInt(m[1], 10) };
    var view = parts[0] || "dashboard";
    if (!VIEWS[view]) view = "dashboard";
    return { view: view, ibId: null };
  }
  function navigate(path) {
    if (location.hash === "#" + path) renderRoute(true);
    else location.hash = path;
  }
  // Monotonic render epoch: every navigation bumps it, and each async view
  // checks `stale(ep)` after its awaits — a slow earlier view can no longer
  // clobber the page (or register its timers) after the user moved on.
  var renderEpoch = 0;
  function stale(ep) { return ep !== renderEpoch; }
  var lastRouteKey = null;
  function renderRoute(force) {
    if (!Z.isAuthed()) return;
    var r = parseRoute();
    var key = r.view + ":" + (r.ibId || "");
    var fresh = force || key !== lastRouteKey;
    lastRouteKey = key;
    document.querySelectorAll(".nav-item[data-view]").forEach(function (n) {
      n.classList.toggle("active", n.dataset.view === r.view);
    });
    $(".sidebar").classList.remove("open");
    $("#scrim").classList.remove("show");
    clearTimers();
    var page = $("#page");
    if (fresh) page.innerHTML = r.view === "dashboard" ? skelDash() : skelTable(6);
    VIEWS[r.view](page, r, ++renderEpoch);
  }
  // Re-fetch + re-render current view without blanking to a skeleton (used
  // after row actions so the page doesn't flash and scroll stays put).
  function reload() {
    clearTimers();
    var r = parseRoute();
    VIEWS[r.view]($("#page"), r, ++renderEpoch);
  }
  function errState(page, msg) {
    page.innerHTML = emptyState(IC.info, "Couldn't load this page", msg,
      '<button class="btn" id="retry-btn">' + IC.refresh + " Try again</button>");
    var rb = $("#retry-btn", page);
    if (rb) rb.onclick = function () { renderRoute(true); };
  }

  // ---------------- shell ----------------
  function showLogin(notice) {
    clearTimers();
    $("#app-view").classList.add("hidden");
    $("#login-view").classList.remove("hidden");
    var err = $("#login-err");
    if (notice) err.textContent = notice;
    var totpField = $("#l-totp-field");
    $("#l-totp-toggle").onclick = function (e) {
      e.preventDefault();
      totpField.classList.remove("hidden");
      e.target.parentElement.classList.add("hidden");
      $("#l-totp").focus();
    };
    $("#login-form").onsubmit = function (e) {
      e.preventDefault();
      err.textContent = "";
      busy($("#login-btn"), async function () {
        try {
          await Z.login($("#l-user").value.trim(), $("#l-pass").value, $("#l-totp").value.trim());
          boot();
        } catch (ex) {
          err.textContent = ex.message;
          // A TOTP-related rejection means the field is needed — reveal it.
          if (/totp|2fa|code/i.test(ex.message) && totpField.classList.contains("hidden")) {
            totpField.classList.remove("hidden");
            $("#l-totp-toggle").parentElement.classList.add("hidden");
            $("#l-totp").focus();
          }
        }
      });
    };
  }

  async function boot() {
    if (!Z.isAuthed()) return showLogin();
    $("#login-view").classList.add("hidden");
    $("#app-view").classList.remove("hidden");
    try {
      var me = await Z.get("/auth/me");
      window.__me = me;
      $("#who").textContent = me.username + " · " + me.role;
    } catch (e) {
      return showLogin(/expired/i.test(e.message) ? e.message : "Can't reach the server — is the panel service running?");
    }
    try { PROTOCOLS = await Z.get("/system/protocols"); } catch (e) { PROTOCOLS = []; }
    renderRoute(true);
  }

  // ---------------- views ----------------
  var VIEWS = {};

  // -------- Dashboard --------
  VIEWS.dashboard = async function (page, route, ep) {
    setTitle("Dashboard", "Server overview & live traffic");
    var s;
    try { s = await Z.get("/system/stats"); } catch (e) { if (!stale(ep)) errState(page, e.message); return; }
    if (stale(ep)) return;

    page.innerHTML =
      '<div class="grid cols-4" id="stat-grid">' +
        statCard("cpu", "CPU", "", "", null) +
        statCard("mem", "Memory", "", "", null, '<span class="swap-line" id="ss-swap"></span>') +
        statCard("disk", "Disk", "", "", null) +
        statCard("up", "Uptime", "", "", undefined) +
      "</div>" +
      '<div class="grid cols-4">' +
        miniCard("Inbounds", '<span id="ct-inb">—</span>') +
        miniCard("Proxy clients", '<span id="ct-cli">—</span>') +
        miniCard("SSH accounts", '<span id="ct-ssh">—</span>') +
        miniCard("Proxy traffic", '<span id="ct-tr" class="traffic-mini">—</span>') +
      "</div>" +
      '<div class="grid cols-2">' +
        '<div class="card pad-lg"><div class="card-head"><h3>Network throughput</h3>' +
          '<span class="legend"><span><i class="lg-down"></i> Download</span><span><i class="lg-up"></i> Upload</span></span></div>' +
          '<canvas class="chart" id="net-chart"></canvas><p class="hint center" id="tp-label">&nbsp;</p></div>' +
        '<div class="card pad-lg"><div class="card-head"><h3>Services</h3></div><div id="svc-list"></div></div>' +
      "</div>" +
      '<div class="card pad-lg"><div class="card-head"><h3>Quick actions</h3></div><div class="btn-row">' +
        '<button class="btn primary" id="qa-inb">' + IC.plus + " Add inbound</button>" +
        '<button class="btn" id="qa-ssh">' + IC.plus + " Add SSH account</button>" +
        '<button class="btn" id="qa-apply">' + IC.refresh + " Apply config &amp; restart cores</button>" +
      "</div></div>";

    $("#qa-inb").onclick = function () { state.autoOpen = "inbound"; navigate("/inbounds"); };
    $("#qa-ssh").onclick = function () { state.autoOpen = "ssh"; navigate("/ssh"); };
    $("#qa-apply").onclick = function () { applyAllFlow($("#qa-apply")); };

    var svc = $("#svc-list");
    svc.addEventListener("click", async function (e) {
      var b = e.target.closest("[data-restart]");
      if (!b) return;
      var unit = b.dataset.restart, label = b.dataset.label;
      var okGo = await confirmModal({
        title: "Restart " + label,
        message: "Restart the " + label + " service now? Anyone connected through it will briefly drop.",
        confirm: "Restart",
      });
      if (!okGo) return;
      busy(b, async function () {
        try {
          var r = await Z.post("/system/services/" + encodeURIComponent(unit) + "/restart");
          r.ok ? toast(label + " restarted") : toast(label + ": " + (r.detail || "restart failed"), "err");
          pollStats();
        } catch (ex) { toast(ex.message, "err"); }
      });
    });

    function updateStats(st) {
      var cpu = Math.round(st.cpu_percent);
      setStat("cpu", cpu + "%", st.cpu_count + " cores · load " + st.load_avg.join(" / "), cpu);
      // RAM in the normal sub (so the bar lines up with the other cards),
      // Swap on its own line UNDER the progress bar.
      setStat("mem", st.mem.percent + "%", fmtBytes(st.mem.used) + " / " + fmtBytes(st.mem.total), st.mem.percent);
      var sw = st.swap || {};
      var ssSwap = $("#ss-swap");
      if (ssSwap) ssSwap.textContent = sw.total
        ? "Swap: " + fmtBytes(sw.used) + " / " + fmtBytes(sw.total) + " · " + sw.percent + "%"
        : "Swap: none";
      setStat("disk", st.disk.percent + "%", fmtBytes(st.disk.used) + " / " + fmtBytes(st.disk.total), st.disk.percent);
      setStat("up", fmtUptime(st.uptime_seconds), st.brand + " v" + st.version, undefined);
      $("#ct-inb").textContent = st.counts.active_inbounds + " / " + st.counts.inbounds;
      $("#ct-cli").textContent = st.counts.clients;
      $("#ct-ssh").textContent = st.counts.ssh_accounts;
      $("#ct-tr").innerHTML = "↑ " + fmtBytes(st.proxy_traffic.up) + ' <span class="muted">·</span> ↓ ' + fmtBytes(st.proxy_traffic.down);
      svc.innerHTML = st.services.map(function (x) {
        return '<div class="svc-row">' +
          '<span class="svc-name"><span class="dot ' + (x.running ? "up" : "down") + '"></span>' + esc(x.label) + "</span>" +
          "<span>" +
            '<span class="badge ' + (x.running ? "on" : "off") + '">' + esc(x.state) + "</span>" +
            '<button class="icon-btn warn" data-restart="' + esc(x.unit) + '" data-label="' + esc(x.label) + '" data-tip="Restart" aria-label="Restart ' + esc(x.label) + '">' + IC.refresh + "</button>" +
          "</span></div>";
      }).join("");
    }
    function setStat(key, value, sub, pct) {
      $("#sv-" + key).textContent = value;
      $("#ss-" + key).textContent = sub;
      var bar = $("#sp-" + key);
      if (bar && pct != null) {
        bar.firstElementChild.style.width = Math.min(100, pct) + "%";
        bar.className = "progress" + (pct >= 85 ? " crit" : pct >= 60 ? " warn" : "");
      }
    }
    updateStats(s);
    function pollStats() { Z.get("/system/stats").then(updateStats).catch(function () {}); }
    everyLater(8000, pollStats);

    // live chart — brand colors pulled from the stylesheet so a theme tweak
    // recolors the chart too.
    var css = getComputedStyle(document.documentElement);
    var cDown = (css.getPropertyValue("--green") || "#35e08c").trim();
    var cUp = (css.getPropertyValue("--red") || "#ff3b47").trim();
    var canvas = $("#net-chart");
    var rx = [], tx = [];
    for (var i = 0; i < 40; i++) { rx.push(0); tx.push(0); }
    every(2000, async function () {
      try {
        var t = await Z.get("/system/throughput");
        rx.push(t.rx_bps); tx.push(t.tx_bps);
        rx.shift(); tx.shift();
        $("#tp-label").textContent = "↓ " + fmtBytes(t.rx_bps) + "/s   ·   ↑ " + fmtBytes(t.tx_bps) + "/s";
        drawChart(canvas, rx, tx, cDown, cUp);
      } catch (e) { /* ignore transient */ }
    });
  };

  function statCard(key, label, value, sub, pct, extra) {
    return '<div class="card stat"><span class="label">' + esc(label) + "</span>" +
      '<span class="value" id="sv-' + key + '">' + value + "</span>" +
      '<span class="muted sub" id="ss-' + key + '">' + esc(sub) + "</span>" +
      (pct !== undefined ? '<div class="progress" id="sp-' + key + '"><span style="width:0%"></span></div>' : "") +
      (extra || "") +   // optional line AFTER the progress bar (e.g. Swap under Memory)
      "</div>";
  }
  function miniCard(label, valueHtml) {
    return '<div class="card stat"><span class="label">' + esc(label) + '</span><span class="value">' + valueHtml + "</span></div>";
  }

  function drawChart(canvas, rx, tx, cDown, cUp) {
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
    line(rx, cDown, hexRgba(cDown, 0.1));
    line(tx, cUp, hexRgba(cUp, 0.1));
  }

  async function applyAllFlow(btn) {
    var okGo = await confirmModal({
      title: "Apply configuration",
      message: "Regenerate the Xray & sing-box configs and restart both cores? Connected users will drop for a few seconds.",
      confirm: "Apply & restart",
      danger: false,
    });
    if (!okGo) return;
    busy(btn, async function () {
      try {
        var results = await Z.post("/inbounds/apply/all");
        var names = ["Xray", "sing-box"];
        (results || []).forEach(function (r, i) {
          r.ok ? toast((names[i] || "Core") + " reloaded")
               : toast((names[i] || "Core") + " failed: " + (r.detail || "unknown error"), "err");
        });
      } catch (e) { toast(e.message, "err"); }
    });
  }

  // -------- Inbounds --------
  VIEWS.inbounds = async function (page, route, ep) {
    setTitle("Inbounds", "Proxy listeners across Xray & sing-box");
    // Consume the one-shot "open Add" flag up front so a failed/stale fetch
    // below can't leave it set and pop the modal on a later successful render.
    var autoAdd = state.autoOpen === "inbound"; state.autoOpen = null;
    var list;
    try { list = await Z.get("/inbounds"); } catch (e) { if (!stale(ep)) errState(page, e.message); return; }
    if (stale(ep)) return;
    var rows = list.map(function (ib) {
      var portCell = (ib.internal_port
        ? ib.port + ' <span class="mono">→ lo:' + ib.internal_port + "</span>"
        : String(ib.port))
        + (ib.extra_ports && ib.extra_ports.length ? ' <span class="mono">+' + ib.extra_ports.join(",") + "</span>" : "");
      return "<tr>" +
        '<td><span class="badge ' + (ib.enabled ? "on" : "off") + '">' + (ib.enabled ? "Active" : "Off") + "</span></td>" +
        "<td><b>" + esc(ib.remark || ib.tag) + '</b><span class="sub mono">' + esc(ib.tag) + "</span></td>" +
        '<td><span class="badge proto">' + esc(ib.protocol) + "</span></td>" +
        '<td><span class="badge core">' + esc(ib.core) + "</span></td>" +
        "<td>" + portCell + "</td>" +
        "<td>" + esc(ib.network) + " / " + esc(ib.security) + "</td>" +
        '<td><button class="btn sm" data-clients="' + ib.id + '">' + IC.users + " " + plural(ib.client_count, "client") + "</button></td>" +
        '<td class="mono">↑ ' + fmtBytes(ib.up) + "<br>↓ " + fmtBytes(ib.down) + "</td>" +
        '<td class="actions">' +
          '<button class="icon-btn" data-info="' + ib.id + '" data-tip="Details" aria-label="Details">' + IC.info + "</button>" +
          '<button class="icon-btn" data-edit="' + ib.id + '" data-tip="Edit" aria-label="Edit">' + IC.edit + "</button>" +
          '<button class="icon-btn warn" data-toggle="' + ib.id + '" data-tip="' + (ib.enabled ? "Disable" : "Enable") + '" aria-label="' + (ib.enabled ? "Disable" : "Enable") + '">' + IC.power + "</button>" +
          '<button class="icon-btn danger" data-del="' + ib.id + '" data-tip="Delete" aria-label="Delete">' + IC.trash + "</button>" +
        "</td></tr>";
    }).join("");

    page.innerHTML =
      '<div class="card pad-lg"><div class="card-head"><h3>' + plural(list.length, "inbound") + "</h3>" +
      '<div class="tools">' +
        (list.length > 3 ? '<span class="search">' + IC.search + '<input id="q-inb" placeholder="Search…"></span>' : "") +
        '<button class="btn primary" id="add-inbound">' + IC.plus + " Add inbound</button>" +
      "</div></div>" +
      (list.length
        ? '<div class="table-wrap"><table class="wide"><thead><tr><th>Status</th><th>Name / Tag</th><th>Protocol</th><th>Core</th><th>Port</th><th>Transport</th><th>Clients</th><th>Traffic</th><th class="right">Actions</th></tr></thead><tbody>' + rows + "</tbody></table></div>"
        : emptyState(IC.layers, "No inbounds yet",
            "An inbound is a listening proxy (VLESS, VMess, Trojan…) your users connect to. Create one, then add clients to it.",
            '<button class="btn primary" id="empty-add">' + IC.plus + " Create your first inbound</button>")) +
      "</div>";

    function openAdd() { inboundModal(null); }
    $("#add-inbound").onclick = openAdd;
    var ea = $("#empty-add"); if (ea) ea.onclick = openAdd;
    if (autoAdd) openAdd();
    var q = $("#q-inb"); if (q) wireSearch(q, page.querySelector("tbody"));

    page.querySelectorAll("[data-clients]").forEach(function (b) {
      b.onclick = function () { state.focusInbound = b.dataset.clients; navigate("/clients"); };
    });
    page.querySelectorAll("[data-info]").forEach(function (b) {
      b.onclick = function () {
        var ib = list.find(function (x) { return x.id == b.dataset.info; });
        if (ib) inboundDetails(ib);
      };
    });
    page.querySelectorAll("[data-edit]").forEach(function (b) {
      b.onclick = function () {
        var ib = list.find(function (x) { return x.id == b.dataset.edit; });
        if (ib) inboundModal(ib);
      };
    });
    page.querySelectorAll("[data-toggle]").forEach(function (b) {
      b.onclick = function () {
        var ib = list.find(function (x) { return x.id == b.dataset.toggle; });
        busy(b, async function () {
          try {
            await Z.post("/inbounds/" + b.dataset.toggle + "/toggle");
            toast('Inbound "' + (ib ? ib.remark || ib.tag : "") + '" ' + (ib && ib.enabled ? "disabled" : "enabled"));
            reload();
          } catch (e) { toast(e.message, "err"); }
        });
      };
    });
    page.querySelectorAll("[data-del]").forEach(function (b) {
      b.onclick = async function () {
        var ib = list.find(function (x) { return x.id == b.dataset.del; });
        var name = ib ? ib.remark || ib.tag : "this inbound";
        var okGo = await confirmModal({
          title: "Delete inbound",
          message: 'Delete "' + name + '" (port ' + (ib ? ib.port : "?") + ") and its " + plural(ib ? ib.client_count : 0, "client") + "? This cannot be undone.",
          confirm: "Delete inbound",
        });
        if (!okGo) return;
        try { await Z.del("/inbounds/" + b.dataset.del); toast('Deleted "' + name + '"'); reload(); }
        catch (e) { toast(e.message, "err"); }
      };
    });
  };

  function wireSearch(input, tbody) {
    if (!tbody) return;
    input.oninput = function () {
      var q = input.value.toLowerCase();
      tbody.querySelectorAll("tr").forEach(function (tr) {
        tr.style.display = !q || tr.textContent.toLowerCase().indexOf(q) !== -1 ? "" : "none";
      });
    };
  }

  function inboundDetails(ib) {
    function kv(k, v, copyable) {
      return '<div class="kv"><span class="k">' + esc(k) + '</span><span class="v mono">' + esc(v) +
        (copyable ? ' <button class="icon-btn" data-copy="' + esc(v) + '" data-tip="Copy" aria-label="Copy">' + IC.copy + "</button>" : "") +
        "</span></div>";
    }
    var body = kv("Tag", ib.tag) +
      kv("Protocol / core", ib.protocol + " · " + ib.core) +
      kv("Public port(s)", [ib.port].concat(ib.extra_ports || []).join(", ")) +
      (ib.internal_port ? kv("Internal port (behind nginx)", "127.0.0.1:" + ib.internal_port) : "") +
      kv("Transport / security", ib.network + " / " + ib.security) +
      kv("Sniffing", ib.sniffing ? "on" : "off");
    var re = (ib.stream_settings || {}).reality;
    if (re) {
      body += '<div class="modal-sec">REALITY</div>';
      if (re.publicKey) body += kv("Public key", re.publicKey, true);
      if (re.shortIds) body += kv("Short IDs", [].concat(re.shortIds).join(", "), true);
      if (re.dest) body += kv("Dest", re.dest);
      if (re.serverNames) body += kv("Server names", [].concat(re.serverNames).join(", "));
    }
    var st = ib.settings || {};
    if (ib.protocol === "shadowsocks" && st.password) {
      body += '<div class="modal-sec">Shadowsocks</div>' +
        (st.method ? kv("Method", st.method) : "") +
        kv("Server key (PSK)", st.password, true);
    }
    var raw = JSON.stringify({ settings: ib.settings, stream_settings: ib.stream_settings }, null, 2);
    body += '<div class="modal-sec">Raw configuration</div>' +
      '<textarea class="share-block" readonly rows="8"></textarea>' +
      '<button class="btn block" data-copyraw>' + IC.copy + " Copy JSON</button>";
    var mo = modal({ title: "Inbound · " + (ib.remark || ib.tag), size: "lg", body: body });
    $("textarea.share-block", mo.body).value = raw;
    mo.body.querySelectorAll("[data-copy]").forEach(function (b) { b.onclick = function () { copy(b.dataset.copy); }; });
    $("[data-copyraw]", mo.body).onclick = function () { copy(raw); };
  }

  function inboundModal(existing) {
    if (!PROTOCOLS.length) {
      toast("Protocol list unavailable — reload the page and try again", "err");
      return;
    }
    var isEdit = !!existing;
    var protoOpts = PROTOCOLS.map(function (p) {
      var sel = existing && existing.protocol === p.key ? " selected" : "";
      return '<option value="' + p.key + '"' + sel + ">" + esc(p.label) + " (" + p.core + ")</option>";
    }).join("");
    var mo = modal({
      title: isEdit ? "Edit inbound · " + (existing.remark || existing.tag) : "Add inbound",
      size: "lg",
      body:
        '<div class="row"><div class="field"><label>Protocol</label><select id="f-proto"' + (isEdit ? " disabled" : "") + ">" + protoOpts + "</select></div>" +
        field("Display name", '<input id="f-remark" placeholder="My VLESS Reality" value="' + esc(existing ? existing.remark : "") + '">') + "</div>" +
        '<div class="row">' +
        field("Tag (internal ID)", '<input id="f-tag" placeholder="auto-filled from the name" value="' + esc(existing ? existing.tag : "") + '"' + (isEdit ? " disabled" : "") + ">",
          isEdit ? "" : "Letters, numbers and dashes — filled in for you from the name.") +
        '<div class="field"><label>Port(s)</label><input id="f-port" type="text" inputmode="numeric" placeholder="443  (or 80, 8080, 8443)"><p class="hint" id="f-port-hint"></p></div></div>' +
        '<div class="row"><div class="field"><label>Transport</label><select id="f-net"></select></div>' +
        '<div class="field"><label>Security</label><select id="f-sec"></select></div></div>' +
        '<div id="f-dyn"></div>',
      foot: '<button class="btn ghost" data-cancel>Cancel</button><button class="btn primary" data-save>' + (isEdit ? "Save changes" : "Create") + "</button>",
    });
    var proto = $("#f-proto", mo.root), net = $("#f-net", mo.root), sec = $("#f-sec", mo.root), dyn = $("#f-dyn", mo.root);
    var port = $("#f-port", mo.root), remark = $("#f-remark", mo.root), tag = $("#f-tag", mo.root);
    var portHint = $("#f-port-hint", mo.root);

    // Only auto-fill port/tag until the admin actually types their own —
    // the "input" event only fires on real user edits, never on our own
    // `.value =` assignments, so this can't confuse itself.
    var portTouched = isEdit;
    port.addEventListener("input", function () { portTouched = true; });
    var tagTouched = isEdit;
    tag.addEventListener("input", function () { tagTouched = true; });
    remark.addEventListener("input", function () {
      if (tagTouched) return;
      tag.value = remark.value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 32);
    });

    function spec() { return PROTOCOLS.find(function (p) { return p.key === proto.value; }); }
    function fillSelect(sel, arr, def) { sel.innerHTML = arr.map(function (v) { return "<option " + (v === def ? "selected" : "") + ">" + v + "</option>"; }).join(""); }
    function refreshPort() {
      var s = spec(), n = net.value;
      var isWs = s.ws_family_networks.indexOf(n) !== -1;
      port.disabled = false;
      if (!portTouched && !isEdit) port.value = isWs ? 80 : ((s.ports_by_network && s.ports_by_network[n]) || s.default_port);
      portHint.textContent = isWs
        ? "Main port 80/443 → shared via nginx, routed by the path (path required). Any other port (8080) → its own direct listener, path optional. Add MORE ports comma-separated (80, 8080, 8443) — same clients on all."
        : "One port, or several comma-separated (443, 8443, 9000) — the inbound listens on each, same clients.";
    }
    function refresh() {
      var s = spec();
      fillSelect(net, s.transports, existing ? existing.network : s.default_transport);
      fillSelect(sec, s.securities, existing ? existing.security : s.default_security);
      if (!isEdit) portTouched = false;
      if (isEdit) port.value = [existing.port].concat(existing.extra_ports || []).join(", ");
      refreshPort();
      renderDyn();
    }
    function renderDyn() {
      var n = net.value, sc = sec.value, html = "";
      var ss0 = (existing && existing.stream_settings) || {};
      if (n === "ws" || n === "httpupgrade" || n === "xhttp") {
        var p0 = (ss0[n] && ss0[n].path) || "/" + proto.value;
        var h0 = (ss0[n] && ss0[n].host) || "";
        html += field("WebSocket / HTTP path", '<input id="d-path" value="' + esc(p0) + '">',
          "On the shared :80/:443 port this routes the inbound (required, unique). On a dedicated port (e.g. 8080) it's optional — set any path or leave it blank.");
        html += field("Host header (optional)", '<input id="d-host" placeholder="cdn.example.com" value="' + esc(h0) + '">');
      }
      if (n === "grpc") {
        var sn0 = (ss0.grpc && ss0.grpc.serviceName) || "zeta";
        html += field("gRPC serviceName", '<input id="d-sn" value="' + esc(sn0) + '">');
      }
      if (sc === "tls") {
        var tls0 = ss0.tls || {};
        html += field("TLS SNI / domain", '<input id="d-sni" placeholder="your.domain.com" value="' + esc(tls0.serverName || "") + '">');
        html += '<div class="row">' +
          field("Certificate path", '<input id="d-cert" value="' + esc(tls0.certificateFile || "/etc/zetavpn/certs/fullchain.pem") + '">') +
          field("Private key path", '<input id="d-key" value="' + esc(tls0.keyFile || "/etc/zetavpn/certs/privkey.pem") + '">') + "</div>";
      }
      if (sc === "reality") {
        html += '<p class="hint">' + (isEdit && ss0.reality
          ? "Existing REALITY keys are kept as-is."
          : "REALITY keys, shortId and camouflage SNI are generated automatically on save (dest: www.apple.com).") + "</p>";
      }
      dyn.innerHTML = html;
    }

    proto.onchange = refresh;
    net.onchange = function () { refreshPort(); renderDyn(); };
    sec.onchange = renderDyn;
    refresh();

    $("[data-cancel]", mo.foot).onclick = mo.close;
    $("[data-save]", mo.foot).onclick = function (ev) {
      busy(ev.currentTarget, async function () {
        var s = spec();
        // Overlay the transport fields onto a copy of the existing
        // stream_settings so server-seeded blocks (REALITY keys, SS PSK)
        // survive an edit instead of being wiped by a fresh object.
        var ss = existing ? JSON.parse(JSON.stringify(existing.stream_settings || {})) : {};
        var dp = $("#d-path", mo.root), dh = $("#d-host", mo.root), dsn = $("#d-sn", mo.root);
        if (dp) ss[net.value] = { path: dp.value, host: dh ? dh.value : "" };
        if (dsn) ss.grpc = { serviceName: dsn.value };
        if (sec.value === "tls") ss.tls = { serverName: val("#d-sni", mo.root), certificateFile: val("#d-cert", mo.root), keyFile: val("#d-key", mo.root) };
        try {
          // Switching an existing inbound to REALITY: PATCH doesn't run the
          // server-side auto-seed (that's create-only), so mint the keypair
          // here — mirrors _seed_reality's shape in api/inbounds.py.
          if (isEdit && sec.value === "reality" && !ss.reality) {
            var keys = await Z.post("/settings/reality-keypair");
            ss.reality = {
              privateKey: keys.privateKey,
              publicKey: keys.publicKey,
              shortIds: [keys.shortId],
              dest: "www.apple.com:443",
              serverNames: ["www.apple.com"],
              fingerprint: "chrome",
            };
          }
          if (isEdit) {
            var patch = {
              remark: remark.value.trim(),
              network: net.value, security: sec.value,
              stream_settings: ss,
            };
            var ppE = parsePorts(port.value);
            if (ppE.port) { patch.port = ppE.port; patch.extra_ports = ppE.extra; }
            await Z.patch("/inbounds/" + existing.id, patch);
            mo.close();
            toast('Inbound "' + (patch.remark || existing.tag) + '" updated');
            reload();
          } else {
            var ppC = parsePorts(port.value);
            var payload = {
              tag: tag.value.trim(),
              remark: remark.value.trim(),
              core: s.core, protocol: s.key,
              port: ppC.port,
              extra_ports: ppC.extra,
              network: net.value, security: sec.value,
              settings: {}, stream_settings: ss, sniffing: true, auto_reality: true,
            };
            if (!payload.tag) { toast("Give the inbound a name or tag first", "err"); tag.focus(); return; }
            if (!payload.port) { toast("Port is required", "err"); port.focus(); return; }
            var created = await Z.post("/inbounds", payload);
            mo.close();
            toast('Inbound "' + (payload.remark || payload.tag) + '" created — now add a client');
            navigate("/inbounds/" + created.id + "/clients");
          }
        } catch (e) { toast(e.message, "err"); }
      });
    };
  }
  function val(sel, root) { var e = $(sel, root); return e ? e.value.trim() : ""; }
  // "80, 8080 8443" -> { port: 80, extra: [8080, 8443] } (de-duped, in range).
  function parsePorts(raw) {
    var nums = String(raw || "").split(/[\s,]+/).map(function (x) { return parseInt(x, 10); })
      .filter(function (n) { return n >= 1 && n <= 65535; });
    var seen = {}, out = [];
    nums.forEach(function (n) { if (!seen[n]) { seen[n] = 1; out.push(n); } });
    return { port: out[0], extra: out.slice(1) };
  }

  // -------- Boost & Tuning (elite gaming tuning + Telegram proxy) --------
  VIEWS.tuning = async function (page, route, ep) {
    setTitle("Boost & Tuning", "Elite gaming network tuning & Telegram proxy");
    var tune = { active: false }, tg = { active: false }, bot = { active: false };
    try { tune = await Z.get("/system/tuning"); } catch (e) { /* degrade if endpoint missing */ }
    try { tg = await Z.get("/system/tgproxy"); } catch (e) { /* degrade */ }
    try { bot = await Z.get("/system/bot"); } catch (e) { /* degrade */ }
    if (stale(ep)) return;

    function tgLinks(t) {
      if (t.active && t.needs_address) return '<p class="hint" style="color:var(--warn)">Set your server address/domain in Settings to generate the proxy link.</p>';
      if (!t.active || !t.tme_url) return "";
      function fld(label, v) {
        return v ? '<div class="field slim"><label>' + label + '</label><div class="linkbox"><input readonly value="' + esc(v) + '"><button class="btn sm" data-copy="' + esc(v) + '">' + IC.copy + "</button></div></div>" : "";
      }
      return '<div class="modal-sec">Give this to your users</div>' +
        '<div class="linkbox"><input readonly value="' + esc(t.tme_url) + '"><button class="btn sm" data-copy="' + esc(t.tme_url) + '">' + IC.copy + " Copy</button></div>" +
        '<a class="btn success block" href="' + esc(t.tme_url) + '" target="_blank" rel="noopener">Open in Telegram</a>' +
        '<div class="modal-sec">Register with @MTProxybot (or add manually)</div>' +
        fld("Server", t.host) + fld("Port", t.port) +
        fld("Secret (32-hex — for @MTProxybot)", t.secret_hex) +
        '<p class="hint">@MTProxybot wants the 32-hex secret above. Apps that support FakeTLS can use the full link.</p>';
    }

    page.innerHTML =
      '<div class="grid cols-2">' +
      '<div class="card pad-lg" id="tune-card"><div class="card-head"><h3>⚡ Elite Gaming Tuning</h3>' +
        '<span class="badge ' + (tune.active ? "on" : "neutral") + '" id="tune-badge">' + (tune.active ? "Active" : "Off") + "</span></div>" +
        '<p class="hint">One-tap low-latency tuning for mobile gamers: BBR + fair queueing (kills bufferbloat), bigger TCP/UDP buffers, TCP Fast Open, MSS clamp for 4G/5G, conntrack timeouts, the performance CPU governor and data-plane priority. Turning it off restores the server to its exact previous state.</p>' +
        '<div class="btn-row" id="tune-actions"></div></div>' +
      '<div class="card pad-lg" id="tg-card"><div class="card-head"><h3>✈️ Telegram Proxy</h3>' +
        '<span class="badge ' + (tg.active ? "on" : "neutral") + '" id="tg-badge">' + (tg.active ? "Active" : "Off") + "</span></div>" +
        '<p class="hint">Build a Telegram <b>MTProto</b> proxy (mtg, FakeTLS-camouflaged) on this server with one tap — great for users on networks that throttle Telegram. Runs on a dedicated port so it never clashes with your proxy inbounds.</p>' +
        '<div class="btn-row" id="tg-actions"></div><div id="tg-links">' + tgLinks(tg) + "</div></div>" +
      "</div>" +
      '<div class="card pad-lg" id="bot-card"><div class="card-head"><h3>🤖 Telegram Bot</h3>' +
        '<span class="badge ' + (bot.active ? "on" : "neutral") + '" id="bot-badge">' + (bot.active ? "Running" : "Off") + "</span></div>" +
        '<p class="hint">A Telegram bot that lets your users self-serve — free trial, buy a plan, and get their config — while you approve payments and create accounts right from Telegram. Everything it does syncs with this dashboard. Set the <b>bot token</b> and your <b>admin chat ID</b> under <b>Settings → Telegram</b> first.</p>' +
        '<div class="btn-row" id="bot-actions"></div><p class="hint" id="bot-note"></p></div>';

    function renderTune() {
      $("#tune-badge", page).className = "badge " + (tune.active ? "on" : "neutral");
      $("#tune-badge", page).textContent = tune.active ? "Active" : "Off";
      $("#tune-actions", page).innerHTML = tune.active
        ? '<button class="btn danger" id="tune-off">' + IC.power + " Turn off tuning</button>"
        : '<button class="btn primary" id="tune-on">' + IC.bolt + " Start elite tuning</button>";
      var on = $("#tune-on", page), off = $("#tune-off", page);
      if (on) on.onclick = function (e) { busy(e.currentTarget, async function () {
        try { var r = await Z.post("/system/tuning/start"); tune.active = r.active !== false;
          r.ok === false ? toast(r.detail || "Failed", "err") : toast("Elite tuning is ON — server boosted"); renderTune();
        } catch (ex) { toast(ex.message, "err"); } }); };
      if (off) off.onclick = async function (e) {
        // Capture the button BEFORE awaiting: once the handler suspends on the
        // await, event dispatch ends and e.currentTarget resets to null.
        var btn = e.currentTarget;
        if (!(await confirmModal({ title: "Turn off tuning", message: "Revert all gaming tuning and return the server to its exact previous state?", confirm: "Turn off" }))) return;
        busy(btn, async function () {
          try { await Z.post("/system/tuning/stop"); tune.active = false; toast("Tuning off — server restored"); renderTune(); }
          catch (ex) { toast(ex.message, "err"); } }); };
    }
    function renderTg() {
      $("#tg-badge", page).className = "badge " + (tg.active ? "on" : "neutral");
      $("#tg-badge", page).textContent = tg.active ? "Active" : "Off";
      $("#tg-actions", page).innerHTML = tg.active
        ? '<button class="btn danger" id="tg-off">' + IC.power + " Turn off proxy</button>"
        : '<button class="btn primary" id="tg-on">' + IC.bolt + " Build Telegram proxy</button>";
      $("#tg-links", page).innerHTML = tgLinks(tg);
      $("#tg-links", page).querySelectorAll("[data-copy]").forEach(function (b) { b.onclick = function () { copy(b.dataset.copy); }; });
      var on = $("#tg-on", page), off = $("#tg-off", page);
      if (on) on.onclick = function (e) { busy(e.currentTarget, async function () {
        try { var r = await Z.post("/system/tgproxy/start"); if (r.ok === false) { toast(r.detail || "Failed", "err"); return; }
          tg = Object.assign({ active: true }, r); toast("Telegram proxy is live"); renderTg();
        } catch (ex) { toast(ex.message, "err"); } }); };
      if (off) off.onclick = async function (e) {
        // Capture the button BEFORE awaiting (see the tuning-off handler): after
        // the await, event dispatch has ended and e.currentTarget is null.
        var btn = e.currentTarget;
        if (!(await confirmModal({ title: "Turn off Telegram proxy", message: "Stop and remove the MTProto proxy? The current link stops working.", confirm: "Turn off" }))) return;
        busy(btn, async function () {
          try { await Z.post("/system/tgproxy/stop"); tg = { active: false }; toast("Telegram proxy removed"); renderTg(); }
          catch (ex) { toast(ex.message, "err"); } }); };
    }
    function renderBot() {
      $("#bot-badge", page).className = "badge " + (bot.active ? "on" : "neutral");
      $("#bot-badge", page).textContent = bot.active ? "Running" : "Off";
      $("#bot-note", page).innerHTML = bot.configured
        ? (bot.admins ? "" : '<span style="color:var(--warn)">No admin chat ID set — add it in Settings so you get payment alerts.</span>')
        : '<span style="color:var(--warn)">No bot token set. Add it under Settings → Telegram, then start the bot.</span>';
      $("#bot-actions", page).innerHTML = bot.active
        ? '<button class="btn danger" id="bot-off">' + IC.power + " Stop bot</button>"
        : '<button class="btn primary" id="bot-on"' + (bot.configured ? "" : " disabled") + ">" + IC.bolt + " Start bot</button>";
      var on = $("#bot-on", page), off = $("#bot-off", page);
      if (on) on.onclick = function (e) { busy(e.currentTarget, async function () {
        try { var r = await Z.post("/system/bot/start"); if (r.ok === false) { toast(r.detail || "Failed", "err"); return; }
          bot.active = true; toast("Telegram bot started"); renderBot();
        } catch (ex) { toast(ex.message, "err"); } }); };
      if (off) off.onclick = function (e) { busy(e.currentTarget, async function () {
        try { await Z.post("/system/bot/stop"); bot.active = false; toast("Telegram bot stopped"); renderBot(); }
        catch (ex) { toast(ex.message, "err"); } }); };
    }
    renderTune();
    renderTg();
    renderBot();
  };

  // -------- Clients --------
  function clientRow(c, ib, showInbound) {
    var used = (c.up || 0) + (c.down || 0);
    var pct = c.total_bytes ? Math.min(100, Math.round((used / c.total_bytes) * 100)) : null;
    var quota = fmtBytes(used) + " / " + (c.total_bytes ? fmtBytes(c.total_bytes) : "∞");
    var ips = c.online_ips || [];
    var online = ips.length
      ? '<button class="badge on" data-ips="' + c.id + '" data-ib="' + ib.id + '"><span class="dot up"></span> ' + ips.length + (c.limit_ip ? " / " + c.limit_ip : "") + "</button>"
      : '<span class="badge neutral"><span class="dot idle"></span> offline</span>';
    if (c.ip_limit_exceeded) online += ' <span class="badge warn" data-tip="More devices than the IP limit — blocked until it drops">IP limit</span>';
    var cred = c.uuid || c.password || "";
    return "<tr>" +
      '<td><span class="badge ' + (c.enabled ? "on" : "off") + '">' + (c.enabled ? "Active" : "Off") + "</span></td>" +
      "<td><b>" + esc(c.email) + "</b>" + (c.comment ? '<span class="sub">' + esc(c.comment) + "</span>" : "") + "</td>" +
      (showInbound ? '<td><span class="badge proto">' + esc(ib.protocol) + "</span> " + esc(ib.remark || ib.tag) + "</td>" : "") +
      '<td><button class="copy-cell" data-copycred="' + esc(cred) + '" data-tip="Copy credential" aria-label="Copy credential">' + esc(cred.slice(0, 10)) + "… " + IC.copy + "</button></td>" +
      "<td>" + online + "</td>" +
      "<td>" + quota + (pct != null ? '<div class="progress mini' + (pct >= 85 ? " crit" : pct >= 60 ? " warn" : "") + '"><span style="width:' + pct + '%"></span></div>' : "") + "</td>" +
      "<td>" + fmtExpiry(c.expiry_time) + "</td>" +
      '<td class="actions">' +
        '<button class="icon-btn success" data-link="' + c.id + '" data-ib="' + ib.id + '" data-tip="Share link / QR" aria-label="Share">' + IC.link + "</button>" +
        '<button class="icon-btn" data-edit="' + c.id + '" data-ib="' + ib.id + '" data-tip="Edit" aria-label="Edit">' + IC.edit + "</button>" +
        '<button class="icon-btn warn" data-toggle="' + c.id + '" data-ib="' + ib.id + '" data-en="' + c.enabled + '" data-tip="' + (c.enabled ? "Disable" : "Enable") + '" aria-label="' + (c.enabled ? "Disable" : "Enable") + '">' + IC.power + "</button>" +
        '<button class="icon-btn" data-reset="' + c.id + '" data-ib="' + ib.id + '" data-tip="Reset traffic" aria-label="Reset traffic">' + IC.refresh + "</button>" +
        '<button class="icon-btn danger" data-del="' + c.id + '" data-ib="' + ib.id + '" data-tip="Delete" aria-label="Delete">' + IC.trash + "</button>" +
      "</td></tr>";
  }

  function clientHead(showInbound) {
    return "<thead><tr><th>Status</th><th>User</th>" + (showInbound ? "<th>Inbound</th>" : "") +
      '<th>Credential</th><th>Online</th><th>Usage</th><th>Expiry</th><th class="right">Actions</th></tr></thead>';
  }

  function wireClientActions(page, items, refreshFn) {
    // items: [{c, ib}]
    function find(b, key) {
      return items.find(function (x) { return x.c.id == b.dataset[key] && x.ib.id == b.dataset.ib; });
    }
    page.querySelectorAll("[data-copycred]").forEach(function (b) {
      b.onclick = function () { copy(b.dataset.copycred); };
    });
    page.querySelectorAll("[data-link]").forEach(function (b) {
      b.onclick = function () { var it = find(b, "link"); if (it) linkModal(it.ib, it.c.id); };
    });
    page.querySelectorAll("[data-edit]").forEach(function (b) {
      b.onclick = function () { var it = find(b, "edit"); if (it) clientModal(it.ib, it.c, refreshFn); };
    });
    page.querySelectorAll("[data-toggle]").forEach(function (b) {
      b.onclick = function () {
        var it = find(b, "toggle"), en = b.dataset.en === "true";
        busy(b, async function () {
          try {
            await Z.patch("/inbounds/" + b.dataset.ib + "/clients/" + b.dataset.toggle, { enabled: !en });
            toast('"' + (it ? it.c.email : "client") + '" ' + (en ? "disabled" : "enabled"));
            refreshFn();
          } catch (e) { toast(e.message, "err"); }
        });
      };
    });
    page.querySelectorAll("[data-reset]").forEach(function (b) {
      b.onclick = async function () {
        var it = find(b, "reset");
        var okGo = await confirmModal({
          title: "Reset traffic",
          message: 'Reset the traffic counters for "' + (it ? it.c.email : "this client") + '"? Their used quota goes back to zero.',
          confirm: "Reset traffic",
          danger: false,
        });
        if (!okGo) return;
        try {
          await Z.post("/inbounds/" + b.dataset.ib + "/clients/" + b.dataset.reset + "/reset-traffic");
          toast('Traffic reset for "' + (it ? it.c.email : "client") + '"');
          refreshFn();
        } catch (e) { toast(e.message, "err"); }
      };
    });
    page.querySelectorAll("[data-del]").forEach(function (b) {
      b.onclick = async function () {
        var it = find(b, "del");
        var okGo = await confirmModal({
          title: "Delete client",
          message: 'Delete "' + (it ? it.c.email : "this client") + '"? Their config stops working immediately. This cannot be undone.',
          confirm: "Delete client",
        });
        if (!okGo) return;
        try {
          await Z.del("/inbounds/" + b.dataset.ib + "/clients/" + b.dataset.del);
          toast('Deleted "' + (it ? it.c.email : "client") + '"');
          refreshFn();
        } catch (e) { toast(e.message, "err"); }
      };
    });
    page.querySelectorAll("[data-ips]").forEach(function (b) {
      b.onclick = function () {
        var it = find(b, "ips");
        var ips = (it && it.c.online_ips) || [];
        modal({
          title: "Active IPs · " + (it ? it.c.email : ""),
          body: (ips.length
            ? '<ul class="ip-list">' + ips.map(function (ip) { return '<li class="mono">' + esc(ip) + "</li>"; }).join("") + "</ul>"
            : '<p class="hint">No recent activity.</p>') +
            '<p class="hint">' + plural(ips.length, "device") + " active in the last 2 minutes" +
            (it && it.c.limit_ip ? " · limit: " + it.c.limit_ip : " · no IP limit set") + "</p>",
        });
      };
    });
  }

  // Per-inbound clients
  // The single Clients page: every inbound is its own collapsible block (one
  // block = one protocol/inbound), each with its own client table and Add
  // button. Reached from the sidebar, or from an inbound's "N clients" button
  // (which focuses+expands just that block). No separate nested view.
  VIEWS.clients = async function (page, route, ep) {
    setTitle("Clients", "Proxy users, grouped by inbound");
    // Consume the focus target BEFORE the awaits so a failed/stale load can't
    // leak it to a later navigation (which would then collapse every other
    // group, making clients look missing).
    var focus = route.focusIbId || state.focusInbound || null;
    state.focusInbound = null;
    var ibs;
    try { ibs = await Z.get("/inbounds"); } catch (e) { if (!stale(ep)) errState(page, e.message); return; }
    var lists = await Promise.all(ibs.map(function (ib) {
      return Z.get("/inbounds/" + ib.id + "/clients").catch(function () { return []; });
    }));
    if (stale(ep)) return;

    var allItems = [];
    var total = 0;

    var blocks = ibs.map(function (ib, i) {
      var cls = lists[i];
      total += cls.length;
      cls.forEach(function (c) { allItems.push({ c: c, ib: ib }); });
      // With a focus target only that block opens; otherwise all open.
      var open = focus ? String(ib.id) === String(focus) : true;
      var body = cls.length
        ? '<div class="table-wrap"><table class="wide">' + clientHead(false) + "<tbody>" +
            cls.map(function (c) { return clientRow(c, ib, false); }).join("") + "</tbody></table></div>"
        : '<p class="hint cg-empty">No clients on this inbound yet — click <b>Add</b>.</p>';
      return '<details class="cli-group"' + (open ? " open" : "") + ' data-ibblock="' + ib.id + '">' +
        "<summary><span class=\"cg-left\">" +
          '<span class="badge ' + (ib.enabled ? "on" : "off") + '">' + (ib.enabled ? "Active" : "Off") + "</span>" +
          "<b>" + esc(ib.remark || ib.tag) + "</b>" +
          '<span class="badge proto">' + esc(ib.protocol) + "</span>" +
          '<span class="badge core">' + esc(ib.core) + "</span>" +
          '<span class="muted mono">:' + ib.port + "</span>" +
        "</span><span class=\"cg-right\">" +
          '<span class="muted">' + plural(cls.length, "client") + "</span>" +
          '<button class="btn sm primary" data-addcli="' + ib.id + '">' + IC.plus + " Add</button>" +
          '<button class="icon-btn danger" data-delib="' + ib.id + '" data-tip="Delete inbound" aria-label="Delete inbound">' + IC.trash + "</button>" +
        "</span></summary>" + body + "</details>";
    }).join("");

    page.innerHTML =
      '<div class="card-head clients-head"><h3>' + plural(total, "client") + " across " + plural(ibs.length, "inbound") + "</h3>" +
      (total > 3 ? '<span class="search">' + IC.search + '<input id="q-cli" placeholder="Search all clients…"></span>' : "") +
      "</div>" +
      (ibs.length
        ? blocks
        : emptyState(IC.users, "No inbounds yet",
            "Clients belong to an inbound — create your first inbound, then add clients to it.",
            '<a class="btn primary" href="#/inbounds">Go to inbounds</a>'));

    page.querySelectorAll("[data-addcli]").forEach(function (b) {
      b.onclick = function (e) {
        e.preventDefault(); e.stopPropagation(); // it's inside <summary>
        var ib = ibs.find(function (x) { return String(x.id) === b.dataset.addcli; });
        if (ib) clientModal(ib, null, reload);
      };
    });
    page.querySelectorAll("[data-delib]").forEach(function (b) {
      b.onclick = async function (e) {
        e.preventDefault(); e.stopPropagation(); // it's inside <summary> — don't toggle
        var ib = ibs.find(function (x) { return String(x.id) === b.dataset.delib; });
        var name = ib ? (ib.remark || ib.tag) : "this inbound";
        var okGo = await confirmModal({
          title: "Delete inbound",
          message: 'Delete "' + name + '" (port ' + (ib ? ib.port : "?") + ") and ALL its clients? This cannot be undone.",
          confirm: "Delete inbound",
        });
        if (!okGo) return;
        try { await Z.del("/inbounds/" + b.dataset.delib); toast('Deleted "' + name + '"'); reload(); }
        catch (err) { toast(err.message, "err"); }
      };
    });
    var q = $("#q-cli");
    if (q) q.oninput = function () {
      var query = q.value.toLowerCase();
      page.querySelectorAll("[data-ibblock]").forEach(function (d) {
        var any = false;
        d.querySelectorAll("tbody tr").forEach(function (tr) {
          var hit = !query || tr.textContent.toLowerCase().indexOf(query) !== -1;
          tr.style.display = hit ? "" : "none";
          if (hit) any = true;
        });
        d.open = query ? any : true; // expand matches while searching; restore all when cleared
      });
    };
    wireClientActions(page, allItems, reload);
  };

  function clientModal(ib, existing, refreshFn) {
    var isEdit = !!existing;
    var gb0 = existing && existing.total_bytes ? (existing.total_bytes / 1073741824) : 0;
    gb0 = Math.round(gb0 * 100) / 100;
    // Which credential kind this inbound's protocol uses decides where a
    // custom value must be sent — body.uuid is ignored for password-credential
    // protocols (trojan/shadowsocks/…) and would create a non-working client.
    var pspec = PROTOCOLS.find(function (p) { return p.key === ib.protocol; });
    var credIsPassword = !!(pspec && pspec.credential === "password");
    var isExpired = isEdit && existing.expiry_time && daysLeft(existing.expiry_time) < 0;
    // Edit mode round-trips expiry as relative days, which is lossy (the
    // backend re-anchors from "now") — so the field starts untouched and is
    // only PATCHed if the admin actually types in it. Same for the quota.
    var daysAttrs = isEdit
      ? (isExpired ? ' value="" placeholder="Expired — enter days to renew"' : ' value="' + Math.max(0, daysLeft(existing.expiry_time || 0)) + '"')
      : ' value="30"';
    var mo = modal({
      title: isEdit ? "Edit client · " + existing.email : "Add client",
      body:
        field("Email / label", '<input id="c-email" placeholder="user01" value="' + esc(existing ? existing.email : "") + '">') +
        '<div class="row">' +
        field("Data limit (GB)", '<input id="c-gb" type="number" min="0" step="0.5" value="' + gb0 + '">',
          "0 = unlimited" + (isEdit ? " · leave unchanged to keep as-is" : "")) +
        field("Expiry (days from today)", '<input id="c-days" type="number" min="0"' + daysAttrs + ">",
          "0 = never expires" + (isEdit ? " · leave unchanged to keep the current expiry" : "")) + "</div>" +
        '<div class="row">' +
        field("Device / IP limit", '<input id="c-ip" type="number" min="0" value="' + (existing ? existing.limit_ip : 0) + '">', "Max devices online at once · 0 = unlimited") +
        field("Comment", '<input id="c-comment" placeholder="optional" value="' + esc(existing ? existing.comment || "" : "") + '">') + "</div>" +
        (isEdit ? "" :
          '<details class="adv"><summary>Advanced</summary>' +
          field("Custom " + (credIsPassword ? "password" : "UUID"), '<div class="linkbox"><input id="c-cred" placeholder="leave empty to auto-generate"><button class="btn sm" type="button" data-gencred>Generate</button></div>') +
          field("Subscription ID", '<input id="c-subid" placeholder="leave empty to auto-generate">', "Give two clients the same ID to bundle them into one subscription link.") +
          "</details>"),
      foot: '<button class="btn ghost" data-cancel>Cancel</button><button class="btn primary" data-save>' + (isEdit ? "Save changes" : "Create") + "</button>",
    });
    var gbDirty = false, daysDirty = false;
    $("#c-gb", mo.root).addEventListener("input", function () { gbDirty = true; });
    $("#c-days", mo.root).addEventListener("input", function () { daysDirty = true; });
    var gen = $("[data-gencred]", mo.root);
    if (gen) gen.onclick = function () {
      busy(gen, async function () {
        try {
          $("#c-cred", mo.root).value = credIsPassword ? genPassword() : (await Z.get("/settings/new-uuid")).uuid;
        } catch (e) { toast(e.message, "err"); }
      });
    };
    $("[data-cancel]", mo.foot).onclick = mo.close;
    $("[data-save]", mo.foot).onclick = function (ev) {
      busy(ev.currentTarget, async function () {
        var payload = {
          email: val("#c-email", mo.root),
          limit_ip: parseInt($("#c-ip", mo.root).value, 10) || 0,
          comment: val("#c-comment", mo.root),
        };
        if (!payload.email) { toast("Email / label is required", "err"); $("#c-email", mo.root).focus(); return; }
        try {
          if (isEdit) {
            if (gbDirty) payload.total_gb = parseFloat($("#c-gb", mo.root).value) || 0;
            if (daysDirty) payload.expiry_days = parseInt($("#c-days", mo.root).value, 10) || 0;
            await Z.patch("/inbounds/" + ib.id + "/clients/" + existing.id, payload);
            mo.close();
            toast('"' + payload.email + '" updated');
          } else {
            payload.total_gb = parseFloat($("#c-gb", mo.root).value) || 0;
            payload.expiry_days = parseInt($("#c-days", mo.root).value, 10) || 0;
            var cred = val("#c-cred", mo.root), sid = val("#c-subid", mo.root);
            if (cred) payload[credIsPassword ? "password" : "uuid"] = cred;
            if (sid) payload.sub_id = sid;
            await Z.post("/inbounds/" + ib.id + "/clients", payload);
            mo.close();
            toast('Client "' + payload.email + '" created');
          }
          (refreshFn || reload)();
        } catch (e) { toast(e.message, "err"); }
      });
    };
  }

  async function linkModal(ib, clientId) {
    var data;
    try { data = await Z.get("/inbounds/" + ib.id + "/clients/" + clientId + "/link"); } catch (e) { toast(e.message, "err"); return; }
    var subUrl = location.origin + Z.subUrl(data.sub_id);
    var portalUrl = location.origin + Z.portalUrl(data.sub_id);
    function linkRow(label, value, hint) {
      return "<label>" + esc(label) + '</label><div class="linkbox"><input readonly value="' + esc(value) + '"><button class="btn sm" data-copy="' + esc(value) + '">' + IC.copy + " Copy</button></div>" +
        '<p class="hint">' + esc(hint) + "</p>";
    }
    var mo = modal({
      title: "Share · " + data.email,
      body:
        (data.qr ? '<img class="qr" src="' + data.qr + '" alt="QR code">' : "") +
        linkRow("Config URI", data.link, "Paste into v2rayNG, NekoBox, Streisand or any client app — or scan the QR.") +
        linkRow("Subscription URL", subUrl, "Auto-updating config list — best choice to give your user.") +
        linkRow("User portal", portalUrl, "Web page where your user can check their own usage and expiry."),
    });
    mo.body.querySelectorAll("[data-copy]").forEach(function (b) {
      b.onclick = function () { copy(b.dataset.copy); };
    });
  }

  // -------- SSH --------
  // Standard SSH-stack ports (see scripts/install_ssh_stack.sh /
  // install_nginx.sh's defaults — not currently exposed via an API, so
  // mirrored here; a custom install that changed them would need this list
  // updated to match). SSH-over-WebSocket is reachable two ways: directly
  // on its own port, and via nginx on :80 at the WS path (the
  // "bug host"/CDN-friendly route tunnelling apps expect) — both are
  // real, simultaneously-working routes to the exact same backend.
  // Each row picks its own host: the raw-SSH ports (22/109/143/445/8880) need
  // the RAW IP — a Cloudflare-proxied domain silently drops them (CF only
  // passes HTTP/HTTPS). Only the nginx :80/:443 WS routes ride the domain/CDN.
  var SSH_PORTS = [
    { label: "OpenSSH (direct — start here)", value: function (ip, dom, port) { return ip + ":" + (port || 22); } },
    { label: "Dropbear (main)", value: function (ip, dom) { return ip + ":109"; } },
    { label: "Dropbear (alt)", value: function (ip, dom) { return ip + ":143"; } },
    { label: "SSH-over-SSL (stunnel)", value: function (ip, dom) { return ip + ":445"; } },
    { label: "SSH-over-WebSocket (direct)", value: function (ip, dom) { return ip + ":8880"; } },
    { label: "UDPGW (UDP/gaming — set in the tunnel app)", value: function (ip, dom) { return "127.0.0.1:7300"; } },
    { label: "SSH-WS via CDN (:80, needs a WS payload)", value: function (ip, dom) { return dom + ":80  (path: /zeta-ws, or any)"; } },
    { label: "SSH-WS via CDN over TLS (:443)", value: function (ip, dom) { return dom + ":443  (path: /zeta-ws)"; } },
  ];

  function fmtExpiryPlain(isoDate) {
    if (!isoDate) return "Never";
    var ms = new Date(isoDate).getTime();
    var days = daysLeft(ms);
    return new Date(ms).toISOString().slice(0, 10) + " (" + (days < 0 ? "expired" : days + "d") + ")";
  }

  // The ready-to-forward text block (like the "SGP/BLR SSH ACCOUNT" style
  // messages resellers send their users) — one button copies everything at
  // once instead of five separate fields.
  function buildShareBlock(acc, pw, ip, dom, brand, sshPort) {
    sshPort = sshPort || 22;
    return [
      "⚡ " + (brand || "ZetaVPN") + " SSH ACCOUNT",
      "Host/IP  : " + ip,
      "Username : " + acc.username,
      "Password : " + (pw || "(unknown — recreate the account)"),
      "Expiry   : " + fmtExpiryPlain(acc.expiry_date),
      "Max login: " + acc.max_login,
      "",
      "— Direct (use the IP — simplest, works on most networks) —",
      "OpenSSH  : " + ip + ":" + sshPort,
      "Dropbear : " + ip + ":109, " + ip + ":143",
      "SSH-SSL  : " + ip + ":445",
      "SSH-WS   : " + ip + ":8880  (direct WebSocket)",
      "UDPGW    : 127.0.0.1:7300  (set in the tunnel app for UDP/gaming)",
      "",
      "— Via CDN / domain (needs a WebSocket payload) —",
      "SSH-WS   : " + dom + ":80",
      "SSH-WS/TLS: " + dom + ":443  (TLS)",
      "Payload  : GET /zeta-ws HTTP/1.1[crlf]Host: " + dom + "[crlf]Upgrade: websocket[crlf][crlf]",
      "",
      "Tip: raw-SSH ports (22/109/143/445/8880) need the IP — a Cloudflare",
      "domain only passes :80/:443. Easiest = OpenSSH " + ip + ":" + sshPort + " (direct).",
    ].join("\n");
  }

  async function sshInfoModal(acc, password) {
    var ip = "your-server-ip", dom = "your-server-ip", brand = "ZetaVPN", sshPort = 22;
    try {
      var s = await Z.get("/settings");
      // IP for raw-SSH ports (Cloudflare drops those); domain for the WS routes.
      ip = s.server_address || s.server_domain || ip;
      dom = s.server_domain || s.server_address || dom;
      brand = s.brand || brand;
      // Real OpenSSH port (providers sometimes move it off :22, e.g. 22022).
      sshPort = s.ssh_port || 22;
    } catch (e) { /* best effort */ }
    // Real password: whatever was just typed at creation, else the stored one
    // the API now returns (single-owner dashboard — safe to show/copy anytime).
    var pw = password || acc.password || "";
    var portRows = SSH_PORTS.map(function (p) {
      var v = p.value(ip, dom, sshPort);
      return '<div class="field slim"><label>' + esc(p.label) + "</label>" +
        '<div class="linkbox"><input readonly value="' + esc(v) + '"><button class="btn sm" data-copy="' + esc(v) + '">' + IC.copy + " Copy</button></div></div>";
    }).join("");
    var block = buildShareBlock(acc, pw, ip, dom, brand, sshPort);
    var pwField = pw
      ? '<div class="linkbox"><span class="input-wrap"><input id="ssh-pw" type="password" readonly value="' + esc(pw) + '">' +
          '<button type="button" class="in-btn icon-btn" data-eye data-tip="Show / hide" aria-label="Show password">' + IC.eye + "</button></span>" +
          '<button class="btn sm" data-copy="' + esc(pw) + '">' + IC.copy + " Copy</button></div>"
      : '<div class="linkbox"><input readonly value="•••••• (unknown — created before passwords were stored)" disabled></div>';
    var mo = modal({
      title: "SSH connection info · " + acc.username,
      size: "lg",
      body:
        '<div class="row">' +
          field("Username", '<div class="linkbox"><input readonly value="' + esc(acc.username) + '"><button class="btn sm" data-copy="' + esc(acc.username) + '">' + IC.copy + " Copy</button></div>") +
          field("Password", pwField) +
        "</div>" +
        '<div class="modal-sec">Server address · port (any one, depending on the app)</div>' +
        portRows +
        '<p class="hint">Ports shown are the default ZetaVPN install ports.</p>' +
        '<div class="modal-sec">Copy-paste ready block — send this to your user</div>' +
        '<textarea class="share-block" readonly rows="10"></textarea>' +
        '<button class="btn primary block" data-copyblock>' + IC.copy + " Copy full block</button>",
      foot: '<button class="btn primary" data-close>Done</button>',
    });
    $("textarea.share-block", mo.body).value = block;
    $("[data-close]", mo.foot).onclick = mo.close;
    var eye = $("[data-eye]", mo.root);
    if (eye) eye.onclick = function () {
      var f = $("#ssh-pw", mo.root);
      f.type = f.type === "password" ? "text" : "password";
    };
    mo.root.querySelectorAll("[data-copy]").forEach(function (b) {
      b.onclick = function () { copy(b.dataset.copy); };
    });
    $("[data-copyblock]", mo.root).onclick = function () { copy(block); };
  }

  VIEWS.ssh = async function (page, route, ep) {
    setTitle("SSH Accounts", "OpenSSH · Dropbear · SSH-over-WS/SSL");
    var list;
    try { list = await Z.get("/ssh"); } catch (e) { if (!stale(ep)) errState(page, e.message); return; }
    if (stale(ep)) return;
    var rows = list.map(function (a) {
      var online = a.online > 0
        ? '<button class="badge on" data-ssh-ips="' + a.id + '"><span class="dot up"></span> ' + a.online + " online</button>"
        : '<span class="badge neutral"><span class="dot idle"></span> 0 online</span>';
      return "<tr>" +
        '<td><span class="badge ' + (a.enabled ? "on" : "off") + '">' + (a.enabled ? "Active" : "Locked") + "</span></td>" +
        "<td><b>" + esc(a.username) + "</b>" + (a.comment ? '<span class="sub">' + esc(a.comment) + "</span>" : "") + "</td>" +
        "<td>" + a.max_login + "</td>" +
        "<td>" + online + "</td>" +
        '<td class="mono">' + fmtBytes(a.used_bytes || 0) + "</td>" +
        "<td>" + (a.expiry_date ? fmtExpiry(new Date(a.expiry_date).getTime()) : '<span class="muted">Never</span>') + "</td>" +
        '<td class="actions">' +
          '<button class="icon-btn success" data-info="' + a.id + '" data-tip="Connection info" aria-label="Connection info">' + IC.link + "</button>" +
          '<button class="icon-btn" data-renew="' + a.id + '" data-tip="Renew" aria-label="Renew">' + IC.cal + "</button>" +
          '<button class="icon-btn" data-resettr="' + a.id + '" data-tip="Reset traffic" aria-label="Reset traffic">' + IC.refresh + "</button>" +
          '<button class="icon-btn warn" data-lock="' + a.id + '" data-en="' + a.enabled + '" data-tip="' + (a.enabled ? "Lock" : "Unlock") + '" aria-label="' + (a.enabled ? "Lock" : "Unlock") + '">' + IC.power + "</button>" +
          '<button class="icon-btn danger" data-del="' + a.id + '" data-tip="Delete" aria-label="Delete">' + IC.trash + "</button>" +
        "</td></tr>";
    }).join("");
    page.innerHTML =
      '<div class="card pad-lg"><div class="card-head"><h3>' + plural(list.length, "account") + "</h3>" +
      '<div class="tools">' +
        (list.length > 3 ? '<span class="search">' + IC.search + '<input id="q-ssh" placeholder="Search…"></span>' : "") +
        '<button class="btn primary" id="add-ssh">' + IC.plus + " Add account</button>" +
      "</div></div>" +
      (list.length
        ? '<div class="table-wrap"><table>' +
          '<thead><tr><th>Status</th><th>Username</th><th>Max login</th><th>Online</th><th>Traffic</th><th>Expiry</th><th class="right">Actions</th></tr></thead><tbody>' + rows + "</tbody></table></div>"
        : emptyState(IC.terminal, "No SSH accounts yet",
            "SSH accounts work with HTTP Injector / HTTP Custom-style tunnelling apps over OpenSSH, Dropbear, SSL and WebSocket.",
            '<button class="btn primary" id="empty-add">' + IC.plus + " Create your first account</button>")) +
      "</div>";

    function openAdd() { sshModal(); }
    $("#add-ssh").onclick = openAdd;
    var ea = $("#empty-add"); if (ea) ea.onclick = openAdd;
    if (state.autoOpen === "ssh") { state.autoOpen = null; openAdd(); }
    var q = $("#q-ssh"); if (q) wireSearch(q, page.querySelector("tbody"));

    page.querySelectorAll("[data-info]").forEach(function (b) {
      b.onclick = function () {
        var a = list.find(function (x) { return x.id == b.dataset.info; });
        if (a) sshInfoModal(a, null);
      };
    });
    page.querySelectorAll("[data-renew]").forEach(function (b) {
      b.onclick = function () {
        var a = list.find(function (x) { return x.id == b.dataset.renew; });
        if (a) renewModal(a);
      };
    });
    page.querySelectorAll("[data-resettr]").forEach(function (b) {
      b.onclick = function () {
        var a = list.find(function (x) { return x.id == b.dataset.resettr; });
        busy(b, async function () {
          try {
            await Z.post("/ssh/" + b.dataset.resettr + "/reset-traffic");
            toast('Traffic reset for "' + (a ? a.username : "account") + '"');
            reload();
          } catch (e) { toast(e.message, "err"); }
        });
      };
    });
    page.querySelectorAll("[data-lock]").forEach(function (b) {
      b.onclick = function () {
        var a = list.find(function (x) { return x.id == b.dataset.lock; });
        var en = b.dataset.en === "true";
        busy(b, async function () {
          try {
            await Z.post("/ssh/" + b.dataset.lock + (en ? "/lock" : "/unlock"));
            toast('"' + (a ? a.username : "account") + '" ' + (en ? "locked" : "unlocked"));
            reload();
          } catch (e) { toast(e.message, "err"); }
        });
      };
    });
    page.querySelectorAll("[data-del]").forEach(function (b) {
      b.onclick = async function () {
        var a = list.find(function (x) { return x.id == b.dataset.del; });
        var okGo = await confirmModal({
          title: "Delete SSH account",
          message: 'Delete "' + (a ? a.username : "this account") + '" and its system user? Active sessions are killed. This cannot be undone.',
          confirm: "Delete account",
        });
        if (!okGo) return;
        try { await Z.del("/ssh/" + b.dataset.del); toast('Deleted "' + (a ? a.username : "account") + '"'); reload(); }
        catch (e) { toast(e.message, "err"); }
      };
    });
    page.querySelectorAll("[data-ssh-ips]").forEach(function (b) {
      b.onclick = function () {
        var a = list.find(function (x) { return x.id == b.getAttribute("data-ssh-ips"); });
        var ips = (a && a.online_ips) || [];
        modal({
          title: "Active IPs · " + (a ? a.username : ""),
          body: (ips.length
            ? '<ul class="ip-list">' + ips.map(function (ip) { return '<li class="mono">' + esc(ip) + "</li>"; }).join("") + "</ul>"
            : '<p class="hint">Connected, but the source IP isn\'t visible for SSL/WebSocket sessions (only direct OpenSSH/Dropbear expose it).</p>') +
            '<p class="hint">' + plural(a ? a.online : 0, "session") + " online" +
            (ips.length < (a ? a.online : 0) ? " · " + (a.online - ips.length) + " via SSL/WS (IP hidden)" : "") + "</p>",
        });
      };
    });
  };

  function renewModal(acc) {
    var mo = modal({
      title: "Renew · " + acc.username,
      body:
        '<p class="hint">Current expiry: <b>' + esc(fmtExpiryPlain(acc.expiry_date)) + "</b>. Renewal extends from the expiry date (or today, if already expired).</p>" +
        '<div class="tabs" id="rn-chips">' +
          [7, 30, 60, 90].map(function (d) {
            return '<button class="tab' + (d === 30 ? " active" : "") + '" data-days="' + d + '">' + d + " days</button>";
          }).join("") +
        "</div>" +
        field("Days to add", '<input id="rn-days" type="number" min="1" value="30">') +
        '<p class="hint" id="rn-preview"></p>',
      foot: '<button class="btn ghost" data-cancel>Cancel</button><button class="btn primary" data-save>Renew</button>',
    });
    var input = $("#rn-days", mo.root), preview = $("#rn-preview", mo.root);
    function refreshPreview() {
      var d = parseInt(input.value, 10) || 0;
      var base = acc.expiry_date ? Math.max(Date.now(), new Date(acc.expiry_date).getTime()) : Date.now();
      preview.innerHTML = d > 0
        ? "New expiry: <b>" + new Date(base + d * 86400000).toISOString().slice(0, 10) + "</b>"
        : "";
    }
    $("#rn-chips", mo.root).addEventListener("click", function (e) {
      var b = e.target.closest(".tab");
      if (!b) return;
      mo.root.querySelectorAll("#rn-chips .tab").forEach(function (t) { t.classList.toggle("active", t === b); });
      input.value = b.dataset.days;
      refreshPreview();
    });
    input.oninput = refreshPreview;
    refreshPreview();
    $("[data-cancel]", mo.foot).onclick = mo.close;
    $("[data-save]", mo.foot).onclick = function (ev) {
      busy(ev.currentTarget, async function () {
        var d = parseInt(input.value, 10);
        if (!d || d < 1) { toast("Enter how many days to add", "err"); input.focus(); return; }
        try {
          var updated = await Z.post("/ssh/" + acc.id + "/renew?days=" + d);
          mo.close();
          toast('"' + acc.username + '" renewed until ' + (updated && updated.expiry_date ? new Date(updated.expiry_date).toISOString().slice(0, 10) : "+" + d + "d"));
          reload();
        } catch (e) { toast(e.message, "err"); }
      });
    };
  }

  function genPassword() {
    var chars = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789";
    var out = "", buf = new Uint32Array(12);
    (window.crypto || window.msCrypto).getRandomValues(buf);
    for (var i = 0; i < buf.length; i++) out += chars[buf[i] % chars.length];
    return out;
  }

  function sshModal() {
    var mo = modal({
      title: "Add SSH account",
      body:
        '<div class="row">' +
        field("Username", '<input id="s-user" placeholder="user01" autocomplete="off">') +
        field("Password", '<div class="linkbox"><span class="input-wrap"><input id="s-pass" type="password" placeholder="••••••" autocomplete="new-password"><button type="button" class="in-btn icon-btn" data-eye data-tip="Show / hide" aria-label="Show password">' + IC.eye + '</button></span><button class="btn sm" type="button" data-genpass>Generate</button></div>') +
        "</div>" +
        '<div class="row">' +
        field("Max login", '<input id="s-max" type="number" min="0" value="1">', "How many devices may connect at once") +
        field("Expiry (days)", '<input id="s-days" type="number" min="0" value="30">', "0 = never expires") + "</div>" +
        field("Comment", '<input id="s-comment" placeholder="optional">'),
      foot: '<button class="btn ghost" data-cancel>Cancel</button><button class="btn primary" data-save>Create</button>',
    });
    var pass = $("#s-pass", mo.root);
    $("[data-eye]", mo.root).onclick = function () {
      pass.type = pass.type === "password" ? "text" : "password";
    };
    $("[data-genpass]", mo.root).onclick = function () {
      pass.value = genPassword();
      pass.type = "text";
    };
    $("[data-cancel]", mo.foot).onclick = mo.close;
    $("[data-save]", mo.foot).onclick = function (ev) {
      busy(ev.currentTarget, async function () {
        // isNaN (not ||) so an explicit 0 survives: 0 max_login = no session
        // cap, 0 expiry days = never expires — both backend-supported.
        var maxL = parseInt($("#s-max", mo.root).value, 10);
        var expD = parseInt($("#s-days", mo.root).value, 10);
        var payload = {
          username: val("#s-user", mo.root),
          password: pass.value,
          max_login: isNaN(maxL) ? 1 : maxL,
          expiry_days: isNaN(expD) ? 30 : expD,
          comment: val("#s-comment", mo.root),
        };
        if (!payload.username || !payload.password) { toast("Username and password are required", "err"); return; }
        try {
          var acc = await Z.post("/ssh", payload);
          mo.close();
          toast('Account "' + payload.username + '" created');
          await sshInfoModal(acc, payload.password);
          reload();
        } catch (e) { toast(e.message, "err"); }
      });
    };
  }

  // -------- Settings --------
  VIEWS.settings = async function (page, route, ep) {
    setTitle("Settings", "Server identity, security & services");
    var s, cores = null;
    try { s = await Z.get("/settings"); } catch (e) { if (!stale(ep)) errState(page, e.message); return; }
    try { cores = await Z.get("/system/cores"); } catch (e) { /* card degrades gracefully */ }
    if (stale(ep)) return;
    var me = window.__me || {};
    var totpOn = !!me.totp_enabled;

    function coreBadge(name, st) {
      var ok = st && st.running;
      return '<div class="svc-row"><span class="svc-name"><span class="dot ' + (ok ? "up" : "down") + '"></span>' + esc(name) + "</span>" +
        '<span class="badge ' + (ok ? "on" : "off") + '">' + esc(st ? st.active : "unknown") + "</span></div>";
    }

    page.innerHTML =
      '<div class="grid cols-2">' +

      '<div class="card pad-lg"><div class="card-head"><h3>Server identity</h3></div>' +
        field("Brand name", '<input id="st-brand" value="' + esc(s.brand || "") + '">', "Shown in client apps, subscriptions and share messages.") +
        field("Server address (IP)", '<input id="st-addr" value="' + esc(s.server_address) + '">') +
        field("Server domain", '<input id="st-dom" value="' + esc(s.server_domain) + '">', "Used instead of the IP in share links when set.") +
        field("Subscription domain", '<input id="st-sub" value="' + esc(s.sub_domain || "") + '">', "Optional separate domain for subscription links.") +
        '<button class="btn primary" id="save-srv">Save identity</button></div>' +

      '<div class="card pad-lg"><div class="card-head"><h3>Security</h3></div>' +
        (me.last_login ? '<p class="hint">Last login: ' + esc(new Date(me.last_login).toLocaleString()) + "</p>" : "") +
        field("Current password", '<input id="pw-cur" type="password" autocomplete="current-password">') +
        field("New password", '<input id="pw-new" type="password" autocomplete="new-password">') +
        '<button class="btn primary" id="save-pw">Update password</button>' +
        '<div class="modal-sec">Two-factor authentication</div>' +
        '<p class="hint" id="totp-state">' + (totpOn ? "2FA is enabled — codes are required at sign-in." : "Add an authenticator app for stronger login security.") + "</p>" +
        (totpOn
          ? '<button class="btn danger" id="totp-disable">Disable 2FA</button>'
          : '<button class="btn" id="totp-setup">Set up 2FA</button>') +
        '<div id="totp-area"></div></div>' +

      "</div>" +

      '<div class="grid cols-2">' +
      '<div class="card pad-lg"><div class="card-head"><h3>Proxy cores</h3>' +
        '<button class="btn" id="apply-all">' + IC.refresh + " Apply config &amp; restart</button></div>" +
        '<div id="cores-list">' +
        (cores ? coreBadge("Xray", cores.xray) + coreBadge("sing-box", cores.singbox) : '<p class="hint">Core status unavailable.</p>') +
        "</div>" +
        '<p class="hint">Regenerates both configs from the panel state and restarts the cores — connected users drop for a few seconds.</p></div>' +

      '<div class="card pad-lg"><div class="card-head"><h3>Telegram (optional)</h3></div>' +
        field("Bot token", '<input id="st-tgtoken" value="' + esc(s.telegram_bot_token || "") + '" placeholder="123456:ABC-…">') +
        field("Admin chat ID", '<input id="st-tgadmin" value="' + esc(s.telegram_admin_id || "") + '" placeholder="123456789">') +
        '<button class="btn primary" id="save-tg">Save Telegram</button>' +
        '<div class="modal-sec">Panel</div>' +
        '<p class="hint">Internal port: <b>' + esc(s.panel_port) + "</b> (behind nginx) · Base path: <b>" + esc(s.base_path || "/") + "</b></p></div>" +
      "</div>" +

      '<div class="card pad-lg"><div class="card-head"><h3>SSH server message</h3></div>' +
        '<p class="hint">A custom banner every SSH user sees the moment they connect (before login) — like the "message from server" other tunnel panels show. Applies to OpenSSH, Dropbear, SSL and WebSocket, and updates instantly for new connections. Leave blank for none.</p>' +
        field("Banner text", '<textarea id="st-sshbanner" rows="4" placeholder="Welcome to MyVPN&#10;Telegram: @myvpn">' + esc(s.ssh_banner || "") + "</textarea>") +
        '<button class="btn primary" id="save-sshbanner">Save banner</button></div>' +

      '<div class="card pad-lg"><div class="card-head"><h3>Panel updates</h3>' +
        '<span class="badge neutral">v' + esc(s.version || "?") + "</span></div>" +
        '<p class="hint">Fetch the latest ZetaVPN from GitHub and apply it in one click — your accounts, settings and secrets are kept, and live tunnels stay connected (only the panel reloads). The page refreshes automatically when the new version is up.</p>' +
        '<button class="btn primary" id="do-update">' + IC.refresh + " Update to latest</button>" +
        '<label class="hint" style="display:flex;gap:8px;align-items:center;margin-top:10px;cursor:pointer"><input type="checkbox" id="upd-full"> Also re-apply firewall / SSH stack / nginx (brief reconnect)</label></div>';

    $("#save-srv").onclick = function (ev) {
      busy(ev.currentTarget, async function () {
        try {
          await Z.put("/settings", {
            brand: val("#st-brand", page),
            server_address: val("#st-addr", page),
            server_domain: val("#st-dom", page),
            sub_domain: val("#st-sub", page),
          });
          toast("Server identity saved");
        } catch (e) { toast(e.message, "err"); }
      });
    };
    $("#save-tg").onclick = function (ev) {
      busy(ev.currentTarget, async function () {
        try {
          await Z.put("/settings", {
            telegram_bot_token: val("#st-tgtoken", page),
            telegram_admin_id: val("#st-tgadmin", page),
          });
          toast("Telegram settings saved");
        } catch (e) { toast(e.message, "err"); }
      });
    };
    $("#save-sshbanner").onclick = function (ev) {
      busy(ev.currentTarget, async function () {
        try {
          await Z.put("/settings", { ssh_banner: val("#st-sshbanner", page) });
          toast("SSH banner saved — new connections see it immediately");
        } catch (e) { toast(e.message, "err"); }
      });
    };
    var updBtn = $("#do-update", page);
    if (updBtn) updBtn.onclick = function () {
      busy(updBtn, async function () {
        var full = $("#upd-full", page) && $("#upd-full", page).checked;
        var ok = await confirmModal({
          title: "Update panel",
          message: "Fetch the latest version from GitHub and apply it? Your accounts, settings and secrets are kept" +
            (full ? ". FULL: firewall / SSH / nginx are re-applied too (users briefly reconnect)." : " and live tunnels stay connected (only the panel reloads).") +
            " The page reloads automatically when it's done.",
          confirm: "Update now",
        });
        if (!ok) return;
        try {
          var r = await Z.post("/system/update" + (full ? "?full=true" : ""));
          if (r && r.ok === false) { toast(r.detail || "Update couldn't start", "err"); return; }
          toast("Updating… the panel will reload in ~25s");
          updBtn.textContent = "Updating… reloading soon";
          updBtn.disabled = true;
          setTimeout(function () { location.reload(); }, 25000);
        } catch (e) { toast(e.message, "err"); }
      });
    };
    $("#save-pw").onclick = function (ev) {
      busy(ev.currentTarget, async function () {
        try {
          await Z.post("/auth/change-password", { current_password: $("#pw-cur", page).value, new_password: $("#pw-new", page).value });
          // The backend bumps token_version on password change, so the
          // current JWT is already dead — go to login cleanly instead of
          // letting the next request bounce with "Session expired".
          Z.logout();
          showLogin("Password changed — sign in with your new password.");
        } catch (e) { toast(e.message, "err"); }
      });
    };
    $("#apply-all").onclick = function () { applyAllFlow($("#apply-all")); };

    var setupBtn = $("#totp-setup");
    if (setupBtn) setupBtn.onclick = function (ev) {
      busy(ev.currentTarget, async function () {
        try {
          var d = await Z.post("/auth/totp/setup");
          $("#totp-area", page).innerHTML =
            '<img class="qr" src="' + d.qr + '" alt="TOTP QR">' +
            '<p class="hint center">Scan with Google Authenticator / Aegis, then enter the 6-digit code.</p>' +
            '<div class="linkbox totp-box"><input id="totp-code" placeholder="123456" inputmode="numeric"><button class="btn primary" id="totp-enable">Enable</button></div>';
          $("#totp-enable", page).onclick = function (ev2) {
            busy(ev2.currentTarget, async function () {
              try {
                await Z.post("/auth/totp/enable", { code: val("#totp-code", page) });
                toast("2FA enabled");
                if (window.__me) window.__me.totp_enabled = true;
                reload();
              } catch (e) { toast(e.message, "err"); }
            });
          };
        } catch (e) { toast(e.message, "err"); }
      });
    };
    var disableBtn = $("#totp-disable");
    if (disableBtn) disableBtn.onclick = function () {
      var mo = modal({
        title: "Disable 2FA",
        body: field("Current 2FA code", '<input id="td-code" placeholder="123456" inputmode="numeric">',
          "Enter a valid code from your authenticator app to confirm it's you."),
        foot: '<button class="btn ghost" data-cancel>Cancel</button><button class="btn danger" data-ok>Disable 2FA</button>',
      });
      $("[data-cancel]", mo.foot).onclick = mo.close;
      $("[data-ok]", mo.foot).onclick = function (ev) {
        busy(ev.currentTarget, async function () {
          try {
            await Z.post("/auth/totp/disable", { code: val("#td-code", mo.root) });
            mo.close();
            toast("2FA disabled");
            if (window.__me) window.__me.totp_enabled = false;
            reload();
          } catch (e) { toast(e.message, "err"); }
        });
      };
    };
  };

  function setTitle(t, sub) { $("#page-title").textContent = t; $("#page-sub").textContent = sub || ""; }

  // ---------------- wire up ----------------
  document.addEventListener("zeta:unauthorized", function () { showLogin("Session expired — please sign in again"); });
  window.addEventListener("hashchange", function () { renderRoute(false); });
  document.addEventListener("DOMContentLoaded", function () {
    $("#logout").onclick = function (e) { e.preventDefault(); Z.logout(); showLogin(); };
    // Clicking the nav item for the route you're already on fires no
    // hashchange — force a re-render so it acts as refresh/retry (and
    // closes the mobile drawer via renderRoute).
    document.querySelectorAll(".nav-item[data-view]").forEach(function (n) {
      n.addEventListener("click", function () {
        if (location.hash === n.getAttribute("href")) renderRoute(true);
      });
    });
    var mt = $("#menu-toggle"), scrim = $("#scrim");
    if (mt) mt.onclick = function () {
      var open = $(".sidebar").classList.toggle("open");
      scrim.classList.toggle("show", open);
    };
    if (scrim) scrim.onclick = function () {
      $(".sidebar").classList.remove("open");
      scrim.classList.remove("show");
    };
    boot();
  });
})();
