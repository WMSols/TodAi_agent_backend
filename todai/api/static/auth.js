/**
 * Supabase Auth — username + password sign-in, email required on sign-up.
 * Sign-up uses POST /api/auth/register (service role, no confirmation email).
 */
(function (global) {
  "use strict";

  const LOGIN_MAP_KEY = "todai_login_map";

  let _config = null;
  let _client = null;
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

  function rememberLogin(loginName, email) {
    const key = normalizeLoginName(loginName);
    if (!key || !email) return;
    const m = JSON.parse(localStorage.getItem(LOGIN_MAP_KEY) || "{}");
    m[key] = email.trim();
    localStorage.setItem(LOGIN_MAP_KEY, JSON.stringify(m));
  }

  function emailForLoginName(loginName) {
    const key = normalizeLoginName(loginName);
    if (!key) return null;
    const m = JSON.parse(localStorage.getItem(LOGIN_MAP_KEY) || "{}");
    if (m[key]) return m[key];
    if (String(loginName).includes("@")) return String(loginName).trim();
    return null;
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

  function accessToken() {
    return _session && _session.access_token ? _session.access_token : null;
  }

  function userId() {
    return _user && _user.id ? _user.id : null;
  }

  function userLabel() {
    if (!_user) return "";
    const meta = _user.user_metadata || {};
    return meta.full_name || meta.display_name || meta.name || _user.email || _user.id.slice(0, 8);
  }

  function authHeaders(extra) {
    const h = Object.assign({ "Content-Type": "application/json" }, extra || {});
    const t = accessToken();
    if (t) h.Authorization = "Bearer " + t;
    return h;
  }

  async function initSupabase() {
    const cfg = await loadConfig();
    if (!cfg.supabase_url || !cfg.supabase_anon_key) {
      throw new Error("Supabase URL and anon key missing in server .env");
    }
    if (!global.supabase || !global.supabase.createClient) {
      throw new Error("Supabase JS SDK not loaded");
    }
    _client = global.supabase.createClient(cfg.supabase_url, cfg.supabase_anon_key, {
      auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: true },
    });
    const { data, error } = await _client.auth.getSession();
    if (error) console.warn("getSession", error);
    _session = data.session;
    _user = _session ? _session.user : null;
    _client.auth.onAuthStateChange(function (_event, session) {
      _session = session;
      _user = session ? session.user : null;
    });
    return _client;
  }

  async function clearStaleSession() {
    if (_client) await _client.auth.signOut();
    _session = null;
    _user = null;
  }

  async function validateSession() {
    if (!_session) return false;
    const { data, error } = await _client.auth.getUser();
    if (error || !data.user) return false;
    _user = data.user;
    return true;
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

  async function signInWithPassword(email, password) {
    const { data, error } = await _client.auth.signInWithPassword({
      email: email.trim(),
      password: password,
    });
    if (error) throw error;
    _session = data.session;
    _user = data.user;
    await bootstrapBackend();
    return { email: email.trim(), session: data.session };
  }

  async function signInUsername(loginName, password) {
    const email = emailForLoginName(loginName);
    if (!email) {
      throw new Error(
        "Unknown username on this browser. Use the same name as when you registered, " +
          "or enter your email in the username field if you used one."
      );
    }
    const result = await signInWithPassword(email, password);
    rememberLogin(loginName, result.email);
    return result;
  }

  async function signUpAccount(displayName, email, password) {
    const name = String(displayName || "").trim();
    const mail = String(email || "").trim();
    const pwd = String(password);
    if (!name) throw new Error("Enter your name.");
    if (!mail) throw new Error("Enter your email.");
    if (!pwd) throw new Error("Enter a password.");

    const reg = await fetch(apiBase() + "/api/auth/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ display_name: name, email: mail, password: pwd }),
    });
    const regBody = await reg.json().catch(function () {
      return {};
    });
    if (!reg.ok) {
      throw new Error(formatApiError(regBody, reg.status));
    }

    rememberLogin(name, mail);
    const signedIn = await signInWithPassword(mail, pwd);
    return { email: mail, login_name: regBody.login_name || normalizeLoginName(name), session: signedIn.session };
  }

  async function signInOAuth(provider) {
    const { error } = await _client.auth.signInWithOAuth({
      provider: provider,
      options: { redirectTo: location.origin + location.pathname },
    });
    if (error) throw error;
  }

  async function signOut() {
    if (_client) await _client.auth.signOut();
    _session = null;
    _user = null;
  }

  async function prepare(onStatus) {
    const cfg = await loadConfig();
    if (onStatus) onStatus("Loading…");

    if (!cfg.auth_required) {
      const q = new URLSearchParams(location.search).get("user");
      return { userId: q || "default", authRequired: false, localMode: true, user: null };
    }

    await initSupabase();
    if (onStatus) onStatus("Checking session…");

    if (location.hash && location.hash.includes("access_token")) {
      await _client.auth.getSession();
      const fresh = await _client.auth.getSession();
      _session = fresh.data.session;
      _user = _session ? _session.user : null;
      history.replaceState(null, "", location.pathname + location.search);
    }

    if (_session && _user) {
      const valid = await validateSession();
      if (!valid) {
        await clearStaleSession();
        return {
          userId: null,
          authRequired: true,
          localMode: false,
          user: null,
          needsLogin: true,
        };
      }
      if (onStatus) onStatus("Syncing profile…");
      try {
        await bootstrapBackend();
      } catch (e) {
        console.warn("bootstrap failed, clearing session", e);
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
      const meta = _user.user_metadata || {};
      const loginName = meta.login_name || meta.full_name || meta.display_name;
      if (loginName && _user.email) rememberLogin(loginName, _user.email);
      return {
        userId: _user.id,
        authRequired: true,
        localMode: false,
        user: _user,
      };
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
    rememberLogin,
    emailForLoginName,
    signInWithPassword,
    signInUsername,
    signInEmail: signInWithPassword,
    signUpAccount,
    signInOAuth,
    signOut,
    prepare,
    bootstrapBackend,
    clearStaleSession,
  };
})(window);
