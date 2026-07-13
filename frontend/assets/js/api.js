/* ZetaVPN — API client. Framework-free fetch wrapper with token handling. */
(function () {
  "use strict";

  function detectBase() {
    // Match the raw (relative) attribute values as authored in the HTML — CSS
    // attribute selectors compare against getAttribute(), not the resolved URL,
    // so the substring must not start with a leading slash.
    var el =
      document.querySelector('link[href*="assets/css/zeta.css"]') ||
      document.querySelector('script[src*="assets/js/"]');
    if (el) {
      var href = el.href || el.src;
      try {
        return new URL(href).pathname.replace(/\/assets\/.*$/, "");
      } catch (e) {
        /* ignore */
      }
    }
    return "";
  }

  var BASE = detectBase();
  var API = BASE + "/api";
  var TOKEN_KEY = "zeta_token";

  function getToken() {
    return localStorage.getItem(TOKEN_KEY) || "";
  }
  function setToken(t) {
    if (t) localStorage.setItem(TOKEN_KEY, t);
    else localStorage.removeItem(TOKEN_KEY);
  }

  async function request(method, path, body) {
    var headers = { "Content-Type": "application/json" };
    var token = getToken();
    if (token) headers["Authorization"] = "Bearer " + token;

    var res = await fetch(API + path, {
      method: method,
      headers: headers,
      body: body != null ? JSON.stringify(body) : undefined,
    });

    var data = null;
    var text = await res.text();
    if (text) {
      try {
        data = JSON.parse(text);
      } catch (e) {
        data = text;
      }
    }
    var msg = (data && (data.detail || data.message)) || res.statusText || "Request failed";
    if (Array.isArray(msg)) msg = msg.map(function (m) { return m.msg || m; }).join(", ");

    // A 401 from /auth/login is just "wrong password/2FA code" — there was
    // never a session to expire, so show the real backend message instead of
    // clearing a token / redirecting to the login screen we're already on.
    if (res.status === 401 && path !== "/auth/login") {
      setToken("");
      document.dispatchEvent(new CustomEvent("zeta:unauthorized"));
      throw new Error("Session expired — please sign in again");
    }

    if (!res.ok) {
      throw new Error(msg);
    }
    return data;
  }

  window.Zeta = {
    base: BASE,
    apiBase: API,
    getToken: getToken,
    setToken: setToken,
    isAuthed: function () { return !!getToken(); },

    get: function (p) { return request("GET", p); },
    post: function (p, b) { return request("POST", p, b); },
    patch: function (p, b) { return request("PATCH", p, b); },
    put: function (p, b) { return request("PUT", p, b); },
    del: function (p) { return request("DELETE", p); },

    login: async function (username, password, totp) {
      var data = await request("POST", "/auth/login", {
        username: username,
        password: password,
        totp: totp || null,
      });
      setToken(data.access_token);
      return data;
    },
    logout: function () { setToken(""); },
    subUrl: function (subId) { return BASE + "/sub/" + subId; },
    portalUrl: function (subId) { return BASE + "/portal?id=" + encodeURIComponent(subId); },
  };
})();
