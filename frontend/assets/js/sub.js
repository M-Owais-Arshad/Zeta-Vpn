// Public subscription portal (sub.html). Externalised from an inline <script>
// so the panel can ship a strict Content-Security-Policy (script-src 'self').
(function () {
  var Z = window.Zeta;
  var root = document.getElementById("portal");
  function esc(s){return String(s==null?"":s).replace(/[&<>"']/g,function(c){return{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c];});}
  function fmtBytes(n){n=Number(n)||0;if(n<1024)return n+" B";var u=["KB","MB","GB","TB"],i=-1;do{n/=1024;i++;}while(n>=1024&&i<u.length-1);return n.toFixed(1)+" "+u[i];}
  function toast(m){var r=document.getElementById("toast-root");var t=document.createElement("div");t.className="toast ok";t.textContent=m;r.appendChild(t);setTimeout(function(){t.remove();},2600);}
  function copy(t){
    function ok(){toast("Copied");}
    function fail(){toast("Select the text and copy manually");}
    // Clipboard API only works in a secure context (HTTPS/localhost); on a
    // plain-http portal (common before TLS is set up) it's unavailable, so fall
    // back to the hidden-textarea + execCommand trick (mirrors app.js copy()).
    if (window.isSecureContext && navigator.clipboard) { navigator.clipboard.writeText(t).then(ok, fail); return; }
    try {
      var ta=document.createElement("textarea");
      ta.value=t; ta.style.position="fixed"; ta.style.left="-9999px"; ta.style.opacity="0";
      document.body.appendChild(ta); ta.focus(); ta.select();
      var s=document.execCommand("copy"); document.body.removeChild(ta);
      s?ok():fail();
    } catch(e){ fail(); }
  }

  var id = new URLSearchParams(location.search).get("id");
  if (!id) { root.innerHTML = '<div class="card"><div class="empty">Missing subscription id.</div></div>'; return; }
  var subUrl = location.origin + Z.subUrl(id);

  fetch(Z.subUrl(id) + "/info").then(function (r) {
    if (!r.ok) throw new Error("Subscription not found");
    return r.json();
  }).then(function (d) {
    var used = fmtBytes(d.used);
    var total = d.total ? fmtBytes(d.total) : "∞";
    var expiry = d.expiry ? new Date(d.expiry).toISOString().slice(0,10) : "Never";
    var head =
      '<div class="card pad-lg" style="margin-bottom:16px">' +
        '<div class="card-head"><h3>' + esc(d.email) + "</h3><span class=\"badge core\">" + esc(d.brand) + "</span></div>" +
        '<p style="margin:0 0 12px">Usage: <b>' + used + " / " + total + "</b> &nbsp;·&nbsp; Expires: <b>" + esc(expiry) + "</b></p>" +
        '<label>Subscription URL (import into your client)</label>' +
        '<div class="linkbox"><input readonly value="' + esc(subUrl) + '"><button class="btn primary sm" id="cpsub">Copy</button></div>' +
      "</div>";
    var cards = d.configs.map(function (c, i) {
      return '<div class="card pad-lg" style="margin-bottom:14px">' +
        '<div class="card-head"><h3>' + esc(c.remark) + '</h3><span class="badge proto">' + esc(c.protocol) + "</span></div>" +
        (c.qr ? '<img class="qr" src="' + c.qr + '">' : "") +
        '<div class="linkbox"><input readonly value="' + esc(c.link) + '"><button class="btn sm" data-cp="' + i + '">Copy</button></div>' +
      "</div>";
    }).join("");
    root.innerHTML = head + cards;
    document.getElementById("cpsub").onclick = function () { copy(subUrl); };
    root.querySelectorAll("[data-cp]").forEach(function (b) { b.onclick = function () { copy(d.configs[b.dataset.cp].link); }; });
  }).catch(function (e) {
    root.innerHTML = '<div class="card"><div class="empty">' + esc(e.message) + "</div></div>";
  });
})();
