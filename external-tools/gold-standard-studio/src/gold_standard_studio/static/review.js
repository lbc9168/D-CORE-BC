/* Drive the cohort article conflict-first review UI and lenient autosave workflow. */

const boot = JSON.parse(document.getElementById("bootstrap").textContent);
const articleId = boot.article.id;
const fieldDefs = boot.field_definitions;
const derivedFields = new Set(boot.derived_field_names || []);
const candidateSources = boot.candidate_sources;
let state = structuredClone(boot.draft.payload);
let articleConfirmed = Boolean(boot.draft.confirmed);
let saveTimer = null;
let saving = false;

/** Escape user and model text before inserting it into generated HTML. */
function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"})[char]);
}

/** Convert arbitrary candidate values to stable readable strings. */
function displayValue(value) {
  if (value === null || value === undefined || value === "") return "未提及";
  return typeof value === "object" ? JSON.stringify(value, null, 2) : String(value);
}

/** Read the common field envelope from a source project at the current project index. */
function candidateField(source, projectIndex, fieldName) {
  const project = (candidateSources[source] || [])[projectIndex];
  return project ? project[fieldName] : null;
}

/** Decide whether candidate sources disagree for one field. */
function hasConflict(projectIndex, fieldName) {
  const numericFields = new Set(["storage_power_mw", "storage_energy_mwh", "generation_capacity_mw"]);
  const values = Object.keys(candidateSources)
    .map(source => candidateField(source, projectIndex, fieldName))
    .filter(Boolean)
    .map(field => numericFields.has(fieldName) && field.value !== null && field.value !== ""
      ? JSON.stringify(Number(field.value)) : JSON.stringify(field.value ?? null));
  return new Set(values).size > 1;
}

/** Schedule a lenient draft write while retaining browser inputs on failure. */
function queueSave() {
  if (articleConfirmed) return;
  document.getElementById("save-status").textContent = "等待保存…";
  clearTimeout(saveTimer);
  saveTimer = setTimeout(saveDraft, 350);
}

