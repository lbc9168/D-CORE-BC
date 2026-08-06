/* Drive the local review shell through explicit Studio API calls and never infer a successful save. */

(() => {
  "use strict";

  const bootstrap = document.querySelector("#review-bootstrap");
  const state = {
    sampleId: bootstrap ? JSON.parse(bootstrap.textContent).sampleId : null,
    articleContext: bootstrap ? JSON.parse(bootstrap.textContent).article : null,
    article: null,
    structureRevision: null,
    structureAccepted: bootstrap ? sessionStorage.getItem(`structure-gate:${JSON.parse(bootstrap.textContent).sampleId}`) === "accepted" : false,
    retry: null,
    timers: new Map(),
  };

  /** Return a readable representation without turning reviewer values into HTML. */
  function displayValue(value) {
    if (value === null || value === undefined || value === "") return "未提供";
    return typeof value === "string" ? value : JSON.stringify(value);
  }

  /** Report a durable request state and expose a safe retry only after a failed request. */
  function setSaveState(message, kind = "ready", retry = null) {
    const output = document.querySelector("#save-state");
    if (!output) return;
    output.className = `save-state ${kind}`;
    output.textContent = message;
    state.retry = retry;
    let retryButton = document.querySelector("#retry-save");
    if (retry) {
      if (!retryButton) {
        retryButton = document.createElement("button");
        retryButton.id = "retry-save";
        retryButton.type = "button";
        retryButton.textContent = "重试保存";
        retryButton.addEventListener("click", () => state.retry && state.retry());
        output.after(retryButton);
      }
    } else if (retryButton) {
      retryButton.remove();
    }
  }

  /** Make one API request and surface its server response rather than applying an optimistic result. */
  async function request(url, options = {}) {
    const response = await fetch(url, {
      headers: { "Content-Type": "application/json", "X-Review-Actor": "local-reviewer" },
      ...options,
    });
    if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail || response.statusText);
    return response.json();
  }

  /** Replace client draft state only with a successful server article response. */
  function applyArticle(payload) {
    state.article = payload.article;
    state.structureRevision = payload.structure_revision;
    state.payload = payload;
    renderReview();
  }

  /** Persist a field decision atomically and redraw only after the server has accepted it. */
  async function saveField(path, field) {
    const operation = async () => {
      setSaveState("正在保存…", "pending");
      const payload = await request(`/api/articles/${encodeURIComponent(state.sampleId)}/fields/${path}`, {
        method: "PUT",
        body: JSON.stringify({ field }),
      });
      applyArticle(payload);
      setSaveState("已保存", "saved");
    };
    try {
      await operation();
    } catch (error) {
      setSaveState(`Save failed: ${error.message}`, "error", () => saveField(path, field));
    }
  }

  /** Return the current human field at a stable project-field path. */
  function currentField(path) {
    const [projectId, fieldName] = path.split(".");
    const project = state.article?.projects.find((item) => item.gold_project_id === projectId);
    return project?.fields[fieldName] || null;
  }

  /** Select the first present candidate that supplies this field, in stable source order. */
  function suggestedField(path, sourceName = null) {
    const [projectId, fieldName] = path.split(".");
    const projectIndex = state.article?.projects.findIndex((item) => item.gold_project_id === projectId);
    const sources = state.payload?.candidate_bundle?.sources || {};
    const names = sourceName ? [sourceName] : ["review", "production", "deterministic", "database"];
    for (const name of names) {
      const field = sources[name]?.projects?.[projectIndex]?.fields?.[fieldName];
      if (field?.state === "present") return field;
    }
    return null;
  }

  /** Render provenance rows for every human or candidate value in one field card. */
  function provenanceRows(path) {
    const [projectId, fieldName] = path.split(".");
    const projectIndex = state.article.projects.findIndex((item) => item.gold_project_id === projectId);
    const rows = [["人工草稿（review DB）", currentField(path)]];
    for (const [source, candidate] of Object.entries(state.payload?.candidate_bundle?.sources || {})) {
      rows.push([`${source} 候选`, candidate.projects?.[projectIndex]?.fields?.[fieldName] || null]);
    }
    return rows.map(([label, field]) => {
      const line = document.createElement("li");
      line.textContent = `${label}：${displayValue(field?.value)}（${field?.state || "未提供"}）`;
      return line;
    });
  }

  /** Build an accessible card for one field using DOM nodes so candidate strings remain inert text. */
  function fieldCard(path, title, kind) {
    const field = currentField(path);
    const card = document.createElement("section");
    card.className = `field-card ${kind}`;
    card.dataset.path = path;
    const heading = document.createElement("h3");
    heading.textContent = title;
    const value = document.createElement("p");
    value.className = "field-value";
    value.dataset.testid = `value-${path}`;
    value.textContent = displayValue(field?.value);
    const provenance = document.createElement("ul");
    provenance.className = "provenance";
    provenance.setAttribute("aria-label", `${title} 的候选来源`);
    provenance.replaceChildren(...provenanceRows(path));
    const controls = document.createElement("div");
    controls.className = "field-controls";
    const suggested = suggestedField(path);
    if (suggested) {
      const accept = document.createElement("button");
      accept.type = "button";
      accept.dataset.action = "accept-field";
      accept.dataset.path = path;
      accept.dataset.testid = `accept-${path}`;
      accept.textContent = `接受建议：${displayValue(suggested.value)}`;
      accept.addEventListener("click", () => saveField(path, suggested));
      controls.append(accept);
    }
    const keep = document.createElement("button");
    keep.type = "button";
    keep.className = "secondary";
    keep.dataset.action = "keep-database";
    keep.dataset.path = path;
    keep.textContent = "保留数据库值";
    keep.disabled = !suggestedField(path, "database");
    keep.addEventListener("click", () => saveField(path, suggestedField(path, "database")));
    const edit = document.createElement("button");
    edit.type = "button";
    edit.className = "secondary";
    edit.dataset.action = "edit-field";
    edit.dataset.path = path;
    edit.dataset.testid = `edit-${path}`;
    edit.textContent = "编辑";
    edit.addEventListener("click", () => openEditor(path, card));
    const flag = document.createElement("button");
    flag.type = "button";
    flag.className = "secondary";
    flag.dataset.action = "flag-evidence";
    flag.dataset.path = path;
    flag.textContent = "证据有误";
    flag.addEventListener("click", () => saveField(path, { ...field, state: "needs_adjudication", value: null }));
    controls.append(keep, edit, flag);
    card.append(heading, value, provenance, controls);
    return card;
  }

  /** Open an autosaving text editor whose save requests recover through the shared retry control. */
  function openEditor(path, card) {
    if (card.querySelector("input")) return;
    const field = currentField(path);
    const input = document.createElement("input");
    input.type = "text";
    input.value = typeof field?.value === "string" ? field.value : displayValue(field?.value);
    input.dataset.testid = `editor-${path}`;
    input.setAttribute("aria-label", `编辑 ${path}`);
    const save = () => {
      clearTimeout(state.timers.get(path));
      const next = { ...field, state: input.value.trim() ? "present" : "not_mentioned", value: input.value.trim() || null };
      saveField(path, next);
    };
    input.addEventListener("input", () => {
      clearTimeout(state.timers.get(path));
      state.timers.set(path, setTimeout(save, 650));
    });
    input.addEventListener("blur", save);
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        save();
      }
    });
    card.querySelector(".field-controls").before(input);
    input.focus();
  }

  /** Highlight only non-overlapping human evidence spans in the immutable article text. */
  function renderEvidence() {
    const target = document.querySelector("#article-content");
    if (!target || !state.articleContext?.content) return;
    const text = state.articleContext.content;
    const spans = state.article.projects.flatMap((project) => Object.values(project.fields).flatMap((field) => field.evidence || []))
      .filter((span) => span.end <= text.length && text.slice(span.start, span.end) === span.quote)
      .sort((left, right) => left.start - right.start || left.end - right.end);
    const nodes = [];
    let cursor = 0;
    for (const span of spans) {
      if (span.start < cursor) continue;
      nodes.push(document.createTextNode(text.slice(cursor, span.start)));
      const mark = document.createElement("mark");
      mark.textContent = text.slice(span.start, span.end);
      nodes.push(mark);
      cursor = span.end;
    }
    nodes.push(document.createTextNode(text.slice(cursor)));
    target.replaceChildren(...nodes);
  }

  /** Group fields into conflict-first and collapsed-consensus panels after the structure gate opens. */
  function renderReview() {
    const summary = document.querySelector("#structure-summary");
    const conflicts = document.querySelector("#conflict-cards");
    const lowConfidence = document.querySelector("#low-confidence-cards");
    const consensus = document.querySelector("#consensus-cards");
    if (!state.article || !summary || !conflicts || !lowConfidence || !consensus) return;
    summary.textContent = `当前草稿有 ${state.article.projects.length} 个项目（结构版本 ${state.structureRevision}）。`;
    if (!state.structureAccepted) return;
    document.querySelector('[data-action="accept-structure"]').disabled = true;
    const conflictKeys = new Set((state.payload.conflicts || []).filter((item) => item.project_index !== null)
      .map((item) => `${state.article.projects[item.project_index]?.gold_project_id}.${item.field_name}`));
    const entries = state.article.projects.flatMap((project) => Object.keys(project.fields).map((name) => [`${project.gold_project_id}.${name}`, `${project.gold_project_id} · ${name}`]));
    const conflictCards = [];
    const lowCards = [];
    const consensusCards = [];
    for (const [path, title] of entries) {
      if (conflictKeys.has(path)) conflictCards.push(fieldCard(path, title, "conflict"));
      else if (!suggestedField(path)) lowCards.push(fieldCard(path, title, "low-confidence"));
      else consensusCards.push(fieldCard(path, title, "consensus"));
    }
    conflicts.replaceChildren(...(conflictCards.length ? conflictCards : [message("没有字段冲突；请抽检折叠的一致候选。")]));
    lowConfidence.replaceChildren(...(lowCards.length ? lowCards : [message("没有缺候选字段。")]));
    consensus.replaceChildren(...consensusCards);
    document.querySelector("#consensus-summary").textContent = `已折叠 ${consensusCards.length} 个有建议的字段。`;
    renderEvidence();
  }

  /** Create a plain explanatory paragraph for empty review sections. */
  function message(text) {
    const paragraph = document.createElement("p");
    paragraph.textContent = text;
    return paragraph;
  }

  /** Fetch the initial draft, record the local review start, and leave all failed loads visible. */
  async function loadReview() {
    try {
      const payload = await request(`/api/articles/${encodeURIComponent(state.sampleId)}`);
      applyArticle(payload);
      await request(`/api/articles/${encodeURIComponent(state.sampleId)}/start`, { method: "POST" });
      setSaveState("草稿已加载", "saved");
    } catch (error) {
      setSaveState(`Save failed: ${error.message}`, "error", loadReview);
    }
  }

  /** Open the field panel only after the reviewer explicitly acknowledges the project structure. */
  function acceptStructure() {
    state.structureAccepted = true;
    sessionStorage.setItem(`structure-gate:${state.sampleId}`, "accepted");
    renderReview();
  }

  /** Replace the structure with no projects and then confirm only after both server calls succeed. */
  async function confirmNoProject() {
    const run = async () => {
      setSaveState("正在保存负样本…", "pending");
      const structure = await request(`/api/articles/${encodeURIComponent(state.sampleId)}/structure`, {
        method: "PUT",
        body: JSON.stringify({ base_revision: state.structureRevision, projects: [] }),
      });
      applyArticle(structure);
      const confirmed = await request(`/api/articles/${encodeURIComponent(state.sampleId)}/confirm`, { method: "POST" });
      applyArticle(confirmed);
      setSaveState("已确认负样本", "saved");
    };
    try { await run(); } catch (error) { setSaveState(`Save failed: ${error.message}`, "error", confirmNoProject); }
  }

  /** Confirm this article strictly from the authoritative server response. */
  async function confirmArticle() {
    const button = document.querySelector('[data-action="confirm-article"]');
    const run = async () => {
      button.disabled = true;
      setSaveState("正在确认…", "pending");
      const payload = await request(`/api/articles/${encodeURIComponent(state.sampleId)}/confirm`, { method: "POST" });
      applyArticle(payload);
      setSaveState("文章已确认", "saved");
    };
    try { await run(); } catch (error) { setSaveState(`Save failed: ${error.message}`, "error", confirmArticle); }
    finally { button.disabled = false; }
  }

  /** Route review shortcuts unless focus is inside a control that accepts text or selection input. */
  function bindShortcuts() {
    document.addEventListener("keydown", (event) => {
      const tag = document.activeElement?.tagName?.toLowerCase();
      if (["input", "textarea", "select"].includes(tag) || document.activeElement?.isContentEditable) return;
      const firstCard = document.querySelector(".field-card");
      if (event.key.toLowerCase() === "a") firstCard?.querySelector('[data-action="accept-field"]')?.click();
      if (event.key.toLowerCase() === "e") firstCard?.querySelector('[data-action="edit-field"]')?.click();
      if (event.key.toLowerCase() === "k") firstCard?.querySelector('[data-action="keep-database"]')?.click();
      if (event.key.toLowerCase() === "x") firstCard?.querySelector('[data-action="flag-evidence"]')?.click();
      if (event.key === "Enter") confirmArticle();
    });
  }

  /** Populate the small standalone queue page through the safe queue API. */
  async function loadQueue() {
    const list = document.querySelector("#queue-items");
    if (!list) return;
    try {
      const payload = await request("/api/queue");
      list.replaceChildren(...payload.items.map((item) => {
        const row = document.createElement("li");
        const link = document.createElement("a");
        link.href = `/review/${encodeURIComponent(item.sample_id)}`;
        link.textContent = `${item.sample_id} · 难度 ${item.difficulty_score}`;
        row.append(link);
        return row;
      }));
      document.querySelector("#queue-status").textContent = `共 ${payload.items.length} 篇待复核。`;
    } catch (error) {
      document.querySelector("#queue-status").textContent = `队列加载失败：${error.message}`;
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    document.querySelector('[data-action="accept-structure"]')?.addEventListener("click", acceptStructure);
    document.querySelector('[data-action="confirm-no-project"]')?.addEventListener("click", confirmNoProject);
    document.querySelector('[data-action="confirm-article"]')?.addEventListener("click", confirmArticle);
    document.querySelector('[data-action="expand-consensus"]')?.addEventListener("click", (event) => {
      const cards = document.querySelector("#consensus-cards");
      cards.hidden = !cards.hidden;
      event.currentTarget.textContent = cards.hidden ? "展开" : "折叠";
    });
    bindShortcuts();
    if (state.sampleId) loadReview(); else loadQueue();
  });
})();
