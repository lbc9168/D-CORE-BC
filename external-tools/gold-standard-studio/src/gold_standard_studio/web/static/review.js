/* Render the local Studio review workflow from server-aligned candidates and persisted decisions only. */

(() => {
  "use strict";
  const bootstrap = document.querySelector("#review-bootstrap");
  const boot = bootstrap ? JSON.parse(bootstrap.textContent) : null;
  const state = { sampleId: boot?.sampleId, articleContext: boot?.article, article: null, payload: null, structure: [], retry: null, timers: new Map(), focusedCard: null };

  /** Display primitive or JSON review values without interpreting candidate content as markup. */
  function displayValue(value) { return value === null || value === undefined || value === "" ? "未提供" : typeof value === "string" ? value : JSON.stringify(value); }

  /** Encode the complete server field path before it is inserted into a same-origin API URL. */
  function fieldUrl(path) { return `/api/articles/${encodeURIComponent(state.sampleId)}/fields/${encodeURIComponent(path)}`; }

  /** Publish pending, saved, or failed state with a retry affordance for the failed operation. */
  function setSaveState(message, kind = "ready", retry = null) {
    const output = document.querySelector("#save-state");
    if (!output) return;
    output.className = `save-state ${kind}`; output.textContent = message; state.retry = retry;
    let button = document.querySelector("#retry-save");
    if (retry && !button) { button = document.createElement("button"); button.id = "retry-save"; button.type = "button"; button.textContent = "重试保存"; button.addEventListener("click", () => state.retry?.()); output.after(button); }
    if (!retry && button) button.remove();
  }

  /** Send one explicit request and surface only a server-confirmed JSON response to the renderer. */
  async function request(url, options = {}) {
    const response = await fetch(url, { headers: { "Content-Type": "application/json", "X-Review-Actor": "local-reviewer" }, ...options });
    if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail || response.statusText);
    return response.json();
  }

  /** Replace all client review state with the latest authoritative API article payload. */
  function applyArticle(payload) { state.article = payload.article; state.payload = payload; state.structure = structuredClone(payload.projects); renderReview(); }

  /** Return the authoritative registry rule for one project field path. */
  function fieldRule(path) { const fieldName = path.split(".")[1]; return state.payload?.field_registry?.[fieldName] || { evidence_required: false }; }

  /** Normalize candidate or editor data into the formal registry contract before persistence. */
  function formalField(path, field) { return { ...field, evidence_required: Boolean(fieldRule(path).evidence_required) }; }

  /** Persist one aligned field decision through the dedicated adjudication action when applicable. */
  async function saveField(path, field) {
    const previous = currentField(path); const replacement = formalField(path, field); const adjudicating = previous?.state === "needs_adjudication" && replacement.state !== "needs_adjudication";
    const run = async () => { setSaveState("正在保存…", "pending"); const url = adjudicating ? `/api/articles/${encodeURIComponent(state.sampleId)}/adjudication` : fieldUrl(path); const body = adjudicating ? { field_path: path, field: replacement } : { field: replacement }; applyArticle(await request(url, { method: adjudicating ? "POST" : "PUT", body: JSON.stringify(body) })); setSaveState("草稿已保存", "saved"); };
    try { await run(); } catch (error) { setSaveState(`Save failed: ${error.message}`, "error", () => saveField(path, field)); }
  }

  /** Persist the complete ordered structure at the server revision currently represented by state. */
  async function saveStructure() {
    const run = async () => { setSaveState("正在保存项目结构…", "pending"); applyArticle(await request(`/api/articles/${encodeURIComponent(state.sampleId)}/structure`, { method: "PUT", body: JSON.stringify({ base_revision: state.payload.structure_revision, projects: state.structure }) })); setSaveState("项目结构已保存", "saved"); };
    try { await run(); } catch (error) { setSaveState(`Save failed: ${error.message}`, "error", saveStructure); }
  }

  /** Return the human field at a stable gold-project field path. */
  function currentField(path) { const [projectId, fieldName] = path.split("."); return state.article?.projects.find((project) => project.gold_project_id === projectId)?.fields[fieldName] || null; }

  /** Return only human evidence spans explicitly tied to the immutable article currently under review. */
  function currentArticleEvidence(field) { return (field?.evidence || []).filter((span) => span.article_sha256 === state.article?.article_sha256); }

  /** Report whether a human field carries any evidence excluded from the current article snapshot. */
  function hasStaleHumanEvidence(field) { return (field?.evidence || []).some((span) => span.article_sha256 !== state.article?.article_sha256); }

  /** Return a server-aligned source candidate rather than indexing into any source project list in JavaScript. */
  function sourceCandidate(path, sourceName = null) {
    const candidates = state.payload?.field_candidates?.[path] || [];
    if (sourceName) return candidates.find((candidate) => candidate.source_name === sourceName && candidate.field) || null;
    return ["review", "production", "deterministic", "database"].map((name) => sourceCandidate(path, name)).find((candidate) => candidate?.field?.state === "present" && !candidate.evidence_warning) || null;
  }

  /** Build provenance rows directly from the server projection, including unmatched or errored source states. */
  function provenanceRows(path) {
    const rows = [["人工草稿（review DB）", currentField(path), null]];
    for (const candidate of state.payload?.field_candidates?.[path] || []) rows.push([`${candidate.source_name} 候选`, candidate.field, candidate]);
    return rows.map(([label, field, candidate]) => { const item = document.createElement("li"); const suffix = candidate?.source_project_id ? `，来源项目 ${candidate.source_project_id}` : candidate?.source_status === "error" ? "，来源失败" : "，无安全对齐项目"; const warning = candidate?.evidence_warning ? "；证据哈希不匹配，不能接受" : ""; item.textContent = `${label}：${displayValue(field?.value)}（${field?.state || "未提供"}${suffix || ""}${warning}）`; return item; });
  }

  /** Track the active card so keyboard actions cannot silently target a different field. */
  function bindCardFocus(card) { card.addEventListener("focusin", () => { state.focusedCard = card; }); card.addEventListener("pointerdown", () => { state.focusedCard = card; }); card.addEventListener("focusout", (event) => { if (!card.contains(event.relatedTarget)) state.focusedCard = null; }); }

  /** Render a field card with actions sourced exclusively from the server-aligned field projection. */
  function fieldCard(path, title, kind) {
    const field = currentField(path); const card = document.createElement("section"); card.className = `field-card ${kind}`; card.dataset.path = path; card.tabIndex = 0; bindCardFocus(card);
    const heading = document.createElement("h3"); heading.textContent = title;
    const value = document.createElement("p"); value.className = "field-value"; value.dataset.testid = `value-${path}`; value.textContent = displayValue(field?.value);
    const humanEvidenceWarning = hasStaleHumanEvidence(field) ? message("人工草稿证据哈希不匹配，已从当前文章高亮中排除，不能作为本篇确认依据。") : null;
    if (humanEvidenceWarning) { humanEvidenceWarning.className = "human-evidence-warning"; humanEvidenceWarning.dataset.testid = `human-evidence-warning-${path}`; }
    const provenance = document.createElement("ul"); provenance.className = "provenance"; provenance.setAttribute("aria-label", `${title} 的候选来源`); provenance.replaceChildren(...provenanceRows(path));
    const controls = document.createElement("div"); controls.className = "field-controls";
    const suggested = sourceCandidate(path);
    if (suggested?.field?.state === "present") { const accept = document.createElement("button"); accept.type = "button"; accept.dataset.action = "accept-field"; accept.dataset.path = path; accept.dataset.testid = `accept-${path}`; accept.textContent = `接受建议：${displayValue(suggested.field.value)}`; accept.addEventListener("click", () => saveField(path, suggested.field)); controls.append(accept); }
    const keep = document.createElement("button"); keep.type = "button"; keep.className = "secondary"; keep.dataset.action = "keep-database"; keep.textContent = "保留数据库值"; const database = sourceCandidate(path, "database"); keep.disabled = !database?.field || database.evidence_warning; keep.addEventListener("click", () => database?.field && !database.evidence_warning && saveField(path, database.field));
    const edit = document.createElement("button"); edit.type = "button"; edit.className = "secondary"; edit.dataset.action = "edit-field"; edit.dataset.testid = `edit-${path}`; edit.textContent = "编辑"; edit.addEventListener("click", () => openEditor(path, card));
    const flag = document.createElement("button"); flag.type = "button"; flag.className = "secondary"; flag.dataset.action = "flag-evidence"; flag.textContent = "证据有误"; flag.addEventListener("click", () => field && saveField(path, { ...field, state: "needs_adjudication", value: null, evidence: [] }));
    controls.append(keep, edit, flag); card.append(heading, value, ...(humanEvidenceWarning ? [humanEvidenceWarning] : []), provenance, controls); return card;
  }

  /** Open a field editor for repeated exact evidence spans and a single formal decision. */
  function openEditor(path, card) {
    if (card.querySelector("input")) return; const field = currentField(path); const wrapper = document.createElement("div"); wrapper.className = "field-editor";
    const stateSelect = document.createElement("select"); stateSelect.dataset.testid = `state-${path}`; for (const name of ["unreviewed", "present", "not_mentioned", "not_applicable", "uncertain", "needs_adjudication"]) { const option = document.createElement("option"); option.value = name; option.textContent = name; stateSelect.append(option); } stateSelect.value = field?.state || "unreviewed";
    const input = document.createElement("input"); input.type = "text"; input.value = field?.value === null || field?.value === undefined ? "" : typeof field.value === "string" ? field.value : JSON.stringify(field.value); input.dataset.testid = `editor-${path}`; input.setAttribute("aria-label", `编辑 ${path} 值`);
    const evidenceDraft = structuredClone(currentArticleEvidence(field)); const evidenceList = document.createElement("div"); evidenceList.className = "evidence-editors";
    const redrawEvidence = () => { evidenceList.replaceChildren(...evidenceDraft.map((span, index) => { const row = document.createElement("div"); const quote = document.createElement("textarea"); quote.dataset.testid = `evidence-quote-${path}-${index}`; quote.placeholder = "原文精确证据 quote"; quote.value = span.quote || ""; quote.addEventListener("input", () => { span.quote = quote.value; }); const start = document.createElement("input"); start.type = "number"; start.min = "0"; start.dataset.testid = `evidence-start-${path}-${index}`; start.placeholder = "start"; start.value = span.start ?? ""; start.addEventListener("input", () => { span.start = Number(start.value); }); const remove = document.createElement("button"); remove.type = "button"; remove.className = "secondary"; remove.dataset.testid = `remove-evidence-${path}-${index}`; remove.textContent = "移除证据"; remove.addEventListener("click", () => { evidenceDraft.splice(index, 1); redrawEvidence(); }); row.append(quote, start, remove); return row; })); };
    const addEvidence = document.createElement("button"); addEvidence.type = "button"; addEvidence.className = "secondary"; addEvidence.dataset.testid = `add-evidence-${path}`; addEvidence.textContent = "新增证据"; addEvidence.addEventListener("click", () => { evidenceDraft.push({ quote: "", start: 0 }); redrawEvidence(); });
    redrawEvidence();
    const save = document.createElement("button"); save.type = "button"; save.dataset.testid = `save-editor-${path}`; save.textContent = "保存字段与证据"; save.addEventListener("click", () => { const evidence = evidenceDraft.filter((span) => span.quote).map((span) => ({ quote: span.quote, start: Number(span.start), end: Number(span.start) + span.quote.length, article_sha256: state.article.article_sha256, source: "article" })); saveField(path, { state: stateSelect.value, value: stateSelect.value === "present" ? parseEditorValue(input.value) : null, evidence }); });
    wrapper.append(stateSelect, input, evidenceList, addEvidence, save); card.querySelector(".field-controls").before(wrapper); input.focus();
  }

  /** Parse a human JSON scalar/list when possible while retaining ordinary text exactly. */
  function parseEditorValue(value) { const trimmed = value.trim(); if (!trimmed) return null; if (/^-?\d+(?:\.\d+)?$/.test(trimmed)) return Number(trimmed); if (trimmed.startsWith("[")) { try { return JSON.parse(trimmed); } catch (_error) { return trimmed; } } return trimmed; }

  /** Persist the explicit article gold status separately from review workflow state. */
  async function saveArticleStatus() { const select = document.querySelector("#article-status"); if (!select || select.value === "unreviewed") return; try { setSaveState("正在保存文章状态…", "pending"); applyArticle(await request(`/api/articles/${encodeURIComponent(state.sampleId)}/status`, { method: "PUT", body: JSON.stringify({ article_status: select.value }) })); setSaveState("文章状态已保存", "saved"); } catch (error) { setSaveState(`Save failed: ${error.message}`, "error", saveArticleStatus); } }

  /** Render full-list Chinese entity editors with controlled roles and repeated exact evidence spans. */
  function renderEntities() { const target = document.querySelector("#entity-editors"); if (!target || !state.article) return; target.replaceChildren(...state.article.projects.map((project) => { const section = document.createElement("div"); section.className = "entity-editor"; const heading = document.createElement("h3"); heading.textContent = project.gold_project_id; const draft = structuredClone(project.entities || []); const list = document.createElement("div"); const redraw = () => { list.replaceChildren(...draft.map((entity, index) => { const row = document.createElement("div"); const name = document.createElement("input"); name.value = entity.name || ""; name.placeholder = "中方实体"; name.dataset.testid = `entity-name-${project.gold_project_id}-${index}`; name.addEventListener("input", () => { entity.name = name.value; }); const role = document.createElement("select"); role.dataset.testid = `entity-role-${project.gold_project_id}-${index}`; for (const roleName of state.payload.entity_roles || []) { const option = document.createElement("option"); option.value = roleName; option.textContent = roleName; role.append(option); } role.value = entity.role || "Unknown"; role.addEventListener("change", () => { entity.role = role.value; }); const evidenceDraft = entity.evidence || []; const evidenceList = document.createElement("div"); const redrawEvidence = () => { evidenceList.replaceChildren(...evidenceDraft.map((span, evidenceIndex) => { const evidenceRow = document.createElement("div"); const quote = document.createElement("input"); quote.value = span.quote || ""; quote.placeholder = "实体与角色原文证据"; quote.dataset.testid = `entity-evidence-quote-${project.gold_project_id}-${index}-${evidenceIndex}`; quote.addEventListener("input", () => { span.quote = quote.value; }); const start = document.createElement("input"); start.type = "number"; start.min = "0"; start.value = span.start ?? ""; start.dataset.testid = `entity-evidence-start-${project.gold_project_id}-${index}-${evidenceIndex}`; start.addEventListener("input", () => { span.start = Number(start.value); }); const removeEvidence = document.createElement("button"); removeEvidence.type = "button"; removeEvidence.className = "secondary"; removeEvidence.textContent = "移除证据"; removeEvidence.addEventListener("click", () => { evidenceDraft.splice(evidenceIndex, 1); redrawEvidence(); }); evidenceRow.append(quote, start, removeEvidence); return evidenceRow; })); }; redrawEvidence(); const addEvidence = document.createElement("button"); addEvidence.type = "button"; addEvidence.className = "secondary"; addEvidence.dataset.testid = `add-entity-evidence-${project.gold_project_id}-${index}`; addEvidence.textContent = "新增实体证据"; addEvidence.addEventListener("click", () => { evidenceDraft.push({ quote: "", start: 0 }); redrawEvidence(); }); const removeEntity = document.createElement("button"); removeEntity.type = "button"; removeEntity.className = "secondary"; removeEntity.dataset.testid = `remove-entity-${project.gold_project_id}-${index}`; removeEntity.textContent = "移除实体"; removeEntity.addEventListener("click", () => { draft.splice(index, 1); redraw(); }); row.append(name, role, evidenceList, addEvidence, removeEntity); return row; })); }; redraw(); const add = document.createElement("button"); add.type = "button"; add.className = "secondary"; add.dataset.testid = `add-entity-${project.gold_project_id}`; add.textContent = "新增中方实体"; add.addEventListener("click", () => { draft.push({ name: "", role: "Unknown", normalized_name: null, evidence: [] }); redraw(); }); const save = document.createElement("button"); save.type = "button"; save.dataset.testid = `save-entities-${project.gold_project_id}`; save.textContent = "保存实体"; save.addEventListener("click", async () => { const entities = draft.map((entity) => ({ ...entity, evidence: (entity.evidence || []).filter((span) => span.quote).map((span) => ({ quote: span.quote, start: Number(span.start), end: Number(span.start) + span.quote.length, article_sha256: state.article.article_sha256, source: "article" })) })); try { applyArticle(await request(`/api/articles/${encodeURIComponent(state.sampleId)}/projects/${encodeURIComponent(project.gold_project_id)}/entities`, { method: "PUT", body: JSON.stringify({ entities }) })); setSaveState("实体已保存", "saved"); } catch (error) { setSaveState(`Save failed: ${error.message}`, "error"); } }); section.append(heading, list, add, save); return section; })); }

  /** Draw non-overlapping human evidence spans from the immutable article source text. */
  function renderEvidence() {
    const target = document.querySelector("#article-content"); const text = state.articleContext?.content; if (!target || !text || !state.article) return;
    const spans = state.article.projects.flatMap((project) => Object.values(project.fields).flatMap(currentArticleEvidence)).filter((span) => span.end <= text.length && text.slice(span.start, span.end) === span.quote).sort((a, b) => a.start - b.start || a.end - b.end);
    const nodes = []; let cursor = 0; for (const span of spans) { if (span.start < cursor) continue; nodes.push(document.createTextNode(text.slice(cursor, span.start))); const mark = document.createElement("mark"); mark.textContent = text.slice(span.start, span.end); nodes.push(mark); cursor = span.end; } nodes.push(document.createTextNode(text.slice(cursor))); target.replaceChildren(...nodes);
  }

  /** Render structural conflict diagnostics, including count and unmatchable-project conflicts with no project index. */
  function renderStructuralConflicts() {
    const target = document.querySelector("#structural-conflicts"); if (!target) return; const conflicts = state.payload?.structural_conflicts || [];
    target.replaceChildren(...(conflicts.length ? conflicts.map((conflict) => { const item = document.createElement("p"); item.className = "structural-conflict"; item.textContent = `${conflict.reason_code}：${Object.entries(conflict.source_values).map(([source, value]) => `${source}=${displayValue(value)}`).join("；")}`; return item; }) : [message("候选未报告结构冲突。") ]));
  }

  /** Render an ordered project editor for rename, add/split, remove, and merge operations before full-list persistence. */
  function renderStructureEditor() {
    const target = document.querySelector("#structure-editor"); if (!target) return; target.replaceChildren(...state.structure.map((project, index) => {
      const row = document.createElement("div"); row.className = "structure-row"; const label = document.createElement("label"); label.textContent = `${project.gold_project_id} 名称`; const input = document.createElement("input"); input.value = project.fields?.project_name?.value || ""; input.dataset.testid = `structure-name-${project.gold_project_id}`; input.addEventListener("input", () => { if (!project.fields.project_name) project.fields.project_name = { state: "present", value: "", evidence: [], evidence_required: Boolean(fieldRule(`${project.gold_project_id}.project_name`).evidence_required) }; project.fields.project_name.value = input.value; project.fields.project_name.state = input.value.trim() ? "present" : "not_mentioned"; });
      const remove = document.createElement("button"); remove.type = "button"; remove.className = "secondary"; remove.textContent = "移除"; remove.addEventListener("click", () => { state.structure.splice(index, 1); renderStructureEditor(); });
      const split = document.createElement("button"); split.type = "button"; split.className = "secondary"; split.textContent = "拆分"; split.addEventListener("click", () => { const clone = structuredClone(project); clone.gold_project_id = `NEW-${Date.now()}`; state.structure.splice(index + 1, 0, clone); renderStructureEditor(); });
      const merge = document.createElement("button"); merge.type = "button"; merge.className = "secondary"; merge.textContent = "与上一项合并"; merge.disabled = index === 0; merge.addEventListener("click", () => { if (index) { const previous = state.structure[index - 1]; Object.assign(previous.fields, Object.fromEntries(Object.entries(project.fields).filter(([name]) => !previous.fields[name]))); state.structure.splice(index, 1); renderStructureEditor(); } });
      label.append(input); row.append(label, remove, split, merge); return row;
    }));
  }

  /** Render conflict-first cards only once a persisted server structure decision clears the gate. */
  function renderReview() {
    if (!state.article || !state.payload) return; const gate = state.payload.structure_gate; const summary = document.querySelector("#structure-summary"); summary.textContent = `当前草稿有 ${state.article.projects.length} 个项目（结构版本 ${state.payload.structure_revision}）。${gate.required ? (gate.resolved ? " 已持久化解决结构冲突。" : " 必须保存结构决定后才能确认。"): ""}`;
    const articleStatus = document.querySelector("#article-status"); if (articleStatus) articleStatus.value = state.article.article_status;
    renderStructuralConflicts(); renderStructureEditor(); const confirm = document.querySelector('[data-action="confirm-article"]'); confirm.disabled = !gate.resolved; confirm.title = gate.resolved ? "" : "请先保存项目结构决定";
    const conflicts = document.querySelector("#conflict-cards"), low = document.querySelector("#low-confidence-cards"), consensus = document.querySelector("#consensus-cards"); if (!gate.resolved) { conflicts.replaceChildren(message("请先通过服务器保存项目结构决定。")); low.replaceChildren(); consensus.replaceChildren(); renderEvidence(); return; }
    const conflictKeys = new Set((state.payload.conflicts || []).filter((item) => item.project_index !== null).map((item) => `${state.article.projects[item.project_index]?.gold_project_id}.${item.field_name}`)); const entries = state.article.projects.flatMap((project) => Object.keys(project.fields).map((name) => [`${project.gold_project_id}.${name}`, `${project.gold_project_id} · ${name}`])); const conflictCards = [], lowCards = [], consensusCards = [];
    for (const [path, title] of entries) { if (conflictKeys.has(path)) conflictCards.push(fieldCard(path, title, "conflict")); else if (!sourceCandidate(path)) lowCards.push(fieldCard(path, title, "low-confidence")); else consensusCards.push(fieldCard(path, title, "consensus")); }
    conflicts.replaceChildren(...(conflictCards.length ? conflictCards : [message("没有字段冲突；请抽检折叠的一致候选。")])); low.replaceChildren(...(lowCards.length ? lowCards : [message("没有缺候选字段。")])); consensus.replaceChildren(...consensusCards); document.querySelector("#consensus-summary").textContent = `已折叠 ${consensusCards.length} 个有建议的字段。`; renderEntities(); renderEvidence();
  }

  /** Create a plain explanatory message for an empty review region. */
  function message(text) { const paragraph = document.createElement("p"); paragraph.textContent = text; return paragraph; }

  /** Load the review draft and durable resume event from the existing API. */
  async function loadReview() { try { applyArticle(await request(`/api/articles/${encodeURIComponent(state.sampleId)}`)); await request(`/api/articles/${encodeURIComponent(state.sampleId)}/start`, { method: "POST" }); setSaveState("草稿已加载", "saved"); } catch (error) { setSaveState(`Save failed: ${error.message}`, "error", loadReview); } }

  /** Save the current full structure as the explicit persisted acknowledgement of the structure gate. */
  function acceptStructure() { saveStructure(); }

  /** Set one selected negative status and confirm it through a single atomic server gate. */
  async function confirmNoProject() { const status = document.querySelector("#article-status")?.value; if (!["no_chinese", "irrelevant", "chinese_unnamed"].includes(status)) { setSaveState("Save failed: 请选择明确负样本状态", "error"); return; } try { setSaveState("正在确认负样本…", "pending"); applyArticle(await request(`/api/articles/${encodeURIComponent(state.sampleId)}/confirm-negative`, { method: "POST", body: JSON.stringify({ article_status: status }) })); setSaveState("负样本已确认", "saved"); } catch (error) { setSaveState(`Save failed: ${error.message}`, "error", confirmNoProject); } }

  /** Confirm only through the server and never invoke it while a persisted structure gate remains unresolved. */
  async function confirmArticle() { if (!state.payload?.structure_gate?.resolved) return; const button = document.querySelector('[data-action="confirm-article"]'); try { button.disabled = true; setSaveState("正在确认…", "pending"); applyArticle(await request(`/api/articles/${encodeURIComponent(state.sampleId)}/confirm`, { method: "POST" })); setSaveState("文章已确认", "saved"); } catch (error) { setSaveState(`Save failed: ${error.message}`, "error", confirmArticle); } finally { button.disabled = !state.payload?.structure_gate?.resolved; } }

  /** Invoke a shortcut on the focused card only and ignore every editable control. */
  function bindShortcuts() { document.addEventListener("pointerdown", (event) => { if (!event.target.closest(".field-card")) state.focusedCard = null; }); document.addEventListener("keydown", (event) => { if (event.ctrlKey || event.metaKey || event.altKey || event.repeat) return; const tag = document.activeElement?.tagName?.toLowerCase(); if (["input", "textarea", "select"].includes(tag) || document.activeElement?.isContentEditable) return; const card = document.activeElement?.closest(".field-card") || state.focusedCard; if (event.key.toLowerCase() === "a") card?.querySelector('[data-action="accept-field"]')?.click(); if (event.key.toLowerCase() === "e") card?.querySelector('[data-action="edit-field"]')?.click(); if (event.key.toLowerCase() === "k") card?.querySelector('[data-action="keep-database"]')?.click(); if (event.key.toLowerCase() === "x") card?.querySelector('[data-action="flag-evidence"]')?.click(); if (event.key === "Enter" && state.payload?.structure_gate?.resolved) confirmArticle(); }); }

  /** Fetch and render the existing audit endpoint only when the reviewer expands the collapsed panel. */
  async function toggleAudit(event) { const list = document.querySelector("#audit-items"); if (!list.hidden) { list.hidden = true; event.currentTarget.textContent = "展开"; return; } const payload = await request(`/api/articles/${encodeURIComponent(state.sampleId)}/audit`); list.replaceChildren(...payload.items.map((entry) => { const item = document.createElement("li"); item.textContent = `${entry.created_at} · ${entry.action} · ${entry.field_path || "文章"}`; return item; })); list.hidden = false; event.currentTarget.textContent = "折叠"; }

  /** Load the local queue from its explicit API without adding a second client-side persistence layer. */
  async function loadQueue() { const list = document.querySelector("#queue-items"); if (!list) return; try { const payload = await request("/api/queue"); list.replaceChildren(...payload.items.map((item) => { const row = document.createElement("li"), link = document.createElement("a"); link.href = `/review/${encodeURIComponent(item.sample_id)}`; link.textContent = `${item.sample_id} · 难度 ${item.difficulty_score}`; row.append(link); return row; })); document.querySelector("#queue-status").textContent = `共 ${payload.items.length} 篇待复核。`; } catch (error) { document.querySelector("#queue-status").textContent = `队列加载失败：${error.message}`; } }

  document.addEventListener("DOMContentLoaded", () => { document.querySelector('[data-action="accept-structure"]')?.addEventListener("click", acceptStructure); document.querySelector('[data-action="add-project"]')?.addEventListener("click", () => { state.structure.push({ gold_project_id: `NEW-${Date.now()}`, fields: {}, entities: [] }); renderStructureEditor(); }); document.querySelector('[data-action="save-structure"]')?.addEventListener("click", saveStructure); document.querySelector('[data-action="save-article-status"]')?.addEventListener("click", saveArticleStatus); document.querySelector('[data-action="confirm-no-project"]')?.addEventListener("click", confirmNoProject); document.querySelector('[data-action="confirm-article"]')?.addEventListener("click", confirmArticle); document.querySelector('[data-action="expand-consensus"]')?.addEventListener("click", (event) => { const cards = document.querySelector("#consensus-cards"); cards.hidden = !cards.hidden; event.currentTarget.textContent = cards.hidden ? "展开" : "折叠"; }); document.querySelector('[data-action="expand-audit"]')?.addEventListener("click", toggleAudit); bindShortcuts(); if (state.sampleId) loadReview(); else loadQueue(); });
})();