/** Persist the complete current draft without final validation. */
async function saveDraft() {
  if (articleConfirmed) return;
  if (saving) return queueSave();
  saving = true;
  document.getElementById("save-status").textContent = "正在保存…";
  try {
    const response = await fetch(`/api/review/${articleId}/draft`, {
      method: "PUT", headers: {"Content-Type": "application/json"}, body: JSON.stringify(state)
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "保存失败");
    document.getElementById("save-status").textContent = `草稿已保存 · r${result.revision}`;
    showError("");
  } catch (error) {
    document.getElementById("save-status").textContent = "保存失败";
    showError(`草稿未提交：${error.message}。页面输入仍保留，可继续修改或重试。`);
  } finally {
    saving = false;
  }
}

/** Show or clear the prominent actionable error banner. */
function showError(message) {
  const banner = document.getElementById("error-banner");
  banner.textContent = message;
  banner.classList.toggle("hidden", !message);
}

/** Highlight an exact evidence quote in the immutable article body. */
function highlightEvidence(quote) {
  if (!quote) return showError("该候选没有可定位的原文证据。");
  const title = boot.article.title || "";
  const body = boot.article.content;
  const fragments = quote.split(/[；;。\n]/).map(fragment => fragment.trim()).filter(Boolean);
  const exactQuote = title.includes(quote) || body.includes(quote)
    ? quote
    : fragments.find(fragment => title.includes(fragment) || body.includes(fragment));
  const source = exactQuote && title.includes(exactQuote) ? "title" : "body";
  const sourceText = source === "title" ? title : body;
  const index = exactQuote ? sourceText.indexOf(exactQuote) : -1;
  if (index < 0) return showError(`证据无法在当前文章中定位：${quote}`);
  const titleTarget = document.getElementById("article-title");
  const bodyTarget = document.getElementById("article-content");
  titleTarget.innerHTML = escapeHtml(title);
  bodyTarget.innerHTML = escapeHtml(body);
  const target = source === "title" ? titleTarget : bodyTarget;
  target.innerHTML = `${escapeHtml(sourceText.slice(0, index))}<mark>${escapeHtml(exactQuote)}</mark>${escapeHtml(sourceText.slice(index + exactQuote.length))}`;
  target.querySelector("mark").scrollIntoView({behavior: "auto", block: "center"});
  state.last_evidence = quote;
  queueSave();
  showError("");
}

/** Replace a draft field with a selected source candidate and preserve provenance. */
function acceptCandidate(projectIndex, fieldName, source) {
  const candidate = candidateField(source, projectIndex, fieldName);
  if (!candidate) return;
  state.projects[projectIndex].fields[fieldName] = {
    value_state: candidate.value_state || "present", value: candidate.value ?? null,
    evidence: candidate.evidence || "", confidence: candidate.confidence ?? null,
    review_action: "confirmed", accepted_source: source
  };
  render();
  queueSave();
}

/** Render one candidate comparison row with evidence navigation. */
function candidateRow(source, projectIndex, fieldName) {
  const field = candidateField(source, projectIndex, fieldName);
  if (!field) return "";
  const labels = {database:"数据库聚合", pipeline:"当前 Pipeline", provider:"Terra Provider", reviewer:"Sol Reviewer", policy:"确定性 Policy"};
  const warning = source === "database" && candidateSources.database[projectIndex]?.cross_source_aggregate ? " · 跨来源" : "";
  return `<div class="candidate-row source-${source}">
    <div class="candidate-source">${labels[source] || source}${warning}</div>
    <div class="candidate-value">${escapeHtml(displayValue(field.value))}</div>
    <div class="candidate-actions"><button data-accept="${source}">采用</button>${field.evidence ? `<button data-evidence="${escapeHtml(field.evidence)}">证据</button>` : ""}</div>
  </div>`;
}

/** Render one editable field card, expanded first when candidate values conflict. */
function fieldCard(project, projectIndex, definition) {
  const [name, label, kind] = definition;
  const field = project.fields[name] || {value_state:"not_mentioned", value:null, evidence:"", review_action:"unreviewed"};
  const conflict = hasConflict(projectIndex, name);
  const derived = derivedFields.has(name);
  const value = kind === "json" && typeof field.value !== "string" ? JSON.stringify(field.value ?? [], null, 2) : (field.value ?? "");
  const input = kind === "json"
    ? `<textarea data-prop="value" rows="3">${escapeHtml(value)}</textarea>`
    : `<input data-prop="value" type="${kind === "number" ? "number" : "text"}" value="${escapeHtml(value)}">`;
  const rows = Object.keys(candidateSources).map(source => candidateRow(source, projectIndex, name)).join("");
  const evidencePlaceholder = field.value_state === "present"
    ? "标题或正文证据（present 状态在确认时必填）"
    : "当前状态无需标题/正文证据，可留空";
  const evidenceControls = derived
    ? `<textarea data-prop="evidence" rows="2" placeholder="归一化依据或登记册说明（草稿和确认均可为空）">${escapeHtml(field.evidence || "")}</textarea>`
    : `<p class="layer-hint">只有 present 事实在最终确认时要求标题或正文中的逐字证据；未提及、不适用和不确定可留空。</p>
       <textarea data-prop="evidence" rows="2" placeholder="${evidencePlaceholder}">${escapeHtml(field.evidence || "")}</textarea>
       <button class="locate-human" data-evidence="${escapeHtml(field.evidence || "")}">定位人工证据</button>`;
  const entityControls = name === "entities"
    ? `<p class="layer-hint">同一项目可加入多家中国企业；每家分别填写 name 与 role，多段原文证据可用分号分隔。</p><button data-add-entity="${projectIndex}">＋ 增加中国企业</button>`
    : "";
  return `<details class="field-card ${conflict ? "conflict" : "consistent"} ${derived ? "derived" : "fact"}" ${conflict || derived ? "open" : ""} data-field="${name}">
    <summary><div><span class="field-label">${escapeHtml(label)}</span><span class="field-key">${name}</span></div><div class="field-flags"><span class="layer-${derived ? "derived" : "fact"}">${derived ? "规则层" : "事实层"}</span>${conflict ? "冲突" : "候选一致"}<span class="action-${field.review_action}">${field.review_action}</span></div></summary>
    <div class="field-body"><div class="candidates">${rows}</div>
      <div class="human-decision"><strong>人工 Gold 草稿</strong>${derived ? `<p class="layer-hint">该值由登记册或确定性规则派生，不要求母公司或标准地名逐字出现在新闻中。</p>` : ""}${input}
        <div class="decision-grid"><select data-prop="value_state">
          ${["present","not_mentioned","not_applicable","uncertain","needs_adjudication"].map(option => `<option ${field.value_state === option ? "selected" : ""}>${option}</option>`).join("")}
        </select><select data-prop="review_action"><option ${field.review_action === "unreviewed" ? "selected" : ""}>unreviewed</option><option ${field.review_action === "confirmed" ? "selected" : ""}>confirmed</option><option ${field.review_action === "modified" ? "selected" : ""}>modified</option></select></div>
        ${entityControls}${evidenceControls}
      </div>
    </div></details>`;
}

/** Render the project tabs, field cards, and conflict summary from current state. */
function render() {
  document.getElementById("article-status").value = state.article_status || "";
  document.getElementById("non-project-reason").value = state.non_project_reason || "";
  document.getElementById("project-tabs").innerHTML = state.projects.map((project, index) =>
    `<div class="project-pill"><span>${project.gold_project_id}</span><input data-project-name="${index}" value="${escapeHtml(project.project_name || "")}"><button data-remove-project="${index}" title="合并/移除">×</button></div>`).join("");
  let conflictCount = 0;
  document.getElementById("projects").innerHTML = state.projects.map((project, projectIndex) => {
    conflictCount += fieldDefs.filter(def => hasConflict(projectIndex, def[0])).length;
    return `<section class="project-section"><div class="project-title"><span>${project.gold_project_id}</span><h3>${escapeHtml(project.project_name || `项目 ${projectIndex + 1}`)}</h3></div>${fieldDefs.map(def => fieldCard(project, projectIndex, def)).join("")}</section>`;
  }).join("");
  document.getElementById("conflict-count").textContent = conflictCount;
  bindDynamicEvents();
  applyConfirmedMode();
}

/** Lock confirmed content while leaving evidence comparison readable and reopen explicit. */
function applyConfirmedMode() {
  const selectors = ["#article-status", "#non-project-reason", "#add-project", "#confirm-consistent",
    "#project-tabs input", "#project-tabs button", "#projects input", "#projects textarea",
    "#projects select", "#projects button"];
  document.querySelectorAll(selectors.join(",")).forEach(control => { control.disabled = articleConfirmed; });
  const button = document.getElementById("confirm-article");
  button.disabled = false;
  button.textContent = articleConfirmed ? "重新打开复核" : "确认文章";
  button.classList.toggle("reopen", articleConfirmed);
}

/** Attach event handlers to field controls created by the latest render. */
function bindDynamicEvents() {
  document.querySelectorAll("[data-accept]").forEach(button => button.addEventListener("click", event => {
    const card = event.target.closest("[data-field]");
    const section = event.target.closest(".project-section");
    acceptCandidate([...document.querySelectorAll(".project-section")].indexOf(section), card.dataset.field, event.target.dataset.accept);
  }));
  document.querySelectorAll("[data-evidence]").forEach(button => button.addEventListener("click", event => highlightEvidence(event.currentTarget.dataset.evidence)));
  document.querySelectorAll("[data-add-entity]").forEach(button => button.addEventListener("click", event => {
    const index = Number(event.currentTarget.dataset.addEntity);
    const field = state.projects[index].fields.entities;
    let entities = Array.isArray(field.value) ? structuredClone(field.value) : [];
    entities.push({name:"", role:""});
    field.value = entities;
    field.value_state = "present";
    field.review_action = "modified";
    render(); queueSave();
  }));
  document.querySelectorAll(".field-card input,.field-card textarea,.field-card select").forEach(control => control.addEventListener("input", event => {
    const card = event.target.closest("[data-field]");
    const section = event.target.closest(".project-section");
    const index = [...document.querySelectorAll(".project-section")].indexOf(section);
    const field = state.projects[index].fields[card.dataset.field];
    let value = event.target.value;
    if (event.target.dataset.prop === "value" && event.target.type === "number") value = value === "" ? null : Number(value);
    if (event.target.dataset.prop === "value" && card.querySelector("textarea[data-prop='value']") === event.target) {
      try { value = JSON.parse(value); } catch (_) { /* Lenient drafts preserve temporarily invalid JSON text. */ }
    }
    field[event.target.dataset.prop] = value;
    if (event.target.dataset.prop === "value_state") render();
    if (event.target.dataset.prop === "value" && field.review_action === "unreviewed") field.review_action = "modified";
    queueSave();
  }));
  document.querySelectorAll("[data-project-name]").forEach(input => input.addEventListener("input", event => {
    const index = Number(event.target.dataset.projectName);
    state.projects[index].project_name = event.target.value;
    state.projects[index].fields.project_name.value = event.target.value;
    state.projects[index].fields.project_name.review_action = "modified";
    queueSave();
  }));
  document.querySelectorAll("[data-remove-project]").forEach(button => button.addEventListener("click", event => {
    state.projects.splice(Number(event.target.dataset.removeProject), 1);
    state.projects.forEach((project, index) => project.gold_project_id = `P${String(index + 1).padStart(2, "0")}`);
    render(); queueSave();
  }));
}

/** Add an empty project structure that remains valid as a lenient draft. */
function addProject() {
  const fields = Object.fromEntries(fieldDefs.map(([name]) => [name, {value_state:"not_mentioned", value:null, evidence:"", review_action:"unreviewed"}]));
  const index = state.projects.length + 1;
  state.projects.push({gold_project_id:`P${String(index).padStart(2, "0")}`, project_name:"", relation_note:"人工拆分", fields});
  render(); queueSave();
}

/** Confirm every currently populated field while leaving values and evidence unchanged. */
function confirmPopulated() {
  state.projects.forEach(project => Object.values(project.fields).forEach(field => {
    if (field.value_state !== "needs_adjudication") field.review_action = field.review_action === "modified" ? "modified" : "confirmed";
  }));
  render(); queueSave();
}

/** Save the latest browser state and request strict whole-article confirmation. */
async function confirmArticle() {
  await saveDraft();
  try {
    const response = await fetch(`/api/review/${articleId}/confirm`, {method:"POST"});
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "确认失败");
    articleConfirmed = true;
    applyConfirmedMode();
    document.getElementById("save-status").textContent = `文章已确认 · r${result.revision}`;
    showError("");
  } catch (error) {
    showError(`无法确认文章：${error.message}`);
  }
}

/** Reopen the current confirmed work copy through an explicit auditable transition. */
async function reopenArticle() {
  try {
    const response = await fetch(`/api/review/${articleId}/reopen`, {method:"POST"});
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "重新打开失败");
    articleConfirmed = false;
    applyConfirmedMode();
    document.getElementById("save-status").textContent = `已重新打开 · r${result.revision}`;
    showError("");
  } catch (error) {
    showError(`无法重新打开复核：${error.message}`);
  }
}

/** Route the primary workflow button according to the current confirmation state. */
function toggleArticleConfirmation() {
  return articleConfirmed ? reopenArticle() : confirmArticle();
}

document.getElementById("article-status").addEventListener("change", event => { state.article_status = event.target.value; queueSave(); });
document.getElementById("non-project-reason").addEventListener("input", event => { state.non_project_reason = event.target.value; queueSave(); });
document.getElementById("add-project").addEventListener("click", addProject);
document.getElementById("confirm-consistent").addEventListener("click", confirmPopulated);
document.getElementById("confirm-article").addEventListener("click", toggleArticleConfirmation);
window.addEventListener("scroll", () => { state.scroll_y = window.scrollY; }, {passive:true});
render();
requestAnimationFrame(() => window.scrollTo(0, state.scroll_y || 0));
