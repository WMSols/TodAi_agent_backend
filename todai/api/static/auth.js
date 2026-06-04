/**
 * TodAI auth — local username/password (web) JWT stored in localStorage.
 * Flutter uses Firebase ID tokens on the same API routes.
 */
(function (global) {
  "use strict";

  const TOKEN_KEY = "todai_access_token";
  const USER_KEY = "todai_user";

  let _config = null;
  let _session = null;
  let _user = null;

  function apiBase() {
    if (location.protocol === "file:") return "";
    return location.origin;
  }

  function normalizeLoginName(name) {
    return String(name || "")
      .trim()
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "");
  }

  async function loadConfig() {
    if (_config) return _config;
    const r = await fetch(apiBase() + "/api/auth/config");
    _config = await r.json();
    return _config;
  }

  function authRequired() {
    return _config && _config.auth_required === true;
  }

  function loadStoredSession() {
    try {
      const raw = localStorage.getItem(USER_KEY);
      _user = raw ? JSON.parse(raw) : null;
      _session = localStorage.getItem(TOKEN_KEY)
        ? { access_token: localStorage.getItem(TOKEN_KEY) }
        : null;
    } catch (e) {
      _user = null;
      _session = null;
    }
  }

  function saveSession(body) {
    _session = { access_token: body.access_token };
    _user = body.user || null;
    localStorage.setItem(TOKEN_KEY, body.access_token);
    localStorage.setItem(USER_KEY, JSON.stringify(_user || {}));
  }

  function accessToken() {
    return _session && _session.access_token ? _session.access_token : null;
  }

  function userId() {
    return _user && _user.id ? _user.id : null;
  }

  function userLabel() {
    if (!_user) return "";
    return _user.display_name || _user.login_name || _user.email || (_user.id || "").slice(0, 8);
  }

  function authHeaders(extra) {
    const h = Object.assign({ "Content-Type": "application/json" }, extra || {});
    const t = accessToken();
    if (t) h.Authorization = "Bearer " + t;
    return h;
  }

  function formatApiError(body, status) {
    if (!body) return "Request failed (" + status + ")";
    if (typeof body === "string") return body;
    const d = body.detail;
    if (typeof d === "string") return d;
    if (Array.isArray(d)) return d.map(function (x) { return x.msg || x; }).join("; ");
    return body.message || JSON.stringify(body);
  }

  async function bootstrapBackend() {
    const r = await fetch(apiBase() + "/api/auth/bootstrap", {
      method: "POST",
      headers: authHeaders(),
    });
    const body = await r.json().catch(function () { return null; });
    if (!r.ok) {
      throw new Error("Could not set up your profile: " + formatApiError(body, r.status));
    }
    return body;
  }

  async function signInUsername(loginName, password) {
    const r = await fetch(apiBase() + "/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: loginName.trim(), password: password }),
    });
    const body = await r.json().catch(function () { return null; });
    if (!r.ok) {
      throw new Error(formatApiError(body, r.status));
    }
    saveSession(body);
    await bootstrapBackend();
    return body;
  }

  async function signUpAccount(displayName, email, password) {
    const name = String(displayName || "").trim();
    const mail = String(email || "").trim();
    const pwd = String(password);
    if (!name) throw new Error("Enter your name.");
    if (!pwd) throw new Error("Enter a password.");

    const r = await fetch(apiBase() + "/api/auth/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ display_name: name, email: mail, password: pwd }),
    });
    const body = await r.json().catch(function () { return {}; });
    if (!r.ok) {
      throw new Error(formatApiError(body, r.status));
    }
    saveSession(body);
    await bootstrapBackend();
    return body;
  }

  async function clearStaleSession() {
    _session = null;
    _user = null;
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
  }

  async function signOut() {
    await clearStaleSession();
  }

  async function prepare(onStatus) {
    const cfg = await loadConfig();
    if (onStatus) onStatus("Loading…");

    if (!cfg.auth_required) {
      const q = new URLSearchParams(location.search).get("user");
      return { userId: q || "default", authRequired: false, localMode: true, user: null };
    }

    loadStoredSession();
    if (_session && _user && _user.id) {
      if (onStatus) onStatus("Checking session…");
      try {
        if (onStatus) onStatus("Syncing profile…");
        await bootstrapBackend();
        return {
          userId: _user.id,
          authRequired: true,
          localMode: false,
          user: _user,
        };
      } catch (e) {
        console.warn("session invalid", e);
        await clearStaleSession();
        return {
          userId: null,
          authRequired: true,
          localMode: false,
          user: null,
          needsLogin: true,
          bootstrapError: String(e.message || e),
        };
      }
    }

    return {
      userId: null,
      authRequired: true,
      localMode: false,
      user: null,
      needsLogin: true,
    };
  }

  global.TodaiAuth = {
    loadConfig,
    authRequired,
    accessToken,
    userId,
    userLabel,
    authHeaders,
    signInUsername,
    signUpAccount,
    signOut,
    prepare,
    bootstrapBackend,
    clearStaleSession,
  };
})(window);
