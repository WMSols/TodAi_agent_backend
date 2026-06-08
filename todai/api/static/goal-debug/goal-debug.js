(function () {
  "use strict";

  const POLL_MS = 3000;

  const state = {
    catalog: null,
    prompts: [],
    planId: "",
    selectedRouteId: null,
    selectedPromptId: null,
    tracePromptPhase: null,
    history: null,
    historyFingerprint: "",
    pollTimer: null,
  };

  function apiBase() {
    return location.origin;
  }

  function resolveUserId() {
    try {
      const u = JSON.parse(localStorage.getItem("todai_user") || "{}");
      return u.id || "default";
    } catch (e) {
      return "default";
    }
  }

  function resolvePlanIdFromMainApp() {
    const q = new URLSearchParams(location.search).get("plan_id");
    if (q) return q.trim();
    return sessionStorage.getItem("todai_goal_plan_id_" + resolveUserId()) || "";
  }

  function authHeaders() {
    const token = localStorage.getItem("todai_access_token");
    const h = { "Content-Type": "application/json" };
    if (token) h.Authorization = "Bearer " + token;
    return h;
  }

  async function api(path, opts) {
    const r = await fetch(apiBase() + path, {
      ...opts,
      headers: { ...authHeaders(), ...(opts && opts.headers) },
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) {
      const d = j.detail;
      const msg = typeof d === "string" ? d : Array.isArray(d) ? d.map(function (x) { return x.msg || x; }).join("; ") : r.statusText;
      throw new Error(msg || "Request failed");
    }
    return j;
  }

  function setStatus(msg) {
    document.getElementById("status-bar").textContent = msg || "";
  }

  function el(id) {
    return document.getElementById(id);
  }

  function historyFingerprint(data) {
    const turns = (data && data.turns) || [];
    return turns.length + ":" + (turns.length ? turns[turns.length - 1].user_message : "");
  }

  async function loadCatalog() {
    state.catalog = await api("/api/goals/debug/catalog");
    renderRoutes();
    renderArchitecture();
    if (state.selectedRouteId) showRouteDetail(state.selectedRouteId);
  }

  async function loadPrompts() {
    const data = await api("/api/goals/debug/prompts");
    state.prompts = data.prompts || [];
    renderPrompts();
    if (state.selectedPromptId) showPromptEditor(state.selectedPromptId);
  }

  async function loadPlans() {
    const data = await api("/api/goals/plan/plans");
    const sel = el("plan-select");
    const plans = data.plans || [];
    const current = state.planId || resolvePlanIdFromMainApp();
    sel.innerHTML = '<option value="">— select plan —</option>';
    plans.forEach(function (p) {
      const opt = document.createElement("option");
      const pid = p.plan_id || p.id || "";
      opt.value = pid;
      const title = p.title || p.goal_title || "Plan";
      const phase = p.phase || p.status || "";
      opt.textContent = title + (pid ? " · " + pid.slice(0, 8) : "") + (phase ? " (" + phase + ")" : "");
      sel.appendChild(opt);
    });
    if (current) {
      state.planId = current;
      sel.value = current;
      if (!sel.value && current) {
        const opt = document.createElement("option");
        opt.value = current;
        opt.textContent = current.slice(0, 8) + "… (from main app)";
        opt.selected = true;
        sel.appendChild(opt);
      }
    }
  }

  function renderArchitecture() {
    const arch = state.catalog && state.catalog.architecture;
    if (!arch) return;
    el("arch-title").textContent = arch.title;
    const steps = el("arch-steps");
    steps.innerHTML = "";
    (arch.steps || []).forEach(function (s) {
      const li = document.createElement("li");
      li.textContent = s;
      steps.appendChild(li);
    });
  }

  function renderRoutes() {
    const list = el("route-list");
    list.innerHTML = "";
    const routes = (state.catalog && state.catalog.routes) || [];
    routes.forEach(function (route) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "list-item" + (state.selectedRouteId === route.id ? " active" : "");
      btn.innerHTML =
        '<div class="id">' + route.id + '</div><div class="desc">' + escapeHtml(route.description) + "</div>";
      btn.addEventListener("click", function () {
        state.selectedRouteId = route.id;
        renderRoutes();
        showRouteDetail(route.id);
      });
      list.appendChild(btn);
    });
  }

  function showRouteDetail(routeId) {
    const route = ((state.catalog && state.catalog.routes) || []).find(function (r) {
      return r.id === routeId;
    });
    const box = el("route-detail-compact");
    if (!route) {
      box.innerHTML = "";
      return;
    }
    let html =
      "<h4>" + escapeHtml(route.label) + "</h4>" +
      "<p>" + escapeHtml(route.description) + "</p>" +
      "<p class='meta'><code>" + escapeHtml(route.handler) + "</code></p>" +
      "<div class='pattern-flow'>";
    (route.pattern || []).forEach(function (step, i) {
      if (i > 0) html += "<span class='arrow'>→</span>";
      html += "<span class='node'>" + escapeHtml(step) + "</span>";
    });
    html += "</div><p class='meta'>Prompts: ";
    html += (route.prompts || []).map(function (pid) {
      return "<button type='button' class='link-btn' data-prompt='" + escapeHtml(pid) + "'>" + escapeHtml(pid) + "</button>";
    }).join(" ");
    html += "</p>";
    box.innerHTML = html;
    box.querySelectorAll(".link-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        switchSidebarTab("prompts");
        state.selectedPromptId = btn.getAttribute("data-prompt");
        renderPrompts();
        showPromptEditor(state.selectedPromptId);
        openCatalogPromptModal(state.selectedPromptId);
      });
    });
  }

  function renderPrompts() {
    const list = el("prompt-list");
    list.innerHTML = "";
    state.prompts.forEach(function (p) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "list-item" + (state.selectedPromptId === p.id ? " active" : "");
      const badge = p.is_overridden ? '<span class="badge override">override</span>' : "";
      btn.innerHTML =
        '<div class="id">' + p.id + badge + '</div><div class="desc">' + escapeHtml(p.title) + "</div>";
      btn.addEventListener("click", function () {
        state.selectedPromptId = p.id;
        renderPrompts();
        showPromptEditor(p.id);
        openCatalogPromptModal(p.id);
      });
      list.appendChild(btn);
    });
  }

  function showPromptEditor(promptId) {
    const p = state.prompts.find(function (x) { return x.id === promptId; });
    if (!p) return;
    const text = p.is_overridden ? p.override : p.default;
    const formatted = formatPromptContent(text);
    el("prompt-title").textContent = p.title + " (" + p.id + ")";
    el("prompt-purpose").textContent = p.purpose || "";
    el("prompt-intake").textContent = "Intake: " + (p.intake || "—");
    el("prompt-file").textContent = p.file + " → " + p.constant;
    el("prompt-routes").textContent = "Routes: " + (p.routes || []).join(", ");
    el("prompt-text").value = formatted;
    resizePromptTextarea();
    el("prompt-override-hint").textContent = p.is_overridden
      ? "Runtime override active (not saved to disk) · " + formatted.length + " chars"
      : "Using default prompt from codebase · " + formatted.length + " chars";
    const sidebar = el("prompt-sidebar-preview");
    const sidebarText = el("prompt-sidebar-text");
    if (sidebar && sidebarText) {
      sidebar.hidden = false;
      sidebarText.textContent = formatted;
    }
  }

  function resizePromptTextarea() {
    const ta = el("prompt-text");
    if (!ta) return;
    ta.style.height = "auto";
    const h = Math.min(Math.max(ta.scrollHeight + 4, 280), Math.max(window.innerHeight * 0.55, 320));
    ta.style.height = h + "px";
  }

  async function applyPromptOverride() {
    if (!state.selectedPromptId) return;
    await api("/api/goals/debug/prompts/" + encodeURIComponent(state.selectedPromptId), {
      method: "PUT",
      body: JSON.stringify({ content: el("prompt-text").value }),
    });
    setStatus("Runtime override applied for " + state.selectedPromptId);
    await loadPrompts();
  }

  async function resetPrompt() {
    if (!state.selectedPromptId) return;
    try {
      await api("/api/goals/debug/prompts/" + encodeURIComponent(state.selectedPromptId), { method: "DELETE" });
    } catch (e) { /* none */ }
    setStatus("Reset to default: " + state.selectedPromptId);
    await loadPrompts();
  }

  async function resetAllPrompts() {
    await api("/api/goals/debug/prompts/reset", { method: "POST" });
    setStatus("All runtime prompt overrides cleared");
    await loadPrompts();
  }

  function renderSessionSummary() {
    const box = el("session-summary");
    const session = (state.history && state.history.session) || {};
    const phase = session.phase || "—";
    const answers = session.answers || {};
    const bits = ["Phase: <strong>" + escapeHtml(phase) + "</strong>"];
    if (answers.objective && answers.objective.display) bits.push("Objective: " + escapeHtml(answers.objective.display));
    if (answers.tasks_per_day && answers.tasks_per_day.parsed) bits.push("Tasks/day: " + answers.tasks_per_day.parsed);
    if (answers.skip_days && answers.skip_days.display) bits.push("Skip: " + escapeHtml(answers.skip_days.display));
    const turns = (state.history && state.history.turns) || [];
    bits.push("Turns: " + turns.length);
    box.innerHTML = bits.join(" · ");
  }

  async function loadHistory(opts) {
    const silent = opts && opts.silent;
    const planId = el("plan-select").value || state.planId || resolvePlanIdFromMainApp();
    if (!planId) {
      el("trace-list").innerHTML =
        '<div class="empty">No plan selected. Chat on the <a href="/">main Goal planner</a> or pick a plan above.<br><br>' +
        "The dashboard reads the same plan id from your main app session.</div>";
      el("session-summary").innerHTML = "";
      return;
    }
    state.planId = planId;
    el("plan-select").value = planId;

    const data = await api("/api/goals/debug/plans/" + encodeURIComponent(planId) + "/history");
    const fp = historyFingerprint(data);
    if (silent && fp === state.historyFingerprint) return;
    state.historyFingerprint = fp;
    state.history = data;
    renderSessionSummary();
    renderHistory();
    if (!silent) setStatus("History updated — " + ((data.turns || []).length) + " turn(s)");
  }

  function renderHistory() {
    const list = el("trace-list");
    const turns = (state.history && state.history.turns) || [];
    if (!turns.length) {
      list.innerHTML =
        '<div class="empty">No turns yet. Send messages on the <a href="/?mode=goal">main Goal planner</a> — traces will show here automatically.</div>';
      return;
    }
    list.innerHTML = "";
    turns.slice().reverse().forEach(function (turn, idx) {
      const n = turns.length - idx;
      const hasTrace = (turn.tool_trace && turn.tool_trace.length) || (turn.groq_trace && turn.groq_trace.length);
      const card = document.createElement("article");
      card.className = "turn-card" + (idx === 0 ? " latest" : "");
      card.setAttribute("data-turn-reverse-idx", String(idx));
      card.innerHTML =
        '<header class="turn-head">' +
        '<span class="turn-num">#' + n + "</span>" +
        '<span class="route">' + escapeHtml(turn.route || "?") + "</span>" +
        '<span class="phase">' + escapeHtml(turn.phase || "") + "</span>" +
        (turn.ui_mode ? '<span class="ui-mode">' + escapeHtml(turn.ui_mode) + "</span>" : "") +
        (!hasTrace ? '<span class="badge warn">no trace</span>' : "") +
        "</header>" +
        '<div class="turn-body">' +
        '<div class="msg-block user"><label>User</label><pre>' + escapeHtml(turn.user_message || "") + "</pre></div>" +
        '<div class="msg-block assistant"><label>Assistant</label><pre>' + escapeHtml(turn.assistant_message || "") + "</pre></div>" +
        renderTimelineHtml(turn.tool_trace || [], turn.groq_trace || []) +
        '<details class="raw-block"><summary>Raw trace JSON</summary><pre class="raw">' +
        escapeHtml(JSON.stringify({ tool_trace: turn.tool_trace, groq_trace: turn.groq_trace }, null, 2)) +
        "</pre></details></div>";
      list.appendChild(card);
    });
    bindTracePromptHandlers(list, turns);
  }

  function bindTracePromptHandlers(list, turns) {
    const reversed = turns.slice().reverse();
    list.querySelectorAll(".tl-step.groq").forEach(function (step) {
      const card = step.closest(".turn-card");
      if (!card) return;
      const turnIdx = parseInt(card.getAttribute("data-turn-reverse-idx") || "-1", 10);
      const groqIdx = parseInt(step.getAttribute("data-groq-idx") || "-1", 10);
      if (turnIdx < 0 || groqIdx < 0 || turnIdx >= reversed.length) return;
      const turn = reversed[turnIdx];
      const call = (turn.groq_trace || [])[groqIdx];
      if (!call) return;

      step.querySelectorAll(".groq-detail").forEach(function (detailsEl) {
        const summary = detailsEl.querySelector(".groq-prompt-summary");
        if (summary && call.messages && call.messages.length) {
          summary.addEventListener("click", function (e) {
            if (e.target.closest(".groq-open-panel")) return;
            e.preventDefault();
            showTracePromptInInspector(call.phase, call.messages, null);
          });
        }
        const respSummary = detailsEl.querySelector(".groq-response-summary");
        if (respSummary && call.response) {
          respSummary.addEventListener("click", function (e) {
            e.preventDefault();
            openPromptModal({
              title: "Groq response · " + (call.phase || "?"),
              subtitle: "Model JSON response for this call",
              bodyHtml: formatResponseHtml(call.response),
            });
          });
        }
      });
      step.querySelectorAll(".groq-open-panel").forEach(function (btn) {
        btn.addEventListener("click", function (e) {
          e.preventDefault();
          e.stopPropagation();
          showTracePromptInInspector(call.phase, call.messages, call.response);
        });
      });
    });
  }

  function renderTimelineHtml(toolTrace, groqTrace) {
    if (!(toolTrace && toolTrace.length) && !(groqTrace && groqTrace.length)) {
      return '<p class="no-trace">No trace stored for this turn (older turns before trace persistence).</p>';
    }
    let html = '<div class="timeline"><div class="tl-title">Execution path</div>';
    (toolTrace || []).forEach(function (step) {
      if (!step || typeof step !== "object") return;
      const phase = step.phase || step.route || "?";
      const bits = [];
      if (step.route) bits.push("route=" + step.route);
      if (step.final_route) bits.push("final=" + step.final_route);
      if (step.source) bits.push("source=" + step.source);
      if (step.reason) bits.push(step.reason);
      if (step.manage_action && step.manage_action !== "none") bits.push("action=" + step.manage_action);
      if (step.tools && step.tools.length) bits.push("tools=" + step.tools.join(","));
      html +=
        '<div class="tl-step router"><div class="label">' + escapeHtml(String(phase)) + '</div>' +
        '<div class="meta">' + escapeHtml(bits.join(" · ")) + "</div></div>";
    });
    (groqTrace || []).forEach(function (call, i) {
      const ok = call.ok ? "ok" : "fail";
      const ov = call.override_applied ? " · prompt override" : "";
      html +=
        '<div class="tl-step groq" data-groq-idx="' + i + '">' +
        '<div class="label">groq:' + escapeHtml(call.phase || "?") +
        " <span class='ok-" + ok + "'>(" + ok + ")</span>" + ov + "</div>";
      if (call.messages && call.messages.length) {
        html +=
          '<details class="groq-detail">' +
          '<summary class="groq-prompt-summary">' +
          "Prompt sent (" + call.messages.length + " message" + (call.messages.length === 1 ? "" : "s") + ")" +
          '<button type="button" class="link-btn groq-open-panel" title="Open full prompt in popup">Open popup →</button>' +
          "</summary>" +
          formatMessagesHtml(call.messages) +
          "</details>";
      }
      if (call.response) {
        html +=
          '<details class="groq-detail">' +
          '<summary class="groq-response-summary">Response (click to open popup)</summary>' +
          formatResponseHtml(call.response) +
          "</details>";
      }
      html += "</div>";
    });
    html += "</div>";
    return html;
  }

  function switchSidebarTab(tab) {
    document.querySelectorAll(".tabs button").forEach(function (b) {
      b.classList.toggle("active", b.dataset.tab === tab);
    });
    el("panel-routes").style.display = tab === "routes" ? "block" : "none";
    el("panel-prompts").style.display = tab === "prompts" ? "flex" : "none";
  }

  function startPolling() {
    stopPolling();
    state.pollTimer = setInterval(function () {
      if (document.hidden) return;
      loadHistory({ silent: true }).catch(function () {});
    }, POLL_MS);
  }

  function stopPolling() {
    if (state.pollTimer) clearInterval(state.pollTimer);
    state.pollTimer = null;
  }

  function escapeHtml(s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  /** Normalize prompt text: preserve real newlines; expand literal \\n when present. */
  function normalizePromptText(text) {
    let s = String(text == null ? "" : text);
    if (s.indexOf("\\n") !== -1 && s.indexOf("\n") === -1) {
      s = s.replace(/\\n/g, "\n").replace(/\\t/g, "\t");
    }
    return s.replace(/\r\n/g, "\n");
  }

  /** Pretty-print JSON payloads; leave plain text unchanged. */
  function formatPromptContent(text) {
    const normalized = normalizePromptText(text);
    const trimmed = normalized.trim();
    if (
      (trimmed.startsWith("{") && trimmed.endsWith("}")) ||
      (trimmed.startsWith("[") && trimmed.endsWith("]"))
    ) {
      try {
        return JSON.stringify(JSON.parse(trimmed), null, 2);
      } catch (e) {
        /* keep original */
      }
    }
    return normalized;
  }

  /** Old trace snapshots were cut at 600 (messages) or 800 (responses) chars + … */
  function isLikelyTruncatedSnapshot(text) {
    const s = String(text || "");
    if (!/…$/.test(s)) return false;
    const len = s.length;
    return len === 601 || len === 801 || len === 600 + 1 || len === 800 + 1;
  }

  function truncationWarningHtml() {
    return (
      '<p class="trace-truncation-warn">' +
      "This snapshot was recorded with an older 600/800-char limit and is incomplete. " +
      "Restart the server and send a <strong>new message</strong> to capture the full prompt." +
      "</p>"
    );
  }

  function formatMessagesHtml(messages) {
    if (!messages || !messages.length) {
      return '<p class="no-trace">No messages recorded.</p>';
    }
    let html = "";
    let anyTruncated = false;
    messages.forEach(function (msg, idx) {
      if (isLikelyTruncatedSnapshot(msg.content)) anyTruncated = true;
    });
    if (anyTruncated) html += truncationWarningHtml();
    html += '<div class="prompt-view">';
    messages.forEach(function (msg, idx) {
      const role = String(msg.role || "unknown");
      const raw = normalizePromptText(msg.content);
      const content = formatPromptContent(raw);
      const truncated = isLikelyTruncatedSnapshot(raw);
      html +=
        '<article class="prompt-msg prompt-msg-' + escapeHtml(role) + (truncated ? " truncated" : "") + '">' +
        '<header class="prompt-msg-head">' +
        '<span class="prompt-role">' + escapeHtml(role.toUpperCase()) + "</span>" +
        '<span class="prompt-meta">message ' + (idx + 1) +
        " · " + content.length + " chars" +
        (truncated ? " · <span class='trunc-badge'>truncated</span>" : "") +
        "</span></header>" +
        '<pre class="prompt-msg-body">' + escapeHtml(content) + "</pre>" +
        "</article>";
    });
    html += "</div>";
    return html;
  }

  function formatResponseHtml(response) {
    let text;
    if (typeof response === "string") {
      text = formatPromptContent(response);
    } else {
      text = JSON.stringify(response, null, 2);
    }
    const truncated = isLikelyTruncatedSnapshot(text);
    let html = "";
    if (truncated) html += truncationWarningHtml();
    html +=
      '<article class="prompt-msg prompt-msg-response' + (truncated ? " truncated" : "") + '">' +
      '<header class="prompt-msg-head">' +
      '<span class="prompt-role">RESPONSE</span>' +
      '<span class="prompt-meta">' + text.length + " chars" +
      (truncated ? " · <span class='trunc-badge'>truncated</span>" : "") +
      "</span></header>" +
      '<pre class="prompt-msg-body">' + escapeHtml(text) + "</pre>" +
      "</article>";
    return html;
  }

  function openPromptModal(opts) {
    const modal = el("prompt-modal");
    if (!modal) return;
    el("prompt-modal-title").textContent = opts.title || "Prompt";
    el("prompt-modal-subtitle").textContent = opts.subtitle || "";
    el("prompt-modal-body").innerHTML = opts.bodyHtml || "";
    modal.hidden = false;
    document.body.classList.add("modal-open");
    el("prompt-modal-close").focus();
  }

  function closePromptModal() {
    const modal = el("prompt-modal");
    if (!modal) return;
    modal.hidden = true;
    document.body.classList.remove("modal-open");
    el("prompt-modal-body").innerHTML = "";
  }

  function openCatalogPromptModal(promptId) {
    const p = state.prompts.find(function (x) { return x.id === promptId; });
    if (!p) return;
    const text = p.is_overridden ? p.override : p.default;
    const formatted = formatPromptContent(text);
    const meta = [
      p.file + " → " + p.constant,
      p.purpose || "",
      "Intake: " + (p.intake || "—"),
      p.is_overridden ? "Runtime override active" : "Default from codebase",
      formatted.length + " characters",
    ].filter(Boolean).join(" · ");
    openPromptModal({
      title: p.title + " (" + p.id + ")",
      subtitle: meta,
      bodyHtml:
        '<pre class="prompt-modal-text">' + escapeHtml(formatted) + "</pre>",
    });
  }

  function showTracePromptInInspector(phase, messages, response) {
    state.tracePromptPhase = phase || null;
    let html = formatMessagesHtml(messages);
    if (response != null && response !== "") {
      html += formatResponseHtml(response);
    }
    const catalog = state.prompts.find(function (p) { return p.id === phase; });
    openPromptModal({
      title: "Groq trace · " + (phase || "?"),
      subtitle: catalog
        ? catalog.title + " — messages sent to the model for this turn"
        : "Messages sent to the model for this turn",
      bodyHtml: html,
    });
    el("trace-prompt-empty").hidden = true;
    const content = el("trace-prompt-content");
    if (content) {
      content.hidden = false;
      el("trace-prompt-phase").textContent = phase || "?";
      el("trace-prompt-messages").innerHTML = html;
      const btn = el("btn-open-catalog-prompt");
      if (btn) btn.hidden = !catalog;
    }
  }

  function openCatalogPromptForTrace() {
    if (!state.tracePromptPhase) return;
    const pid = state.tracePromptPhase;
    if (!state.prompts.some(function (p) { return p.id === pid; })) return;
    switchSidebarTab("prompts");
    state.selectedPromptId = pid;
    renderPrompts();
    showPromptEditor(pid);
  }

  async function init() {
    document.querySelectorAll(".tabs button").forEach(function (btn) {
      btn.addEventListener("click", function () { switchSidebarTab(btn.dataset.tab); });
    });
    el("btn-refresh-plans").addEventListener("click", function () {
      loadPlans().then(loadHistory).catch(function (e) { setStatus(e.message); });
    });
    el("btn-refresh-history").addEventListener("click", function () {
      loadHistory().catch(function (e) { setStatus(e.message); });
    });
    el("plan-select").addEventListener("change", function () {
      state.planId = el("plan-select").value;
      state.historyFingerprint = "";
      loadHistory().catch(function () {});
    });
    el("btn-apply-prompt").addEventListener("click", function () {
      applyPromptOverride().catch(function (e) { setStatus(e.message); });
    });
    el("btn-reset-prompt").addEventListener("click", function () {
      resetPrompt().catch(function (e) { setStatus(e.message); });
    });
    el("btn-reset-all-prompts").addEventListener("click", function () {
      resetAllPrompts().catch(function (e) { setStatus(e.message); });
    });
    el("btn-open-catalog-prompt").addEventListener("click", openCatalogPromptForTrace);
    el("prompt-modal-close").addEventListener("click", closePromptModal);
    el("prompt-modal-backdrop").addEventListener("click", closePromptModal);
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && !el("prompt-modal").hidden) closePromptModal();
    });
    el("prompt-text").addEventListener("input", resizePromptTextarea);
    window.addEventListener("resize", resizePromptTextarea);

    document.addEventListener("visibilitychange", function () {
      el("live-indicator").classList.toggle("on", !document.hidden);
      if (!document.hidden) loadHistory({ silent: true }).catch(function () {});
    });

    state.planId = resolvePlanIdFromMainApp();

    try {
      await Promise.all([loadCatalog(), loadPrompts(), loadPlans()]);
      if (state.catalog && state.catalog.routes && state.catalog.routes.length) {
        state.selectedRouteId = state.catalog.routes[0].id;
        renderRoutes();
        showRouteDetail(state.selectedRouteId);
      }
      if (state.prompts.length) {
        state.selectedPromptId = state.prompts[0].id;
        renderPrompts();
        showPromptEditor(state.selectedPromptId);
      }
      await loadHistory();
      startPolling();
      setStatus(
        state.planId
          ? "Watching plan " + state.planId.slice(0, 8) + "… — chat on main app to see traces"
          : "Open main Goal planner and chat — this page auto-syncs your active plan"
      );
    } catch (e) {
      el("trace-list").innerHTML = '<div class="empty error">Failed to load: ' + escapeHtml(e.message) + "</div>";
      setStatus("Load failed: " + e.message);
    }
  }

  init();
})();
