const kinds = [
  { id: "llm", title: "LLM 对话模型" },
  { id: "llm_task", title: "LLM 任务模型" },
  { id: "embedding", title: "Embedding 记忆向量" },
  { id: "tts", title: "TTS 语音合成" },
  { id: "search", title: "联网搜索" },
  { id: "weather", title: "天气服务" },
  { id: "image", title: "图片生成" },
];

const state = {
  presets: {},
  providers: [],
  cards: new Map(),
  characters: [],
  voices: [],
  ttsProviders: [],
  users: [],
  relations: [],
  memories: [],
  calendarEvents: [],
  proactiveEvents: [],
  aiSchedule: [],
  proactiveActionResult: null,
  runtimeLogs: [],
  runtimeFeatures: [],
  runtimeFlows: [],
  runtimeTraces: [],
  runtimeServerTime: 0,
  runtimeTotal: 0,
  runtimeLegacyCount: 0,
  runtimeOldestTs: null,
  runtimeHasMore: false,
  runtimePendingPayload: null,
  runtimeOpenTraces: new Set(),
  runtimeOpenItems: new Set(),
  runtimeSelectedId: "",
  runtimeTimer: null,
  runtimeFilters: { feature: "", status: "", q: "", limit: 200, traceId: "", includeLegacy: true, includeCacheHits: false },
  currentPage: "users",
  userPage: { page: 1, pageSize: 20, total: 0, q: "" },
  memoryPage: { page: 1, pageSize: 10, total: 0, q: "" },
  calendarPage: { page: 1, pageSize: 10, total: 0, q: "" },
  proactivePage: { page: 1, pageSize: 10, total: 0, q: "" },
  activeUserId: "",
  activeUserCharacterId: "",
  activeCharacterId: "atri",
  editingVoiceId: "",
  editingCalendarEventId: "",
  live2dAppearanceId: "neko",
  live2dHitAreas: [],
  live2dSelectedAreaId: "",
  live2dReferenceImage: null,
  live2dPreviewConfig: null,
  live2dPlacement: { scale: 1.1, offsetX: 0, offsetY: -10, bottomInset: 30 },
  live2dDrag: null,
  live2dPreviewReady: false,
};

const $ = (selector, root = document) => root.querySelector(selector);

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const text = await response.text();
  const payload = text ? JSON.parse(text) : {};
  if (!response.ok) {
    throw new Error(payload.detail || text || `HTTP ${response.status}`);
  }
  return payload;
}

function pretty(value) {
  return JSON.stringify(value, null, 2);
}

function parseJsonField(field, fallback = {}) {
  const raw = field.value.trim();
  if (!raw) return fallback;
  return JSON.parse(raw);
}

function formatDateTime(value) {
  if (!value) return "-";
  const date = new Date(Number(value) * 1000);
  if (Number.isNaN(date.getTime())) return "-";
  return date.toLocaleString();
}

function formatDuration(ms) {
  const value = Number(ms || 0);
  if (value < 1000) return `${Math.max(0, Math.round(value))} ms`;
  return `${(value / 1000).toFixed(value < 10_000 ? 2 : 1)} s`;
}

function formatIsoTime(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "\"": "&quot;",
    "'": "&#39;",
  }[char]));
}

function pageCount(meta) {
  return Math.max(1, Math.ceil((meta.total || 0) / (meta.pageSize || 20)));
}

function renderPager(node, meta, onChange) {
  const totalPages = pageCount(meta);
  node.innerHTML = `
    <span>第 ${escapeHtml(meta.page)} / ${escapeHtml(totalPages)} 页 · 共 ${escapeHtml(meta.total || 0)} 条</span>
    <button type="button" class="secondary" data-page-step="prev" ${meta.page <= 1 ? "disabled" : ""}>上一页</button>
    <button type="button" class="secondary" data-page-step="next" ${meta.page >= totalPages ? "disabled" : ""}>下一页</button>
  `;
  node.querySelectorAll("[data-page-step]").forEach((button) => {
    button.addEventListener("click", () => {
      const next = button.dataset.pageStep === "prev" ? meta.page - 1 : meta.page + 1;
      onChange(Math.min(totalPages, Math.max(1, next)));
    });
  });
}

function switchPage(page) {
  state.currentPage = page;
  document.querySelectorAll("[data-page]").forEach((section) => {
    section.hidden = section.dataset.page !== page;
  });
  document.querySelectorAll("[data-page-target]").forEach((button) => {
    button.classList.toggle("active", button.dataset.pageTarget === page);
  });
  if (page === "runtime-logs") {
    readRuntimeFilters();
    loadRuntimeLogs().catch((error) => {
      $("#runtimeLogList").innerHTML = `<pre>${escapeHtml(pretty({ ok: false, message: error.message }))}</pre>`;
    });
  }
  if (page === "debug") {
    Promise.all([loadProactiveEvents(), loadAiSchedule()])
      .then(() => renderProactiveEditor())
      .catch((error) => {
        $("#proactiveEditor").innerHTML = `<pre>${escapeHtml(pretty({ ok: false, message: error.message }))}</pre>`;
      });
  }
  if (page === "live2d") {
    loadLive2dManager().catch((error) => {
      $("#live2dResult").textContent = pretty({ ok: false, message: error.message });
    });
  }
}

function providerList(kind) {
  return state.presets[kind] || [];
}

function currentProvider(kind) {
  return state.providers.find((item) => item.kind === kind && item.ready)
    || state.providers.find((item) => item.kind === kind && item.enabled && providerList(kind).some((preset) => preset.provider === item.provider))
    || state.providers.find((item) => item.kind === kind && item.enabled)
    || state.providers.find((item) => item.kind === kind)
    || null;
}

function presetFor(kind, providerName) {
  return providerList(kind).find((item) => item.provider === providerName) || providerList(kind)[0] || {};
}

function defaultForField(field, provider) {
  if (field.name === "label") return provider.label || field.default || "";
  if (field.name === "base_url") return provider.base_url || field.default || "";
  if (field.name === "model") return provider.model || field.default || "";
  if (field.default !== undefined && field.default !== null) return field.default;
  return "";
}

function existingValue(field, existing, provider) {
  if (!existing) return defaultForField(field, provider);
  if (field.section === "core") {
    if (field.name === "label") return existing.label || provider.label || "";
    if (field.name === "base_url") return existing.base_url || provider.base_url || "";
    if (field.name === "model") return existing.model || provider.model || "";
  }
  if (field.section === "metadata" || field.section === "advanced") {
    const value = (existing.metadata || {})[field.name];
    if (value !== undefined) return value;
  }
  return defaultForField(field, provider);
}

function createField(field, value) {
  const label = document.createElement("label");
  label.className = field.section === "advanced" ? "advanced-field" : "";
  label.dataset.section = field.section;
  label.dataset.name = field.name;
  if (field.show_when && Object.keys(field.show_when).length) {
    label.dataset.showWhen = JSON.stringify(field.show_when);
  }

  const title = document.createElement("span");
  title.textContent = field.required ? `${field.label} *` : field.label;
  label.appendChild(title);

  let input;
  if (field.type === "select") {
    input = document.createElement("select");
    for (const optionItem of field.options || []) {
      const optionValue = typeof optionItem === "object" ? optionItem.value : optionItem;
      const option = document.createElement("option");
      option.value = optionValue;
      option.textContent = typeof optionItem === "object" ? (optionItem.label || optionValue) : optionValue;
      input.appendChild(option);
    }
    input.value = value || field.default || "";
  } else if (field.type === "textarea" || field.type === "json") {
    input = document.createElement("textarea");
    input.rows = field.type === "json" ? 4 : 3;
    input.spellcheck = false;
    input.value = field.type === "json" ? pretty(value || field.default || {}) : (value || "");
  } else if (field.type === "checkbox") {
    input = document.createElement("input");
    input.type = "checkbox";
    input.checked = value === true || String(value).toLowerCase() === "true";
  } else {
    input = document.createElement("input");
    input.type = field.section === "secret" ? "password" : (field.type === "number" ? "number" : "text");
    if (field.type === "number") input.step = "any";
    input.value = field.section === "secret" ? "" : (value ?? "");
    if (field.section === "secret") input.placeholder = field.placeholder || "留空则保留已保存凭据";
  }
  input.dataset.name = field.name;
  input.dataset.section = field.section;
  input.dataset.type = field.type || "text";
  if (field.placeholder && field.section !== "secret") input.placeholder = field.placeholder;
  if (field.required) input.required = true;
  label.appendChild(input);
  if (field.help) {
    const help = document.createElement("small");
    help.className = "field-help";
    help.textContent = field.help;
    label.appendChild(help);
  }
  return label;
}

function fieldInput(card, name) {
  return [...card.querySelectorAll("input, select, textarea")].find((input) => input.dataset.name === name) || null;
}

function updateConditionalFields(card) {
  card.querySelectorAll("label[data-show-when]").forEach((label) => {
    const conditions = JSON.parse(label.dataset.showWhen || "{}");
    const visible = Object.entries(conditions).every(([name, expected]) => {
      const input = fieldInput(card, name);
      if (!input) return false;
      const value = input.dataset.type === "checkbox" ? String(input.checked) : input.value;
      return value === String(expected);
    });
    label.hidden = !visible;
  });
}

function parseFieldValue(input) {
  const type = input.dataset.type;
  if (type === "checkbox") return input.checked;
  if (type === "number") return input.value === "" ? "" : Number(input.value);
  if (type === "json") {
    const raw = input.value.trim();
    return raw ? JSON.parse(raw) : {};
  }
  return input.value.trim();
}

function setCardResult(card, value) {
  $(".result", card).textContent = typeof value === "string" ? value : pretty(value);
}

function cardPayload(kind, card) {
  const providerName = $(".provider-select", card).value;
  const preset = presetFor(kind, providerName);
  const payload = {
    kind,
    provider: providerName,
    label: preset.label || providerName,
    base_url: preset.base_url || "",
    model: preset.model || "",
    metadata: {},
    secrets: {},
    enabled: true,
  };
  if (card.dataset.providerId) payload.provider_id = card.dataset.providerId;

  card.querySelectorAll("[data-section]").forEach((input) => {
    if (!input.matches("input, select, textarea")) return;
    const section = input.dataset.section;
    const name = input.dataset.name;
    const value = parseFieldValue(input);
    if (section === "core") {
      if (name === "label") payload.label = value;
      if (name === "base_url") payload.base_url = value;
      if (name === "model") payload.model = value;
    } else if (section === "secret") {
      if (value) payload.secrets[name] = value;
    } else if (section === "metadata" || section === "advanced") {
      if (value !== "" && !(typeof value === "object" && Object.keys(value).length === 0)) {
        payload.metadata[name] = value;
      }
    }
  });
  return payload;
}

function curlPreview(kind, card) {
  const payload = cardPayload(kind, card);
  const masked = JSON.parse(JSON.stringify(payload));
  if (masked.secrets) {
    for (const key of Object.keys(masked.secrets)) masked.secrets[key] = "***";
  }
  return [
    "curl -X POST http://127.0.0.1:8899/api/config/providers/test \\",
    "  -H \"Content-Type: application/json\" \\",
    `  -d '${JSON.stringify({ ...masked, test_text: "今天也想听你说说话。" })}'`,
  ].join("\n");
}

async function saveProvider(kind, card, test = false) {
  const button = test ? $(".test", card) : $(".save", card);
  button.disabled = true;
  try {
    const payload = cardPayload(kind, card);
    const result = await api(test ? "/api/config/providers/test" : "/api/config/providers", {
      method: "POST",
      body: JSON.stringify(test ? { ...payload, test_text: "今天也想听你说说话。" } : payload),
    });
    setCardResult(card, result);
    await loadStatus();
    if (kind === "tts") {
      await loadVoices();
    }
  } catch (error) {
    setCardResult(card, { ok: false, message: error.message });
  } finally {
    button.disabled = false;
  }
}

async function loadModels(kind, card) {
  if (!card.dataset.providerId) {
    setCardResult(card, "请先保存该服务，再拉取模型列表。");
    return;
  }
  $(".models", card).disabled = true;
  try {
    const result = await api(`/api/config/providers/models?provider_id=${encodeURIComponent(card.dataset.providerId)}`);
    setCardResult(card, result);
    if (result.ok && Array.isArray(result.models)) {
      renderModelPicker(card, result.models);
    }
  } catch (error) {
    setCardResult(card, { ok: false, message: error.message });
  } finally {
    $(".models", card).disabled = false;
  }
}

function renderModelPicker(card, models) {
  const picker = $(".model-picker", card);
  const select = $(".model-select", card);
  const input = fieldInput(card, "model");
  select.innerHTML = "";
  if (!models.length || !input) {
    picker.hidden = true;
    return;
  }
  for (const model of models) {
    const option = document.createElement("option");
    option.value = model;
    option.textContent = model;
    select.appendChild(option);
  }
  select.value = models.includes(input.value) ? input.value : models[0];
  input.value = select.value;
  picker.hidden = false;
  select.onchange = () => {
    input.value = select.value;
    input.dispatchEvent(new Event("change", { bubbles: true }));
  };
}

function renderFields(card, kind, providerName) {
  const preset = presetFor(kind, providerName);
  const existing = state.providers.find((item) => item.kind === kind && item.provider === providerName) || null;
  card.dataset.providerId = existing ? existing.provider_id : "";
  $(".provider-title", card).textContent = preset.label || providerName;
  $(".description", card).textContent = preset.description || "";
  $(".doc-link", card).href = preset.docs || "#";
  $(".doc-link", card).style.display = preset.docs ? "" : "none";
  $(".models", card).style.display = preset.supports_models ? "" : "none";
  $(".model-picker", card).hidden = true;

  const fields = $(".fields", card);
  fields.innerHTML = "";
  const advancedFields = [];
  for (const field of preset.fields || []) {
    const node = createField(field, existingValue(field, existing, preset));
    if (field.section === "advanced") advancedFields.push(node);
    else fields.appendChild(node);
  }
  const advanced = $(".advanced-fields", card);
  advanced.innerHTML = "";
  advancedFields.forEach((node) => advanced.appendChild(node));
  card.querySelectorAll("input, select, textarea").forEach((input) => {
    input.addEventListener("change", () => updateConditionalFields(card));
  });
  updateConditionalFields(card);

  const badge = $(".badge", card);
  if (existing) {
    const missing = [...(existing.missing_secret_fields || []), ...(existing.missing_required_fields || [])];
    badge.textContent = existing.ready ? "已配置" : (missing.length ? `缺少 ${missing.join(", ")}` : "未完成");
    badge.classList.toggle("ok", Boolean(existing.ready));
    setCardResult(card, {
      provider_id: existing.provider_id,
      saved_credentials: existing.has_secret_fields || {},
      missing_secret_fields: existing.missing_secret_fields || [],
      missing_required_fields: existing.missing_required_fields || [],
      enabled: existing.enabled,
    });
  } else {
    badge.textContent = "未配置";
    badge.classList.remove("ok");
    setCardResult(card, "等待配置...");
  }
}

function voiceProviderOptions(selectedId = "") {
  const providers = state.ttsProviders.length
    ? state.ttsProviders
    : state.providers.filter((item) => item.kind === "tts");
  const options = ['<option value="">使用当前启用 TTS Provider</option>'];
  for (const provider of providers) {
    const label = provider.label || provider.provider_id || provider.provider;
    const ready = provider.ready ? "已配置" : "未完成";
    options.push(
      `<option value="${escapeHtml(provider.provider_id)}" ${provider.provider_id === selectedId ? "selected" : ""}>${escapeHtml(label)} · ${escapeHtml(provider.model || provider.provider)} · ${ready}</option>`
    );
  }
  return options.join("");
}

function renderCharacterManager() {
  const tabs = $("#characterTabs");
  const editor = $("#characterEditor");
  const status = $("#characterStatus");
  tabs.innerHTML = "";
  if (!state.characters.length) {
    status.textContent = "无角色";
    editor.textContent = "还没有角色数据。";
    return;
  }
  status.textContent = `${state.characters.length} 个角色`;
  status.classList.add("ok");
  if (!state.activeCharacterId || !state.characters.some((item) => item.character_id === state.activeCharacterId)) {
    state.activeCharacterId = state.characters[0].character_id;
  }
  for (const character of state.characters) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = character.name || character.character_id;
    button.classList.toggle("active", character.character_id === state.activeCharacterId);
    button.addEventListener("click", () => {
      state.activeCharacterId = character.character_id;
      renderCharacterManager();
    });
    tabs.appendChild(button);
  }
  const character = state.characters.find((item) => item.character_id === state.activeCharacterId);
  if (!character) return;
  editor.innerHTML = `
    <form class="character-form">
      <label><span>角色名</span><input name="name" value="${escapeHtml(character.name)}"></label>
      <label><span>特殊回复阈值</span><input name="key_reply_threshold" type="number" min="0" max="100" value="${escapeHtml(character.key_reply_threshold ?? 75)}"></label>
      <label><span>当前音色</span><select name="tts_voice_profile_id"></select></label>
      <label><span>兼容旧 voice_type</span><input value="${escapeHtml(character.tts_voice_type || "")}" disabled></label>
      <label class="wide"><span>结构化人设卡 JSON</span><textarea name="persona_card">${escapeHtml(pretty(character.persona_card || {}))}</textarea></label>
      <label class="wide"><span>角色设定</span><textarea name="persona_prompt">${escapeHtml(character.persona_prompt || "")}</textarea></label>
      <label class="wide"><span>表达风格</span><textarea name="speech_style">${escapeHtml(character.speech_style || "")}</textarea></label>
      <label class="wide"><span>关系边界</span><textarea name="relationship_boundary">${escapeHtml(character.relationship_boundary || "")}</textarea></label>
      <div class="actions">
        <button type="submit">保存角色设定</button>
      </div>
    </form>
  `;
  const form = $("form", editor);
  const voiceSelect = form.elements.tts_voice_profile_id;
  voiceSelect.innerHTML = `<option value="">未选择：使用旧 voice_type / Provider 默认</option>` + state.voices
    .map((voice) => `<option value="${escapeHtml(voice.voice_id)}" ${voice.voice_id === character.tts_voice_profile_id ? "selected" : ""}>${escapeHtml(voice.label || voice.speaker)} · ${voice.language === "ja" ? "日文 TTS" : "中文 TTS"}</option>`)
    .join("");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const payload = {
        name: form.elements.name.value.trim(),
        persona_card: parseJsonField(form.elements.persona_card, {}),
        persona_prompt: form.elements.persona_prompt.value.trim(),
        speech_style: form.elements.speech_style.value.trim(),
        relationship_boundary: form.elements.relationship_boundary.value.trim(),
        tts_voice_profile_id: form.elements.tts_voice_profile_id.value,
        key_reply_threshold: Number(form.elements.key_reply_threshold.value || 75),
      };
      const updated = await api(`/api/admin/characters/${encodeURIComponent(character.character_id)}`, {
        method: "PUT",
        body: JSON.stringify(payload),
      });
      state.characters = state.characters.map((item) => item.character_id === updated.character_id ? updated : item);
      renderCharacterManager();
    } catch (error) {
      editor.insertAdjacentHTML("beforeend", `<pre>${escapeHtml(pretty({ ok: false, message: error.message }))}</pre>`);
    }
  });
}

function setVoiceForm(voice = null) {
  const form = $("#voiceForm");
  state.editingVoiceId = voice ? voice.voice_id : "";
  form.elements.voice_id.value = voice ? voice.voice_id : "";
  form.elements.voice_id.disabled = Boolean(voice);
  form.elements.provider_id.innerHTML = voiceProviderOptions(voice ? voice.provider_id : "");
  form.elements.label.value = voice ? (voice.label || "") : "";
  form.elements.speaker.value = voice ? voice.speaker : "";
  form.elements.resource_id.value = voice ? voice.resource_id : "";
  form.elements.language.value = voice ? voice.language : "zh";
  form.elements.enabled.checked = voice ? Boolean(voice.enabled) : true;
}

function renderVoiceManager() {
  const status = $("#voiceStatus");
  const list = $("#voiceList");
  status.textContent = `${state.voices.length} 个音色`;
  status.classList.toggle("ok", state.voices.some((voice) => voice.enabled));
  list.innerHTML = "";
  if (!state.voices.length) {
    list.innerHTML = `<div class="editor-placeholder">先添加一个中文或日文音色。日文音色会额外生成日文 TTS 文本，首页仍显示中文。</div>`;
  }
  for (const voice of state.voices) {
    const item = document.createElement("article");
    item.className = "voice-item";
    item.innerHTML = `
      <header>
        <div>
          <h3>${escapeHtml(voice.label || voice.speaker)}</h3>
          <div class="voice-meta">
            <span>${voice.language === "ja" ? "日文 TTS" : "中文 TTS"}</span>
            <span>${voice.enabled ? "启用" : "停用"}</span>
            <span>speaker: ${escapeHtml(voice.speaker)}</span>
            <span>resource: ${escapeHtml(voice.resource_id)}</span>
          </div>
        </div>
        <span class="badge ${voice.last_test_ok ? "ok" : ""}">${voice.last_test_message ? (voice.last_test_ok ? "测试通过" : "测试失败") : "未测试"}</span>
      </header>
      <small>${escapeHtml(voice.last_test_message || "还没有测试结果")}</small>
      <div class="voice-test">
        <input data-test-text value="${voice.language === "ja" ? "今日は少し声を聞かせたいです。" : "今天也想听你说说话。"}">
        <button type="button" data-action="edit">编辑</button>
        <button type="button" class="secondary" data-action="test">测试</button>
        <button type="button" class="secondary" data-action="delete">删除</button>
      </div>
    `;
    item.querySelector('[data-action="edit"]').addEventListener("click", () => setVoiceForm(voice));
    item.querySelector('[data-action="test"]').addEventListener("click", () => testVoice(voice.voice_id, item.querySelector("[data-test-text]").value));
    item.querySelector('[data-action="delete"]').addEventListener("click", () => deleteVoice(voice.voice_id));
    list.appendChild(item);
  }
  setVoiceForm(state.editingVoiceId ? state.voices.find((voice) => voice.voice_id === state.editingVoiceId) : null);
}

async function saveVoice(event) {
  event.preventDefault();
  const form = $("#voiceForm");
  const payload = {
    voice_id: form.elements.voice_id.value.trim(),
    provider_id: form.elements.provider_id.value,
    label: form.elements.label.value.trim(),
    speaker: form.elements.speaker.value.trim(),
    resource_id: form.elements.resource_id.value.trim(),
    language: form.elements.language.value,
    enabled: form.elements.enabled.checked,
  };
  const editing = Boolean(state.editingVoiceId);
  const path = editing ? `/api/admin/tts-voices/${encodeURIComponent(state.editingVoiceId)}` : "/api/admin/tts-voices";
  try {
    const saved = await api(path, { method: editing ? "PUT" : "POST", body: JSON.stringify(payload) });
    $("#voiceResult").textContent = pretty(saved);
    await loadVoices();
    await loadCharacters();
  } catch (error) {
    $("#voiceResult").textContent = pretty({ ok: false, message: error.message });
  }
}

async function testVoice(voiceId, text) {
  try {
    const result = await api(`/api/admin/tts-voices/${encodeURIComponent(voiceId)}/test`, {
      method: "POST",
      body: JSON.stringify({ text }),
    });
    $("#voiceResult").textContent = pretty(result);
    await loadVoices();
  } catch (error) {
    $("#voiceResult").textContent = pretty({ ok: false, message: error.message });
  }
}

async function deleteVoice(voiceId) {
  if (!window.confirm("确定删除这个音色？角色中引用它的设置会被清空。")) return;
  try {
    const result = await api(`/api/admin/tts-voices/${encodeURIComponent(voiceId)}`, { method: "DELETE" });
    $("#voiceResult").textContent = pretty(result);
    await loadVoices();
    await loadCharacters();
  } catch (error) {
    $("#voiceResult").textContent = pretty({ ok: false, message: error.message });
  }
}

async function loadVoices() {
  const payload = await api("/api/admin/tts-voices");
  state.voices = payload.items || [];
  state.ttsProviders = payload.providers || [];
  renderVoiceManager();
}

async function loadCharacters() {
  const payload = await api("/api/admin/characters");
  state.characters = payload.items || [];
  renderCharacterManager();
}

function activeUser() {
  return state.users.find((item) => item.user_id === state.activeUserId) || state.users[0] || null;
}

function defaultCharacterId() {
  return state.activeCharacterId || state.characters[0]?.character_id || "atri";
}

function activeUserRelations() {
  const user = activeUser();
  if (!user) return [];
  return state.relations.filter((item) => item.user_id === user.user_id);
}

function syncActiveUserCharacter(preferredId = state.activeUserCharacterId) {
  const user = activeUser();
  const fallback = defaultCharacterId();
  const relations = activeUserRelations();
  if (preferredId && relations.some((item) => item.character_id === preferredId)) {
    state.activeUserCharacterId = preferredId;
    return state.activeUserCharacterId;
  }
  if (user?.active_character_id && (relations.length === 0 || relations.some((item) => item.character_id === user.active_character_id))) {
    state.activeUserCharacterId = user.active_character_id;
    return state.activeUserCharacterId;
  }
  state.activeUserCharacterId = relations.find((item) => item.character_id === fallback)?.character_id
    || relations[0]?.character_id
    || fallback;
  return state.activeUserCharacterId;
}

function selectedUserCharacterId() {
  return state.activeUserCharacterId || syncActiveUserCharacter();
}

function characterSelectOptions(selectedId) {
  const items = [...state.characters];
  if (selectedId && !items.some((item) => item.character_id === selectedId)) {
    items.unshift({ character_id: selectedId, name: selectedId });
  }
  if (!items.length) {
    const fallback = defaultCharacterId();
    items.push({ character_id: fallback, name: fallback });
  }
  return items.map((character) => {
    const value = character.character_id;
    const label = character.name || character.character_id;
    return `<option value="${escapeHtml(value)}" ${value === selectedId ? "selected" : ""}>${escapeHtml(label)} (${escapeHtml(value)})</option>`;
  }).join("");
}

async function changeActiveUserCharacter(characterId) {
  state.activeUserCharacterId = characterId || defaultCharacterId();
  state.memoryPage.page = 1;
  state.calendarPage.page = 1;
  state.proactivePage.page = 1;
  state.editingCalendarEventId = "";
  state.proactiveActionResult = null;
  if (!state.activeUserId) {
    renderTestData();
    return;
  }
  await api(`/api/admin/users/${encodeURIComponent(state.activeUserId)}/relation`, {
    method: "PUT",
    body: JSON.stringify({ character_id: state.activeUserCharacterId }),
  });
  await loadTestData();
}

function activeRelation() {
  const user = activeUser();
  if (!user) return null;
  const characterId = selectedUserCharacterId();
  return state.relations.find((item) => item.user_id === user.user_id && item.character_id === characterId)
    || null;
}

function renderUserTabs() {
  $("#userSearch").value = state.userPage.q;
  $("#userPageSize").value = String(state.userPage.pageSize);
  const tbody = $("#userTable tbody");
  tbody.innerHTML = "";
  for (const user of state.users) {
    const row = document.createElement("tr");
    row.classList.toggle("active", user.user_id === state.activeUserId);
    row.innerHTML = `
      <td><strong>${escapeHtml(user.display_name || user.user_id)}</strong><br><small>${escapeHtml(user.user_id)}</small></td>
      <td>${escapeHtml(user.timezone || "")}</td>
      <td>${escapeHtml(user.proactive_daily_limit || "")}</td>
      <td>${user.story_completed ? "已完成" : "未完成"}</td>
      <td><small>${escapeHtml(user.updated_at || user.created_at || "")}</small></td>
    `;
    row.addEventListener("click", async () => {
      state.activeUserId = user.user_id;
      syncActiveUserCharacter();
      state.memoryPage.page = 1;
      state.calendarPage.page = 1;
      state.proactivePage.page = 1;
      await Promise.all([loadMemories(), loadCalendarEvents(), loadProactiveEvents(), loadAiSchedule()]);
      renderTestData();
    });
    tbody.appendChild(row);
  }
  renderPager($("#userPager"), state.userPage, async (page) => {
    state.userPage.page = page;
    await loadTestData();
  });
}

function renderUserEditor() {
  const node = $("#userEditor");
  const user = activeUser();
  if (!user) {
    node.textContent = "还没有用户。";
    return;
  }
  node.innerHTML = `
    <form class="compact-form" id="userForm">
      <label><span>user_id</span><input name="user_id" value="${escapeHtml(user.user_id)}" disabled></label>
      <label><span>当前角色</span><select id="userCharacterSelect" name="active_character_id">${characterSelectOptions(selectedUserCharacterId())}</select></label>
      <label><span>显示名</span><input name="display_name" value="${escapeHtml(user.display_name || "")}"></label>
      <label><span>时区</span><input name="timezone" value="${escapeHtml(user.timezone || "Asia/Hong_Kong")}"></label>
      <label><span>主动消息频率</span><input name="proactive_daily_limit" value="${escapeHtml(user.proactive_daily_limit || "unlimited")}"></label>
      <label><span>睡眠开始</span><input name="sleep_start" value="${escapeHtml(user.sleep_start || "00:30")}"></label>
      <label><span>睡眠结束</span><input name="sleep_end" value="${escapeHtml(user.sleep_end || "08:00")}"></label>
      <label class="wide"><span>兴趣主题（一行一个）</span><textarea name="interest_topics">${escapeHtml((user.interest_topics || []).join("\n"))}</textarea></label>
      <label class="wide"><span>用户画像 JSON</span><textarea name="profile">${escapeHtml(pretty(user.profile || {}))}</textarea></label>
      <label class="inline-check"><input type="checkbox" name="story_completed" ${user.story_completed ? "checked" : ""}><span>已完成开场剧情</span></label>
      <label class="inline-check"><input type="checkbox" name="tts_enabled" ${user.tts_enabled ? "checked" : ""}><span>TTS</span></label>
      <label class="inline-check"><input type="checkbox" name="notifications_enabled" ${user.notifications_enabled ? "checked" : ""}><span>通知</span></label>
      <label class="inline-check"><input type="checkbox" name="news_enabled" ${user.news_enabled ? "checked" : ""}><span>新闻</span></label>
      <div class="actions">
        <button type="submit">保存用户</button>
        <button type="button" class="secondary" id="deleteUser">删除用户</button>
      </div>
    </form>
  `;
  $("#userForm").addEventListener("submit", saveUser);
  $("#userCharacterSelect").addEventListener("change", async (event) => {
    try {
      await changeActiveUserCharacter(event.target.value);
    } catch (error) {
      node.insertAdjacentHTML("beforeend", `<pre>${escapeHtml(pretty({ ok: false, message: error.message }))}</pre>`);
    }
  });
  $("#deleteUser").addEventListener("click", deleteUser);
}

function renderRelationEditor() {
  const node = $("#relationEditor");
  const relation = activeRelation();
  if (!relation) {
    node.textContent = "还没有关系数据，保存一次用户后会自动补齐。";
    return;
  }
  node.innerHTML = `
    <form class="compact-form" id="relationForm">
      <label><span>好感</span><input name="affection" type="number" value="${escapeHtml(relation.affection)}"></label>
      <label><span>信任</span><input name="trust" type="number" value="${escapeHtml(relation.trust)}"></label>
      <label><span>依赖</span><input name="dependency" type="number" value="${escapeHtml(relation.dependency)}"></label>
      <label><span>心情</span><input name="mood" type="number" value="${escapeHtml(relation.mood)}"></label>
      <label class="wide"><span>关系阶段</span><input name="relationship_stage" value="${escapeHtml(relation.relationship_stage || relation.stage || "")}"></label>
      <div class="wide relation-attitude">
        <strong>${escapeHtml(relation.attitude_band || "neutral")}</strong>
        <span>${escapeHtml(relation.attitude_text || "")}</span>
      </div>
      <div class="actions"><button type="submit">保存关系数值</button></div>
    </form>
  `;
  $("#relationForm").addEventListener("submit", saveRelation);
}

function renderMemoryEditor() {
  const node = $("#memoryEditor");
  const user = activeUser();
  if (!user) {
    node.textContent = "请选择用户。";
    return;
  }
  const items = state.memories.map((memory) => `
    <article class="data-item ${memory.hidden ? "hidden" : ""}">
      <strong>${escapeHtml(memory.layer)} · ${escapeHtml(memory.importance)}</strong>
      <div>${escapeHtml(memory.content)}</div>
      <small>${escapeHtml(memory.created_at || "")} · vector ${escapeHtml(memory.vector_status || "unknown")} ${memory.vector_updated_at ? `· ${escapeHtml(memory.vector_updated_at)}` : ""}</small>
      <small>${(memory.tags || []).map((tag) => `#${escapeHtml(tag)}`).join(" ")}</small>
      <div class="actions">
        <button type="button" class="secondary" data-memory-hide="${escapeHtml(memory.memory_id)}">${memory.hidden ? "恢复" : "隐藏"}</button>
        <button type="button" class="secondary" data-memory-delete="${escapeHtml(memory.memory_id)}">删除</button>
      </div>
    </article>
  `).join("");
  node.innerHTML = `
    <form class="memory-form" id="memoryForm">
      <label><span>层级</span><input name="layer" value="chat"></label>
      <label><span>重要度</span><input name="importance" type="number" step="0.01" value="0.5"></label>
      <label><span>置信度</span><input name="confidence" type="number" step="0.01" value="0.8"></label>
      <label><span>角色</span><select name="character_id">${characterSelectOptions(selectedUserCharacterId())}</select></label>
      <label><span>Tags（逗号分隔）</span><input name="tags" placeholder="interest,event"></label>
      <label class="wide"><span>内容</span><textarea name="content"></textarea></label>
      <label class="wide"><span>Metadata JSON</span><textarea name="metadata">{}</textarea></label>
      <div class="actions"><button type="submit">新增记忆</button></div>
    </form>
    <div class="table-toolbar">
      <label><span>搜索记忆</span><input id="memorySearch" value="${escapeHtml(state.memoryPage.q)}" placeholder="内容 / 层级 / 角色"></label>
      <label><span>每页</span><select id="memoryPageSize">${[10, 20, 50].map((size) => `<option value="${size}" ${state.memoryPage.pageSize === size ? "selected" : ""}>${size}</option>`).join("")}</select></label>
      <button type="button" class="secondary" id="memoryRefresh">刷新</button>
      <span></span>
    </div>
    <div class="data-list">${items || "<small>暂无记忆</small>"}</div>
    <div class="pager" id="memoryPager"></div>
  `;
  $("#memoryForm").addEventListener("submit", createMemory);
  $("#memoryRefresh").addEventListener("click", async () => {
    state.memoryPage.q = $("#memorySearch").value.trim();
    state.memoryPage.pageSize = Number($("#memoryPageSize").value || 10);
    state.memoryPage.page = 1;
    await loadMemories();
    renderMemoryEditor();
  });
  $("#memorySearch").addEventListener("keydown", async (event) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    state.memoryPage.q = $("#memorySearch").value.trim();
    state.memoryPage.page = 1;
    await loadMemories();
    renderMemoryEditor();
  });
  $("#memoryPageSize").addEventListener("change", async () => {
    state.memoryPage.pageSize = Number($("#memoryPageSize").value || 10);
    state.memoryPage.page = 1;
    await loadMemories();
    renderMemoryEditor();
  });
  renderPager($("#memoryPager"), state.memoryPage, async (page) => {
    state.memoryPage.page = page;
    await loadMemories();
    renderMemoryEditor();
  });
  node.querySelectorAll("[data-memory-hide]").forEach((button) => button.addEventListener("click", () => toggleMemory(button.dataset.memoryHide)));
  node.querySelectorAll("[data-memory-delete]").forEach((button) => button.addEventListener("click", () => deleteMemory(button.dataset.memoryDelete)));
}

function editableCalendarEvents() {
  const user = activeUser();
  if (!user) return [];
  return state.calendarEvents.filter((item) => item.category !== "holiday" || item.user_id === user.user_id);
}

function renderCalendarEventEditor() {
  const node = $("#calendarEventEditor");
  const user = activeUser();
  if (!user) {
    node.textContent = "请选择用户。";
    return;
  }
  const editing = state.calendarEvents.find((item) => item.event_id === state.editingCalendarEventId) || null;
  const events = editableCalendarEvents().map((event) => `
    <article class="data-item ${event.hidden ? "hidden" : ""}">
      <strong>${escapeHtml(event.date)} · ${escapeHtml(event.title)}</strong>
      <small>${escapeHtml(event.category)} · salience ${escapeHtml(event.salience)} ${event.repeats_yearly ? "· 每年重复" : ""}</small>
      <div>${escapeHtml(event.description || "")}</div>
      <div class="actions">
        <button type="button" class="secondary" data-calendar-edit="${escapeHtml(event.event_id)}">编辑</button>
        <button type="button" class="secondary" data-calendar-hide="${escapeHtml(event.event_id)}">${event.hidden ? "恢复" : "隐藏"}</button>
        <button type="button" class="secondary" data-calendar-delete="${escapeHtml(event.event_id)}">删除</button>
      </div>
    </article>
  `).join("");
  node.innerHTML = `
    <form class="calendar-event-form" id="calendarEventForm">
      <label><span>日期</span><input name="date" type="date" value="${escapeHtml(editing?.date || new Date().toISOString().slice(0, 10))}"></label>
      <label><span>标题</span><input name="title" value="${escapeHtml(editing?.title || "")}" placeholder="约会日"></label>
      <label><span>分类</span><select name="category">
        ${["relationship", "date", "anniversary", "special"].map((category) => `<option value="${category}" ${category === (editing?.category || "relationship") ? "selected" : ""}>${category}</option>`).join("")}
      </select></label>
      <label><span>显著度</span><input name="salience" type="number" value="${escapeHtml(editing?.salience ?? 86)}"></label>
      <label class="inline-check"><input type="checkbox" name="repeats_yearly" ${editing?.repeats_yearly ? "checked" : ""}><span>每年重复</span></label>
      <label class="inline-check"><input type="checkbox" name="hidden" ${editing?.hidden ? "checked" : ""}><span>隐藏</span></label>
      <label class="wide"><span>描述/期待</span><textarea name="description">${escapeHtml(editing?.description || "")}</textarea></label>
      <div class="actions">
        <button type="submit">${editing ? "保存日历事件" : "新增日历事件"}</button>
        <button type="button" class="secondary" id="calendarEventNew">新建</button>
      </div>
    </form>
    <div class="table-toolbar">
      <label><span>搜索事件</span><input id="calendarSearch" value="${escapeHtml(state.calendarPage.q)}" placeholder="标题 / 描述 / 分类"></label>
      <label><span>每页</span><select id="calendarPageSize">${[10, 20, 50].map((size) => `<option value="${size}" ${state.calendarPage.pageSize === size ? "selected" : ""}>${size}</option>`).join("")}</select></label>
      <button type="button" class="secondary" id="calendarRefresh">刷新</button>
      <span></span>
    </div>
    <div class="data-list">${events || "<small>暂无可编辑事件</small>"}</div>
    <div class="pager" id="calendarPager"></div>
  `;
  $("#calendarEventForm").addEventListener("submit", saveCalendarEvent);
  $("#calendarRefresh").addEventListener("click", async () => {
    state.calendarPage.q = $("#calendarSearch").value.trim();
    state.calendarPage.pageSize = Number($("#calendarPageSize").value || 10);
    state.calendarPage.page = 1;
    await loadCalendarEvents();
    renderCalendarEventEditor();
  });
  $("#calendarSearch").addEventListener("keydown", async (event) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    state.calendarPage.q = $("#calendarSearch").value.trim();
    state.calendarPage.page = 1;
    await loadCalendarEvents();
    renderCalendarEventEditor();
  });
  $("#calendarPageSize").addEventListener("change", async () => {
    state.calendarPage.pageSize = Number($("#calendarPageSize").value || 10);
    state.calendarPage.page = 1;
    await loadCalendarEvents();
    renderCalendarEventEditor();
  });
  renderPager($("#calendarPager"), state.calendarPage, async (page) => {
    state.calendarPage.page = page;
    await loadCalendarEvents();
    renderCalendarEventEditor();
  });
  $("#calendarEventNew").addEventListener("click", () => {
    state.editingCalendarEventId = "";
    renderCalendarEventEditor();
  });
  node.querySelectorAll("[data-calendar-edit]").forEach((button) => button.addEventListener("click", () => {
    state.editingCalendarEventId = button.dataset.calendarEdit;
    renderCalendarEventEditor();
  }));
  node.querySelectorAll("[data-calendar-hide]").forEach((button) => button.addEventListener("click", () => toggleCalendarEvent(button.dataset.calendarHide)));
  node.querySelectorAll("[data-calendar-delete]").forEach((button) => button.addEventListener("click", () => deleteCalendarEvent(button.dataset.calendarDelete)));
}

function renderProactiveEditor() {
  const node = $("#proactiveEditor");
  if (!node) return;
  const user = activeUser();
  if (!user) {
    node.textContent = "请选择用户。";
    const status = $("#proactiveDebugStatus");
    if (status) status.textContent = "无用户";
    return;
  }
  const status = $("#proactiveDebugStatus");
  if (status) {
    status.textContent = `${user.display_name || user.user_id} · ${state.proactivePage.total || state.proactiveEvents.length} 条`;
    status.classList.toggle("ok", true);
  }
  const scheduleSegments = compactSchedule(state.aiSchedule);
  const scheduleItems = scheduleSegments.map((slot) => `
    <article class="data-item">
      <strong>${escapeHtml(formatIsoTime(slot.start_at))} - ${escapeHtml(formatIsoTime(slot.end_at))} · ${escapeHtml(slot.activity_title || "")}</strong>
      <small>${escapeHtml(slot.location || "")} · ${escapeHtml(slot.activity_type || "")} · ${escapeHtml(slot.actual_status || "")} · salience ${escapeHtml(slot.salience || 0)}</small>
    </article>
  `).join("");
  const sourceOptions = [
    ["news", "新闻"],
    ["weather", "天气"],
    ["schedule", "AI 日程"],
    ["calendar_event", "日历事件"],
    ["moment_interaction", "朋友圈互动"],
    ["appointment", "约定"],
    ["memory", "记忆"],
  ].map(([value, label]) => `<option value="${value}">${label}</option>`).join("");
  const slotOptions = state.aiSchedule.map((slot) => `
    <option value="${escapeHtml(slot.slot_id)}">${escapeHtml(formatIsoTime(slot.start_at))} ${escapeHtml(slot.activity_title || "")}</option>
  `).join("");
  const items = state.proactiveEvents.map((event) => `
    <article class="data-item">
      <strong>${escapeHtml(event.status)} · ${escapeHtml(event.source_type)} · priority ${escapeHtml(event.priority)}</strong>
      <div>${escapeHtml(event.title || "")}</div>
      <small>${escapeHtml(event.text || "")}</small>
      <small>scheduled ${escapeHtml(event.scheduled_at || "")} · prepared ${event.prepared ? "yes" : "no"} ${event.prepare_error ? `· ${escapeHtml(event.prepare_error)}` : ""}</small>
    </article>
  `).join("");
  node.innerHTML = `
    <div class="proactive-current-user">当前用户：<strong>${escapeHtml(user.display_name || user.user_id)}</strong><span>${escapeHtml(user.user_id)}</span><span>角色：${escapeHtml(selectedUserCharacterId())}</span></div>
    <form class="compact-form" id="proactiveGenerateForm">
      <label><span>主动来源</span><select name="source_type">${sourceOptions}</select></label>
      <label><span>AI 日程 slot</span><select name="slot_id"><option value="">自动选择</option>${slotOptions}</select></label>
      <label><span>优先级</span><input name="priority" type="number" min="0" max="100" value="80"></label>
      <label><span>标题</span><input name="title" placeholder="后台测试主动消息"></label>
      <label class="wide"><span>内容</span><textarea name="text" placeholder="留空则按来源自动生成测试内容"></textarea></label>
      <label class="inline-check"><input type="checkbox" name="due_now" checked><span>立刻到期</span></label>
      <label class="inline-check"><input type="checkbox" name="manual_only"><span>只生成测试候选</span></label>
      <label class="inline-check"><input type="checkbox" name="prepare"><span>判断后预制对话</span></label>
      <div class="actions">
        <button type="button" id="proactiveGenerate">从来源生成</button>
        <button type="button" class="secondary" id="proactiveJudgeNow">立即判断</button>
      </div>
    </form>
    <section class="data-list">
      <h4>今天的 AI 日程</h4>
      ${scheduleItems || "<small>暂无 AI 日程</small>"}
    </section>
    <pre id="proactiveActionResult">${state.proactiveActionResult ? escapeHtml(pretty(state.proactiveActionResult)) : "等待主动消息测试操作..."}</pre>
    <div class="table-toolbar">
      <label><span>搜索主动消息</span><input id="proactiveSearch" value="${escapeHtml(state.proactivePage.q)}" placeholder="标题 / 内容 / 来源"></label>
      <label><span>每页</span><select id="proactivePageSize">${[10, 20, 50].map((size) => `<option value="${size}" ${state.proactivePage.pageSize === size ? "selected" : ""}>${size}</option>`).join("")}</select></label>
      <button type="button" class="secondary" id="proactiveRefresh">刷新</button>
      <button type="button" id="proactivePrewarm">预生成</button>
    </div>
    <div class="data-list">${items || "<small>暂无主动消息</small>"}</div>
    <div class="pager" id="proactivePager"></div>
  `;
  $("#proactiveGenerate").addEventListener("click", () => generateProactiveFromSource());
  $("#proactiveJudgeNow").addEventListener("click", () => judgeProactiveNow());
  $("#proactiveRefresh").addEventListener("click", async () => {
    state.proactivePage.q = $("#proactiveSearch").value.trim();
    state.proactivePage.pageSize = Number($("#proactivePageSize").value || 10);
    state.proactivePage.page = 1;
    await Promise.all([loadProactiveEvents(), loadAiSchedule()]);
    renderProactiveEditor();
  });
  $("#proactiveSearch").addEventListener("keydown", async (event) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    state.proactivePage.q = $("#proactiveSearch").value.trim();
    state.proactivePage.page = 1;
    await loadProactiveEvents();
    renderProactiveEditor();
  });
  $("#proactivePageSize").addEventListener("change", async () => {
    state.proactivePage.pageSize = Number($("#proactivePageSize").value || 10);
    state.proactivePage.page = 1;
    await loadProactiveEvents();
    renderProactiveEditor();
  });
  $("#proactivePrewarm").addEventListener("click", async () => {
    state.proactiveActionResult = await api("/api/admin/proactive-events/prewarm", {
      method: "POST",
      body: JSON.stringify({ user_id: state.activeUserId, character_id: selectedUserCharacterId(), limit: 4 }),
    });
    await loadProactiveEvents();
    renderProactiveEditor();
  });
  renderPager($("#proactivePager"), state.proactivePage, async (page) => {
    state.proactivePage.page = page;
    await loadProactiveEvents();
    renderProactiveEditor();
  });
}

function compactSchedule(items) {
  const segments = [];
  for (const item of items || []) {
    const prev = segments[segments.length - 1];
    const same = prev
      && prev.activity_title === item.activity_title
      && prev.activity_type === item.activity_type
      && prev.location === item.location
      && prev.actual_status === item.actual_status;
    if (same) {
      prev.end_at = item.end_at;
      prev.salience = Math.max(Number(prev.salience || 0), Number(item.salience || 0));
    } else {
      segments.push({ ...item });
    }
  }
  return segments;
}

function renderTestData() {
  $("#testDataStatus").textContent = `${state.userPage.total || state.users.length} 个用户`;
  $("#testDataStatus").classList.toggle("ok", (state.userPage.total || state.users.length) > 0);
  renderUserTabs();
  renderUserEditor();
  renderRelationEditor();
  renderMemoryEditor();
  renderCalendarEventEditor();
}

async function loadUsers() {
  const params = new URLSearchParams({
    page: String(state.userPage.page),
    page_size: String(state.userPage.pageSize),
    q: state.userPage.q,
  });
  const payload = await api(`/api/admin/users?${params.toString()}`);
  state.users = payload.items || [];
  state.relations = payload.relations || [];
  state.userPage.total = payload.total || state.users.length;
  state.userPage.page = payload.page || state.userPage.page;
  state.userPage.pageSize = payload.page_size || state.userPage.pageSize;
  if (!state.activeUserId || !state.users.some((item) => item.user_id === state.activeUserId)) {
    state.activeUserId = state.users[0]?.user_id || "";
  }
  syncActiveUserCharacter();
}

async function loadMemories() {
  if (!state.activeUserId) {
    state.memories = [];
    state.memoryPage.total = 0;
    return;
  }
  const params = new URLSearchParams({
    page: String(state.memoryPage.page),
    page_size: String(state.memoryPage.pageSize),
    character_id: selectedUserCharacterId(),
    q: state.memoryPage.q,
  });
  const payload = await api(`/api/admin/users/${encodeURIComponent(state.activeUserId)}/memories?${params.toString()}`);
  state.memories = payload.items || [];
  state.memoryPage.total = payload.total || state.memories.length;
  state.memoryPage.page = payload.page || state.memoryPage.page;
  state.memoryPage.pageSize = payload.page_size || state.memoryPage.pageSize;
}

async function loadCalendarEvents() {
  if (!state.activeUserId) {
    state.calendarEvents = [];
    state.calendarPage.total = 0;
    return;
  }
  const params = new URLSearchParams({
    user_id: state.activeUserId,
    character_id: selectedUserCharacterId(),
    page: String(state.calendarPage.page),
    page_size: String(state.calendarPage.pageSize),
    q: state.calendarPage.q,
  });
  const payload = await api(`/api/admin/calendar-events?${params.toString()}`);
  state.calendarEvents = payload.items || [];
  state.calendarPage.total = payload.total || state.calendarEvents.length;
  state.calendarPage.page = payload.page || state.calendarPage.page;
  state.calendarPage.pageSize = payload.page_size || state.calendarPage.pageSize;
}

async function loadProactiveEvents() {
  if (!state.activeUserId) {
    state.proactiveEvents = [];
    state.proactivePage.total = 0;
    return;
  }
  const params = new URLSearchParams({
    user_id: state.activeUserId,
    character_id: selectedUserCharacterId(),
    page: String(state.proactivePage.page),
    page_size: String(state.proactivePage.pageSize),
    q: state.proactivePage.q,
  });
  const payload = await api(`/api/admin/proactive-events?${params.toString()}`);
  state.proactiveEvents = payload.items || [];
  state.proactivePage.total = payload.total || state.proactiveEvents.length;
  state.proactivePage.page = payload.page || state.proactivePage.page;
  state.proactivePage.pageSize = payload.page_size || state.proactivePage.pageSize;
}

async function loadAiSchedule() {
  if (!state.activeUserId) {
    state.aiSchedule = [];
    return;
  }
  const params = new URLSearchParams({ user_id: state.activeUserId, character_id: selectedUserCharacterId() });
  const payload = await api(`/api/admin/ai-schedule/today?${params.toString()}`);
  state.aiSchedule = payload.items || [];
}

async function loadTestData() {
  await loadUsers();
  await Promise.all([loadMemories(), loadCalendarEvents(), loadProactiveEvents(), loadAiSchedule()]);
  renderTestData();
}

function proactiveFormPayload() {
  const form = $("#proactiveGenerateForm");
  return {
    user_id: state.activeUserId,
    character_id: selectedUserCharacterId(),
    source_type: form.elements.source_type.value,
    slot_id: form.elements.slot_id.value,
    priority: Number(form.elements.priority.value || 80),
    title: form.elements.title.value.trim(),
    text: form.elements.text.value.trim(),
    due_now: form.elements.due_now.checked,
    manual_only: form.elements.manual_only.checked,
    prepare: form.elements.prepare.checked,
    ignore_next_check: true,
    idle_seconds: 120,
    input_active: false,
  };
}

async function generateProactiveFromSource() {
  state.proactiveActionResult = await api("/api/admin/proactive-events/generate", {
    method: "POST",
    body: JSON.stringify(proactiveFormPayload()),
  });
  state.proactivePage.page = 1;
  await Promise.all([loadProactiveEvents(), loadAiSchedule()]);
  renderProactiveEditor();
}

async function judgeProactiveNow() {
  state.proactiveActionResult = await api("/api/admin/proactive-events/judge", {
    method: "POST",
    body: JSON.stringify(proactiveFormPayload()),
  });
  await loadProactiveEvents();
  renderProactiveEditor();
}

async function createUser() {
  const characterId = selectedUserCharacterId();
  const created = await api("/api/admin/users", { method: "POST", body: JSON.stringify({ display_name: "测试用户", story_completed: true, character_id: characterId }) });
  state.activeUserId = created.user_id;
  state.activeUserCharacterId = characterId;
  state.userPage.page = 1;
  state.userPage.q = "";
  await loadTestData();
}

async function saveUser(event) {
  event.preventDefault();
  const form = $("#userForm");
  try {
    const payload = {
      display_name: form.elements.display_name.value.trim(),
      timezone: form.elements.timezone.value.trim(),
      sleep_start: form.elements.sleep_start.value.trim(),
      sleep_end: form.elements.sleep_end.value.trim(),
      proactive_daily_limit: form.elements.proactive_daily_limit.value.trim(),
      interest_topics: form.elements.interest_topics.value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean),
      profile: parseJsonField(form.elements.profile, {}),
      story_completed: form.elements.story_completed.checked,
      tts_enabled: form.elements.tts_enabled.checked,
      notifications_enabled: form.elements.notifications_enabled.checked,
      news_enabled: form.elements.news_enabled.checked,
    };
    await api(`/api/admin/users/${encodeURIComponent(state.activeUserId)}`, { method: "PUT", body: JSON.stringify(payload) });
    await loadTestData();
  } catch (error) {
    $("#userEditor").insertAdjacentHTML("beforeend", `<pre>${escapeHtml(pretty({ ok: false, message: error.message }))}</pre>`);
  }
}

async function deleteUser() {
  if (!window.confirm("确定删除这个用户和关联测试数据？")) return;
  await api(`/api/admin/users/${encodeURIComponent(state.activeUserId)}`, { method: "DELETE" });
  state.activeUserId = "";
  await loadTestData();
}

async function saveRelation(event) {
  event.preventDefault();
  const form = $("#relationForm");
  const payload = {
    character_id: selectedUserCharacterId(),
    affection: Number(form.elements.affection.value || 0),
    trust: Number(form.elements.trust.value || 0),
    dependency: Number(form.elements.dependency.value || 0),
    mood: Number(form.elements.mood.value || 0),
    relationship_stage: form.elements.relationship_stage.value.trim(),
  };
  await api(`/api/admin/users/${encodeURIComponent(state.activeUserId)}/relation`, { method: "PUT", body: JSON.stringify(payload) });
  await loadTestData();
}

async function createMemory(event) {
  event.preventDefault();
  const form = $("#memoryForm");
  try {
    const payload = {
      character_id: form.elements.character_id.value.trim() || selectedUserCharacterId(),
      layer: form.elements.layer.value.trim() || "chat",
      content: form.elements.content.value.trim(),
      importance: Number(form.elements.importance.value || 0.5),
      confidence: Number(form.elements.confidence.value || 0.8),
      tags: form.elements.tags.value.split(",").map((item) => item.trim()).filter(Boolean),
      metadata: parseJsonField(form.elements.metadata, {}),
    };
    await api(`/api/admin/users/${encodeURIComponent(state.activeUserId)}/memories`, { method: "POST", body: JSON.stringify(payload) });
    state.memoryPage.page = 1;
    await loadMemories();
    renderMemoryEditor();
  } catch (error) {
    $("#memoryEditor").insertAdjacentHTML("beforeend", `<pre>${escapeHtml(pretty({ ok: false, message: error.message }))}</pre>`);
  }
}

async function toggleMemory(memoryId) {
  const memory = state.memories.find((item) => item.memory_id === memoryId);
  await api(`/api/admin/memories/${encodeURIComponent(memoryId)}`, { method: "PUT", body: JSON.stringify({ hidden: !memory?.hidden }) });
  await loadMemories();
  renderMemoryEditor();
}

async function deleteMemory(memoryId) {
  await api(`/api/admin/memories/${encodeURIComponent(memoryId)}`, { method: "DELETE" });
  await loadMemories();
  renderMemoryEditor();
}

async function saveCalendarEvent(event) {
  event.preventDefault();
  const form = $("#calendarEventForm");
  const payload = {
    user_id: state.activeUserId,
    character_id: selectedUserCharacterId(),
    date: form.elements.date.value,
    title: form.elements.title.value.trim(),
    category: form.elements.category.value,
    description: form.elements.description.value.trim(),
    salience: Number(form.elements.salience.value || 80),
    repeats_yearly: form.elements.repeats_yearly.checked,
    hidden: form.elements.hidden.checked,
    source_type: "admin",
  };
  const editing = Boolean(state.editingCalendarEventId);
  await api(editing ? `/api/admin/calendar-events/${encodeURIComponent(state.editingCalendarEventId)}` : "/api/admin/calendar-events", {
    method: editing ? "PUT" : "POST",
    body: JSON.stringify(payload),
  });
  state.editingCalendarEventId = "";
  state.calendarPage.page = 1;
  await loadCalendarEvents();
  renderCalendarEventEditor();
}

async function toggleCalendarEvent(eventId) {
  const item = state.calendarEvents.find((event) => event.event_id === eventId);
  await api(`/api/admin/calendar-events/${encodeURIComponent(eventId)}`, { method: "PUT", body: JSON.stringify({ hidden: !item?.hidden }) });
  await loadCalendarEvents();
  renderCalendarEventEditor();
}

async function deleteCalendarEvent(eventId) {
  await api(`/api/admin/calendar-events/${encodeURIComponent(eventId)}`, { method: "DELETE" });
  await loadCalendarEvents();
  renderCalendarEventEditor();
}

function renderCards() {
  const grid = $("#providerGrid");
  grid.innerHTML = "";
  state.cards.clear();
  for (const kind of kinds) {
    const card = document.createElement("article");
    card.className = "card";
    card.dataset.kind = kind.id;
    const current = currentProvider(kind.id);
    const presets = providerList(kind.id);
    const selected = current ? current.provider : (presets[0] || {}).provider;
    card.innerHTML = `
      <header>
        <div>
          <p class="eyebrow">${kind.id}</p>
          <h2>${kind.title}</h2>
        </div>
        <span class="badge">未配置</span>
      </header>
      <div class="provider-row">
        <label><span>服务类型</span><select class="provider-select"></select></label>
        <a class="doc-link" target="_blank" rel="noreferrer">官方文档</a>
      </div>
      <h3 class="provider-title"></h3>
      <p class="description"></p>
      <div class="fields"></div>
      <div class="model-picker" hidden>
        <label><span>已拉取模型</span><select class="model-select"></select></label>
        <small>选择后会自动写入模型字段，保存后生效。</small>
      </div>
      <details class="advanced">
        <summary>Advanced JSON / 可选扩展字段</summary>
        <div class="advanced-fields"></div>
      </details>
      <div class="actions">
        <button class="save">保存</button>
        <button class="test secondary">真实测试</button>
        <button class="models secondary">拉取模型</button>
        <button class="curl secondary">复制 curl 预览</button>
      </div>
      <pre class="result">等待配置...</pre>
    `;
    const select = $(".provider-select", card);
    for (const preset of presets) {
      const option = document.createElement("option");
      option.value = preset.provider;
      option.textContent = preset.label || preset.provider;
      select.appendChild(option);
    }
    select.value = selected;
    select.addEventListener("change", () => renderFields(card, kind.id, select.value));
    $(".save", card).addEventListener("click", () => saveProvider(kind.id, card, false));
    $(".test", card).addEventListener("click", () => saveProvider(kind.id, card, true));
    $(".models", card).addEventListener("click", () => loadModels(kind.id, card));
    $(".curl", card).addEventListener("click", async () => {
      const preview = curlPreview(kind.id, card);
      setCardResult(card, preview);
      try {
        await navigator.clipboard.writeText(preview);
      } catch (_) {
        // Clipboard may be blocked on non-HTTPS localhost; preview is still visible.
      }
    });
    renderFields(card, kind.id, select.value);
    state.cards.set(kind.id, card);
    grid.appendChild(card);
  }
}

function renderStatus(configured) {
  for (const kind of kinds) {
    const node = $(`#status-${kind.id}`);
    const provider = currentProvider(kind.id);
    const metadata = provider?.metadata || {};
    node.textContent = configured[kind.id] ? "已就绪" : "未完成";
    node.style.color = configured[kind.id] ? "#286241" : "#a65359";
    node.parentElement.title = provider
      ? [
        `${provider.label || provider.provider} / ${provider.model || provider.provider_id}`,
        metadata.last_test_message ? `最近测试：${metadata.last_test_ok ? "通过" : "失败"} · ${metadata.last_test_message}` : "",
        metadata.last_test_endpoint ? `Endpoint: ${metadata.last_test_endpoint}` : "",
      ].filter(Boolean).join("\n")
      : "";
  }
}

function runtimeStatusLabel(item) {
  if (item.stale || item.status === "stale") return "可能中断";
  if (item.running || item.status === "running") return "运行中";
  if (item.status === "error") return "错误";
  if (item.status === "warn") return "注意";
  return "成功";
}

function runtimeStatusClass(item) {
  if (item.stale || item.status === "stale") return "stale";
  if (item.running || item.status === "running") return "running";
  if (item.status === "error") return "error";
  if (item.status === "warn") return "warn";
  return "ok";
}

function firstRuntimeValue(...values) {
  return values.find((value) => value !== undefined && value !== null && value !== "" && !(Array.isArray(value) && value.length === 0));
}

function runtimeDetailSection(title, value) {
  if (value === undefined || value === null || value === "" || (Array.isArray(value) && value.length === 0)) return "";
  return `
    <section class="runtime-detail-section">
      <h3>${escapeHtml(title)}</h3>
      <pre>${escapeHtml(typeof value === "string" ? value : pretty(value))}</pre>
    </section>
  `;
}

function runtimeItemById(id) {
  return state.runtimeLogs.find((item) => item.id === id) || null;
}

function runtimeInspecting() {
  return [...document.querySelectorAll("#runtimeLogList details")].some((details) => details.open);
}

function captureRuntimeViewState() {
  const list = $("#runtimeLogList");
  if (list) {
    state.runtimeOpenTraces = new Set(
      [...list.querySelectorAll("details[data-runtime-trace-id]")]
        .filter((details) => details.open)
        .map((details) => details.dataset.runtimeTraceId)
    );
    state.runtimeOpenItems = new Set(
      [...list.querySelectorAll("details[data-runtime-id]")]
        .filter((details) => details.open)
        .map((details) => details.dataset.runtimeId)
    );
  }
  return { scrollY: window.scrollY || document.documentElement.scrollTop || 0 };
}

function restoreRuntimeViewState(view) {
  if (!view) return;
  window.requestAnimationFrame(() => {
    window.scrollTo({ top: view.scrollY || 0 });
  });
}

function setRuntimeNotice(visible) {
  const notice = $("#runtimeNewNotice");
  if (notice) notice.hidden = !visible;
}

function runtimeDetailActions(item) {
  return `
    <div class="runtime-detail-actions">
      <button type="button" class="secondary" data-runtime-jump="${escapeHtml(item.prev_id || "")}" ${item.prev_id ? "" : "disabled"}>上一步输入</button>
      <button type="button" class="secondary" data-runtime-jump="${escapeHtml(item.next_id || "")}" ${item.next_id ? "" : "disabled"}>下一步输出</button>
      <button type="button" class="secondary" data-runtime-trace="${escapeHtml(item.trace_id || "")}" ${item.trace_id ? "" : "disabled"}>只看本 trace</button>
      <button type="button" class="secondary" data-runtime-feature="${escapeHtml(item.feature || "")}" ${item.feature ? "" : "disabled"}>只看本功能</button>
    </div>
  `;
}

function runtimeDetailHtml(item) {
  const details = item.details || {};
  const start = details.start || {};
  const end = details.end || {};
  const point = details.point || {};
  const points = details.points || [];
  const judgementPoints = points.filter((entry) => ["reply_llm_judgement", "reply_output_ready", "reply_context_ready"].includes(entry.event));
  const prev = runtimeItemById(item.prev_id);
  const next = runtimeItemById(item.next_id);
  const references = firstRuntimeValue(start.references, end.references, point.references, start.input?.references, end.input?.references, point.input?.references);
  const errorPayload = firstRuntimeValue(
    end.error_type || end.message ? { error_type: end.error_type, message: end.message } : null,
    point.error_type || point.message ? { error_type: point.error_type, message: point.message } : null,
  );
  return [
    runtimeDetailActions(item),
    runtimeDetailSection("上一步输入", prev ? firstRuntimeValue(prev.input_summary, prev.details?.start?.input, prev.details?.point?.input) : ""),
    runtimeDetailSection("下一步输出", next ? firstRuntimeValue(next.output_summary, next.details?.end?.output, next.details?.end?.response, next.details?.point?.output) : ""),
    runtimeDetailSection("输入", firstRuntimeValue(start.input, point.input, end.input)),
    runtimeDetailSection("引用内容", references),
    runtimeDetailSection("LLM / 外部请求", firstRuntimeValue(start.request, point.request)),
    runtimeDetailSection("响应", firstRuntimeValue(end.response, end.response_text, end.raw_content, point.response)),
    runtimeDetailSection("判断", judgementPoints.length ? judgementPoints : firstRuntimeValue(end.keys, point.keys)),
    runtimeDetailSection("错误", errorPayload),
    runtimeDetailSection("原始 JSON", details.raw_events || [point]),
  ].join("");
}

function renderRuntimeFeatures() {
  const select = $("#runtimeFeature");
  const selected = state.runtimeFilters.feature;
  select.innerHTML = `<option value="">全部</option>${state.runtimeFeatures.map((feature) => `<option value="${escapeHtml(feature)}">${escapeHtml(feature)}</option>`).join("")}`;
  select.value = selected;
}

function renderRuntimeTraceFilter() {
  const node = $("#runtimeTraceFilter");
  if (!node) return;
  if (!state.runtimeFilters.traceId) {
    node.hidden = true;
    node.innerHTML = "";
    return;
  }
  node.hidden = false;
  node.innerHTML = `
    <span>trace: ${escapeHtml(state.runtimeFilters.traceId)}</span>
    <button type="button" class="secondary" data-runtime-clear-trace>清除 trace 筛选</button>
  `;
}

function runtimeTraceByItemId(id) {
  for (const trace of state.runtimeTraces || []) {
    const items = trace.items || [];
    if (items.some((item) => item.id === id)) return trace;
  }
  return null;
}

function runtimeTraceTitle(trace) {
  return trace.summary || trace.feature || trace.trace_id || "trace";
}

function renderRuntimeReferenceBadges(item) {
  const badges = [];
  if (item.references_summary) badges.push(`<span class="runtime-reference-chip">${escapeHtml(item.references_summary)}</span>`);
  if (item.input_summary) badges.push(`<span class="runtime-mini-summary">输入：${escapeHtml(item.input_summary)}</span>`);
  if (item.output_summary) badges.push(`<span class="runtime-mini-summary">输出：${escapeHtml(item.output_summary)}</span>`);
  return badges.length ? `<div class="runtime-step-meta">${badges.join("")}</div>` : "";
}

function renderRuntimeTraceItem(item, index) {
  const statusClass = runtimeStatusClass(item);
  const title = [item.feature, item.stage].filter(Boolean).join(" / ") || item.event || "runtime";
  const trace = item.trace_id ? `trace ${item.trace_id}` : "no trace";
  const span = item.span_id ? `span ${item.span_id}` : "";
  const open = state.runtimeOpenItems.has(item.id) ? "open" : "";
  const selected = state.runtimeSelectedId === item.id ? "selected" : "";
  const childCount = (item.children_ids || []).length;
  return `
    <article class="runtime-step runtime-item ${statusClass} ${selected}" data-runtime-item-id="${escapeHtml(item.id || "")}">
      <div class="runtime-step-index" aria-hidden="true">${index + 1}</div>
      <div class="runtime-step-content">
        <button type="button" class="runtime-node-button" data-runtime-node-id="${escapeHtml(item.id || "")}">
          <span class="runtime-state ${statusClass}">${escapeHtml(runtimeStatusLabel(item))}</span>
          <span class="runtime-node-main">
            <strong>${escapeHtml(title)}</strong>
            <small>${escapeHtml(formatDateTime(item.started_ts))} · ${escapeHtml(formatDuration(item.elapsed_ms))} · ${escapeHtml(trace)} ${escapeHtml(span)}</small>
          </span>
        </button>
        <p>${escapeHtml(item.summary || item.purpose || item.event || "")}</p>
        <div class="runtime-node-tags">
          ${item.feature ? `<button type="button" class="runtime-feature-chip" data-runtime-feature="${escapeHtml(item.feature)}">${escapeHtml(item.feature)}</button>` : ""}
          ${item.parent_id ? `<span class="runtime-relation-chip">父节点</span>` : ""}
          ${childCount ? `<span class="runtime-relation-chip">子节点 ${childCount}</span>` : ""}
        </div>
        ${renderRuntimeReferenceBadges(item)}
        <details data-runtime-id="${escapeHtml(item.id || "")}" ${open}>
          <summary>查看详情</summary>
          <div class="runtime-detail-grid">${runtimeDetailHtml(item)}</div>
        </details>
      </div>
    </article>
  `;
}

function renderRuntimeTraceCard(trace) {
  const items = trace.items || [];
  const statusClass = runtimeStatusClass(trace);
  const open = state.runtimeOpenTraces.has(trace.trace_id) ? "open" : "";
  const selected = items.some((item) => item.id === state.runtimeSelectedId) ? "selected" : "";
  const canFilterTrace = trace.trace_id && !trace.is_legacy;
  return `
    <details class="runtime-trace-card ${statusClass} ${selected}" data-runtime-trace-id="${escapeHtml(trace.trace_id || "")}" ${open}>
      <summary class="runtime-trace-summary">
        <span class="runtime-state ${statusClass}">${escapeHtml(runtimeStatusLabel(trace))}</span>
        <span class="runtime-trace-main">
          <strong>${escapeHtml(runtimeTraceTitle(trace))}</strong>
          <small>${escapeHtml(formatDateTime(trace.started_ts))} · ${escapeHtml(formatDuration(trace.elapsed_ms))} · ${items.length} 个节点 · ${escapeHtml(trace.trace_id || "no trace")}</small>
        </span>
        <span class="runtime-trace-actions">
          ${trace.feature ? `<button type="button" class="secondary" data-runtime-feature="${escapeHtml(trace.feature)}">只看本功能</button>` : ""}
          <button type="button" class="secondary" data-runtime-trace="${escapeHtml(trace.trace_id || "")}" ${canFilterTrace ? "" : "disabled"}>只看本 trace</button>
        </span>
      </summary>
      <div class="runtime-trace-flow">
        ${items.map((item, index) => renderRuntimeTraceItem(item, index)).join("")}
      </div>
    </details>
  `;
}

function renderRuntimeLogs() {
  const list = $("#runtimeLogList");
  const traceCount = state.runtimeTraces.length || 0;
  $("#runtimeLogStatus").textContent = `${traceCount} traces / ${state.runtimeLogs.length} 条`;
  $("#runtimeLogStatus").classList.toggle("ok", state.runtimeLogs.length > 0);
  renderRuntimeFeatures();
  renderRuntimeTraceFilter();
  const loadOlder = $("#runtimeLoadOlder");
  if (loadOlder) loadOlder.disabled = !state.runtimeHasMore || !state.runtimeOldestTs;
  if (!state.runtimeTraces.length) {
    list.innerHTML = `<div class="editor-placeholder">暂无运行日志。</div>`;
    return;
  }
  list.innerHTML = state.runtimeTraces.map(renderRuntimeTraceCard).join("");
  list.querySelectorAll("details[data-runtime-trace-id]").forEach((details) => {
    details.addEventListener("toggle", () => {
      if (details.open) state.runtimeOpenTraces.add(details.dataset.runtimeTraceId);
      else state.runtimeOpenTraces.delete(details.dataset.runtimeTraceId);
    });
  });
  list.querySelectorAll("details[data-runtime-id]").forEach((details) => {
    details.addEventListener("toggle", () => {
      if (details.open) state.runtimeOpenItems.add(details.dataset.runtimeId);
      else state.runtimeOpenItems.delete(details.dataset.runtimeId);
    });
  });
}

function mergeRuntimeItems(existing, incoming) {
  const byId = new Map();
  [...existing, ...incoming].forEach((item) => {
    if (item && item.id) byId.set(item.id, item);
  });
  return [...byId.values()].sort((a, b) => Number(b.started_ts || 0) - Number(a.started_ts || 0));
}

function mergeRuntimeFlows(existing, incoming) {
  const byTrace = new Map();
  for (const flow of existing || []) byTrace.set(flow.trace_id, flow);
  for (const flow of incoming || []) {
    const current = byTrace.get(flow.trace_id);
    if (!current) {
      byTrace.set(flow.trace_id, flow);
      continue;
    }
    const nodeIds = new Set((current.nodes || []).map((node) => node.id));
    current.nodes = [...(current.nodes || []), ...(flow.nodes || []).filter((node) => !nodeIds.has(node.id))];
    const edgeIds = new Set((current.edges || []).map((edge) => `${edge.from}->${edge.to}`));
    current.edges = [...(current.edges || []), ...(flow.edges || []).filter((edge) => !edgeIds.has(`${edge.from}->${edge.to}`))];
  }
  return [...byTrace.values()].sort((a, b) => Number(b.started_ts || 0) - Number(a.started_ts || 0));
}

function mergeRuntimeTraces(existing, incoming) {
  const byTrace = new Map();
  for (const trace of existing || []) {
    if (trace && trace.trace_id) byTrace.set(trace.trace_id, { ...trace, items: [...(trace.items || [])], nodes: [...(trace.nodes || [])], edges: [...(trace.edges || [])] });
  }
  for (const trace of incoming || []) {
    if (!trace || !trace.trace_id) continue;
    const current = byTrace.get(trace.trace_id);
    if (!current) {
      byTrace.set(trace.trace_id, trace);
      continue;
    }
    const itemIds = new Set((current.items || []).map((item) => item.id));
    current.items = mergeRuntimeItems(current.items || [], (trace.items || []).filter((item) => !itemIds.has(item.id)));
    const nodeIds = new Set((current.nodes || []).map((node) => node.id));
    current.nodes = [...(current.nodes || []), ...(trace.nodes || []).filter((node) => !nodeIds.has(node.id))];
    const edgeIds = new Set((current.edges || []).map((edge) => `${edge.from}->${edge.to}`));
    current.edges = [...(current.edges || []), ...(trace.edges || []).filter((edge) => !edgeIds.has(`${edge.from}->${edge.to}`))];
    current.status = trace.status || current.status;
    current.running = Boolean(trace.running || current.running);
    current.stale = Boolean(trace.stale || current.stale);
    current.elapsed_ms = Math.max(Number(current.elapsed_ms || 0), Number(trace.elapsed_ms || 0));
    current.started_ts = Math.min(Number(current.started_ts || trace.started_ts || 0), Number(trace.started_ts || current.started_ts || 0));
    current.ended_ts = Math.max(Number(current.ended_ts || 0), Number(trace.ended_ts || 0)) || current.ended_ts || trace.ended_ts || null;
  }
  return [...byTrace.values()].sort((a, b) => Number(b.started_ts || 0) - Number(a.started_ts || 0));
}

function applyRuntimePayload(payload, { append = false, preserve = true } = {}) {
  const view = preserve ? captureRuntimeViewState() : null;
  const items = payload.items || [];
  state.runtimeLogs = append ? mergeRuntimeItems(state.runtimeLogs, items) : items;
  state.runtimeFeatures = payload.features || state.runtimeFeatures || [];
  state.runtimeFlows = append ? mergeRuntimeFlows(state.runtimeFlows, payload.flows || []) : (payload.flows || []);
  state.runtimeTraces = append ? mergeRuntimeTraces(state.runtimeTraces, payload.traces || []) : (payload.traces || []);
  state.runtimeServerTime = payload.server_time || 0;
  state.runtimeTotal = payload.total || state.runtimeLogs.length;
  state.runtimeLegacyCount = payload.legacy_count || 0;
  state.runtimeHasMore = Boolean(payload.has_more);
  if (append) {
    const values = [state.runtimeOldestTs, payload.oldest_ts].filter((value) => value !== null && value !== undefined);
    state.runtimeOldestTs = values.length ? Math.min(...values) : null;
  } else {
    state.runtimeOldestTs = payload.oldest_ts || null;
  }
  state.runtimePendingPayload = null;
  setRuntimeNotice(false);
  renderRuntimeLogs();
  restoreRuntimeViewState(view);
}

function runtimePayloadHasChanges(payload) {
  const incomingTraces = (payload.traces || []).map((trace) => `${trace.trace_id}:${trace.items?.length || 0}:${trace.elapsed_ms || 0}:${trace.status || ""}`).join("|");
  const currentTraces = state.runtimeTraces
    .slice(0, (payload.traces || []).length)
    .map((trace) => `${trace.trace_id}:${trace.items?.length || 0}:${trace.elapsed_ms || 0}:${trace.status || ""}`)
    .join("|");
  const incomingItems = (payload.items || []).map((item) => item.id).join("|");
  const currentItems = state.runtimeLogs.slice(0, (payload.items || []).length).map((item) => item.id).join("|");
  return incomingTraces !== currentTraces || incomingItems !== currentItems || Number(payload.total || 0) !== Number(state.runtimeTotal || 0);
}

async function loadRuntimeLogs({ deferIfInspecting = false, append = false } = {}) {
  if (append && !state.runtimeOldestTs) return;
  const params = new URLSearchParams({
    limit: String(state.runtimeFilters.limit || 200),
    feature: state.runtimeFilters.feature || "",
    status: state.runtimeFilters.status || "",
    q: state.runtimeFilters.q || "",
    trace_id: state.runtimeFilters.traceId || "",
    include_legacy: String(Boolean(state.runtimeFilters.includeLegacy)),
    include_cache_hits: String(Boolean(state.runtimeFilters.includeCacheHits)),
  });
  if (append && state.runtimeOldestTs) params.set("before_ts", String(state.runtimeOldestTs));
  const payload = await api(`/api/admin/runtime-logs?${params.toString()}`);
  if (deferIfInspecting && runtimeInspecting()) {
    if (runtimePayloadHasChanges(payload)) {
      state.runtimePendingPayload = { payload, append };
      setRuntimeNotice(true);
    }
    return;
  }
  applyRuntimePayload(payload, { append, preserve: true });
}

function readRuntimeFilters() {
  state.runtimeFilters.feature = $("#runtimeFeature").value;
  state.runtimeFilters.status = $("#runtimeStatus").value;
  state.runtimeFilters.limit = Number($("#runtimeLimit").value || 200);
  state.runtimeFilters.q = $("#runtimeSearch").value.trim();
  state.runtimeFilters.includeLegacy = $("#runtimeIncludeLegacy") ? $("#runtimeIncludeLegacy").checked : true;
  state.runtimeFilters.includeCacheHits = $("#runtimeIncludeCacheHits") ? $("#runtimeIncludeCacheHits").checked : false;
}

function restartRuntimeAutoRefresh() {
  if (state.runtimeTimer) {
    window.clearInterval(state.runtimeTimer);
    state.runtimeTimer = null;
  }
  const auto = $("#runtimeAutoRefresh");
  if (!auto || !auto.checked) return;
  state.runtimeTimer = window.setInterval(async () => {
    if (state.currentPage !== "runtime-logs" || !$("#runtimeAutoRefresh").checked) return;
    try {
      readRuntimeFilters();
      await loadRuntimeLogs({ deferIfInspecting: true });
    } catch (_) {
      // Keep the page quiet during transient backend reloads.
    }
  }, 5000);
}

async function setRuntimeFeatureFilter(feature) {
  state.runtimeFilters.feature = feature || "";
  const select = $("#runtimeFeature");
  if (select) select.value = state.runtimeFilters.feature;
  await loadRuntimeLogs();
}

async function setRuntimeTraceFilter(traceId) {
  state.runtimeFilters.traceId = traceId || "";
  if (traceId) {
    state.runtimeFilters.feature = "";
    const feature = $("#runtimeFeature");
    if (feature) feature.value = "";
  }
  await loadRuntimeLogs();
}

function selectRuntimeItem(id) {
  if (!id) return;
  const item = runtimeItemById(id);
  if (!item) return;
  state.runtimeSelectedId = id;
  state.runtimeOpenItems.add(id);
  const trace = runtimeTraceByItemId(id);
  if (trace?.trace_id) state.runtimeOpenTraces.add(trace.trace_id);
  renderRuntimeLogs();
  const node = [...document.querySelectorAll("[data-runtime-item-id]")].find((element) => element.dataset.runtimeItemId === id)
    || [...document.querySelectorAll("[data-runtime-id]")].find((element) => element.dataset.runtimeId === id);
  if (node) node.scrollIntoView({ block: "center", behavior: "smooth" });
}

function bindRuntimeLogs() {
  $("#runtimeRefresh").addEventListener("click", async () => {
    readRuntimeFilters();
    await loadRuntimeLogs();
  });
  $("#runtimeNewNotice").addEventListener("click", () => {
    if (state.runtimePendingPayload) {
      applyRuntimePayload(state.runtimePendingPayload.payload, { append: state.runtimePendingPayload.append, preserve: true });
    }
  });
  $("#runtimeLoadOlder").addEventListener("click", async () => {
    readRuntimeFilters();
    await loadRuntimeLogs({ append: true });
  });
  $("#runtimeSearch").addEventListener("keydown", async (event) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    readRuntimeFilters();
    await loadRuntimeLogs();
  });
  ["runtimeFeature", "runtimeStatus", "runtimeLimit", "runtimeIncludeLegacy", "runtimeIncludeCacheHits"].forEach((id) => {
    $(`#${id}`).addEventListener("change", async () => {
      readRuntimeFilters();
      await loadRuntimeLogs();
    });
  });
  $("#runtimeLogList").addEventListener("click", async (event) => {
    const jump = event.target.closest("[data-runtime-jump]");
    if (jump) {
      event.preventDefault();
      event.stopPropagation();
      selectRuntimeItem(jump.dataset.runtimeJump);
      return;
    }
    const trace = event.target.closest("[data-runtime-trace]");
    if (trace) {
      event.preventDefault();
      event.stopPropagation();
      await setRuntimeTraceFilter(trace.dataset.runtimeTrace);
      return;
    }
    const feature = event.target.closest("[data-runtime-feature]");
    if (feature) {
      event.preventDefault();
      event.stopPropagation();
      await setRuntimeFeatureFilter(feature.dataset.runtimeFeature);
      return;
    }
    const node = event.target.closest("[data-runtime-node-id]");
    if (node) {
      event.preventDefault();
      event.stopPropagation();
      selectRuntimeItem(node.dataset.runtimeNodeId);
      return;
    }
  });
  $("#runtimeLogList").addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    const node = event.target.closest("[data-runtime-node-id]");
    if (!node) return;
    event.preventDefault();
    selectRuntimeItem(node.dataset.runtimeNodeId);
  });
  $("#runtimeTraceFilter").addEventListener("click", async (event) => {
    if (!event.target.closest("[data-runtime-clear-trace]")) return;
    await setRuntimeTraceFilter("");
  });
  $("#runtimeAutoRefresh").addEventListener("change", restartRuntimeAutoRefresh);
  restartRuntimeAutoRefresh();
}

async function loadStatus() {
  const status = await api("/api/admin/status");
  state.providers = status.providers || [];
  renderStatus(status.configured || {});
  renderCards();
}

async function loadPairing() {
  const pairing = await api("/api/admin/pairing");
  $("#serverUrl").textContent = pairing.server_url;
  $("#adminUrl").textContent = `管理台：${pairing.admin_url}`;
}

async function runDebug(path) {
  $("#debugResult").textContent = "执行中...";
  try {
    const body = path.endsWith("advance-time")
      ? { local_time: new Date().toISOString() }
      : {};
    $("#debugResult").textContent = pretty(await api(path, { method: "POST", body: JSON.stringify(body) }));
  } catch (error) {
    $("#debugResult").textContent = pretty({ ok: false, message: error.message });
  }
}

function bindAdminNavigation() {
  document.querySelectorAll("[data-page-target]").forEach((button) => {
    button.addEventListener("click", () => switchPage(button.dataset.pageTarget));
  });
  $("#userCreate").addEventListener("click", createUser);
  $("#userRefresh").addEventListener("click", async () => {
    state.userPage.q = $("#userSearch").value.trim();
    state.userPage.pageSize = Number($("#userPageSize").value || 20);
    state.userPage.page = 1;
    await loadTestData();
  });
  $("#userSearch").addEventListener("keydown", async (event) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    state.userPage.q = $("#userSearch").value.trim();
    state.userPage.page = 1;
    await loadTestData();
  });
  $("#userPageSize").addEventListener("change", async () => {
    state.userPage.pageSize = Number($("#userPageSize").value || 20);
    state.userPage.page = 1;
    await loadTestData();
  });
  switchPage(state.currentPage);
}

const LIVE2D_AREA_COLORS = ["#ff6b8a", "#6bc6ff", "#ffd166", "#8df0b3", "#c792ff", "#ffa36b"];
const LIVE2D_COORD_PRECISION = 2;
const LIVE2D_MODEL_MIN = -0.5;
const LIVE2D_MODEL_MAX = 1.5;
const LIVE2D_FULL_BODY_BOUNDS = { left: 0, top: 0, right: 1, bottom: 1 };

function live2dRoundCoord(value) {
  const factor = 10 ** LIVE2D_COORD_PRECISION;
  return Math.round(Number(value) * factor) / factor;
}

function live2dClampModelCoord(value, min = LIVE2D_MODEL_MIN, max = LIVE2D_MODEL_MAX) {
  return live2dClamp(value, min, max);
}

function live2dNormalizeAreaBounds(area) {
  if (!area) return area;
  area.left = live2dClampModelCoord(live2dRoundCoord(area.left));
  area.top = live2dClampModelCoord(live2dRoundCoord(area.top));
  area.right = live2dClampModelCoord(live2dRoundCoord(area.right));
  area.bottom = live2dClampModelCoord(live2dRoundCoord(area.bottom));
  const minSize = live2dRoundCoord(0.04);
  if (area.right - area.left < minSize) {
    area.right = live2dClampModelCoord(Math.min(LIVE2D_MODEL_MAX, area.left + minSize));
  }
  if (area.bottom - area.top < minSize) {
    area.bottom = live2dClampModelCoord(Math.min(LIVE2D_MODEL_MAX, area.top + minSize));
  }
  return area;
}

function live2dSelectedArea() {
  return state.live2dHitAreas.find((item) => item.area_id === state.live2dSelectedAreaId) || null;
}

function setLive2dAreaForm(area) {
  const form = $("#live2dAreaForm");
  if (!form || !area) return;
  live2dNormalizeAreaBounds(area);
  form.elements.area_id.value = area.area_id || "";
  form.elements.label.value = area.label || "";
  form.elements.left.value = live2dRoundCoord(area.left ?? 0).toFixed(LIVE2D_COORD_PRECISION);
  form.elements.top.value = live2dRoundCoord(area.top ?? 0).toFixed(LIVE2D_COORD_PRECISION);
  form.elements.right.value = live2dRoundCoord(area.right ?? 1).toFixed(LIVE2D_COORD_PRECISION);
  form.elements.bottom.value = live2dRoundCoord(area.bottom ?? 1).toFixed(LIVE2D_COORD_PRECISION);
  form.elements.priority.value = area.priority ?? 0;
  form.elements.base_cooldown_ms.value = area.base_cooldown_ms ?? 1400;
  form.elements.enabled.checked = Boolean(area.enabled);
  form.elements.flirt_hint.checked = Boolean(area.flirt_hint);
}

function renderLive2dAreaList() {
  const list = $("#live2dAreaList");
  if (!list) return;
  list.innerHTML = "";
  if (!state.live2dHitAreas.length) {
    list.innerHTML = `<div class="editor-placeholder">还没有触摸区域。</div>`;
    return;
  }
  for (const area of state.live2dHitAreas) {
    const item = document.createElement("button");
    item.type = "button";
    item.className = `live2d-area-item${area.area_id === state.live2dSelectedAreaId ? " active" : ""}`;
    item.innerHTML = `<strong>${escapeHtml(area.label || area.area_id)}</strong><small>${escapeHtml(area.area_id)} · priority ${area.priority}</small>`;
    item.addEventListener("click", async () => {
      state.live2dSelectedAreaId = area.area_id;
      setLive2dAreaForm(area);
      renderLive2dAreaList();
      drawLive2dPreview();
      await loadLive2dTouchPool();
    });
    list.appendChild(item);
  }
}

function live2dPlacementFromInputs() {
  return {
    scale: Number($("#live2dPlacementScale")?.value || state.live2dPlacement.scale),
    offsetX: Number($("#live2dPlacementOffsetX")?.value || state.live2dPlacement.offsetX),
    offsetY: Number($("#live2dPlacementOffsetY")?.value || state.live2dPlacement.offsetY),
    bottomInset: Number($("#live2dPlacementBottomInset")?.value || state.live2dPlacement.bottomInset),
  };
}

function setLive2dPlacementInputs(placement) {
  const next = placement || state.live2dPlacement;
  state.live2dPlacement = { ...next };
  if ($("#live2dPlacementScale")) $("#live2dPlacementScale").value = String(next.scale);
  if ($("#live2dPlacementOffsetX")) $("#live2dPlacementOffsetX").value = String(next.offsetX);
  if ($("#live2dPlacementOffsetY")) $("#live2dPlacementOffsetY").value = String(next.offsetY);
  if ($("#live2dPlacementBottomInset")) $("#live2dPlacementBottomInset").value = String(next.bottomInset);
}

function live2dScreenRect(area) {
  const canvas = $("#live2dPreviewOverlay");
  if (!canvas || !window.Live2DHitTest) return null;
  const width = canvas.width;
  const height = canvas.height;
  const topLeft = Live2DHitTest.mapModelToScreenSpaceRaw(area.left, area.top, state.live2dPlacement);
  const bottomRight = Live2DHitTest.mapModelToScreenSpaceRaw(area.right, area.bottom, state.live2dPlacement);
  return {
    left: topLeft.x * width,
    top: topLeft.y * height,
    right: bottomRight.x * width,
    bottom: bottomRight.y * height,
  };
}

function drawLive2dCharacterLayer() {
  const config = state.live2dPreviewConfig;
  if (!config) return;
  if (config.renderer_mode === "live2d" && window.Live2DPreview) {
    Live2DPreview.setPlacement(state.live2dPlacement);
    return;
  }
  const bg = $("#live2dPreviewBg");
  if (!bg || !window.Live2DPreview) return;
  const ctx = bg.getContext("2d");
  if (state.live2dReferenceImage) {
    Live2DPreview.drawStaticStandee(ctx, state.live2dReferenceImage, state.live2dPlacement, bg.width, bg.height);
  } else {
    ctx.clearRect(0, 0, bg.width, bg.height);
    ctx.fillStyle = "#1a1a22";
    ctx.fillRect(0, 0, bg.width, bg.height);
    ctx.fillStyle = "#888";
    ctx.font = "14px sans-serif";
    ctx.fillText("未找到立绘参考图", 16, 32);
  }
}

const LIVE2D_HANDLE_RADIUS = 8;

function live2dClamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function live2dAreaHandles(rect) {
  const cx = (rect.left + rect.right) / 2;
  const cy = (rect.top + rect.bottom) / 2;
  return [
    { id: "nw", x: rect.left, y: rect.top },
    { id: "n", x: cx, y: rect.top },
    { id: "ne", x: rect.right, y: rect.top },
    { id: "e", x: rect.right, y: cy },
    { id: "se", x: rect.right, y: rect.bottom },
    { id: "s", x: cx, y: rect.bottom },
    { id: "sw", x: rect.left, y: rect.bottom },
    { id: "w", x: rect.left, y: cy },
  ];
}

function live2dHandleAtPoint(px, py, area) {
  const rect = live2dScreenRect(area);
  if (!rect) return null;
  const radius = LIVE2D_HANDLE_RADIUS + 4;
  if (area.area_id === state.live2dSelectedAreaId) {
    for (const handle of live2dAreaHandles(rect)) {
      if (Math.abs(px - handle.x) <= radius && Math.abs(py - handle.y) <= radius) {
        return handle.id;
      }
    }
  }
  if (px >= rect.left && px <= rect.right && py >= rect.top && py <= rect.bottom) {
    return "move";
  }
  return null;
}

function live2dApplyResize(area, handle, currentModel) {
  const minSize = 0.04;
  switch (handle) {
    case "se":
      area.right = live2dClampModelCoord(currentModel.x, area.left + minSize);
      area.bottom = live2dClampModelCoord(currentModel.y, area.top + minSize);
      break;
    case "sw":
      area.left = live2dClampModelCoord(currentModel.x, LIVE2D_MODEL_MIN, area.right - minSize);
      area.bottom = live2dClampModelCoord(currentModel.y, area.top + minSize);
      break;
    case "ne":
      area.right = live2dClampModelCoord(currentModel.x, area.left + minSize);
      area.top = live2dClampModelCoord(currentModel.y, LIVE2D_MODEL_MIN, area.bottom - minSize);
      break;
    case "nw":
      area.left = live2dClampModelCoord(currentModel.x, LIVE2D_MODEL_MIN, area.right - minSize);
      area.top = live2dClampModelCoord(currentModel.y, LIVE2D_MODEL_MIN, area.bottom - minSize);
      break;
    case "e":
      area.right = live2dClampModelCoord(currentModel.x, area.left + minSize);
      break;
    case "w":
      area.left = live2dClampModelCoord(currentModel.x, LIVE2D_MODEL_MIN, area.right - minSize);
      break;
    case "n":
      area.top = live2dClampModelCoord(currentModel.y, LIVE2D_MODEL_MIN, area.bottom - minSize);
      break;
    case "s":
      area.bottom = live2dClampModelCoord(currentModel.y, area.top + minSize);
      break;
    default:
      break;
  }
}

function live2dApplyFullBodyBounds() {
  const area = live2dSelectedArea();
  if (!area) return;
  area.left = LIVE2D_FULL_BODY_BOUNDS.left;
  area.top = LIVE2D_FULL_BODY_BOUNDS.top;
  area.right = LIVE2D_FULL_BODY_BOUNDS.right;
  area.bottom = LIVE2D_FULL_BODY_BOUNDS.bottom;
  live2dNormalizeAreaBounds(area);
  setLive2dAreaForm(area);
  drawLive2dPreview({ overlayOnly: true });
}

function live2dNudgeSelectedArea(delta) {
  const area = live2dSelectedArea();
  if (!area) return;
  const minSize = 0.04;
  area.left = live2dClampModelCoord(area.left - delta, LIVE2D_MODEL_MIN, area.right - minSize);
  area.top = live2dClampModelCoord(area.top - delta, LIVE2D_MODEL_MIN, area.bottom - minSize);
  area.right = live2dClampModelCoord(area.right + delta, area.left + minSize);
  area.bottom = live2dClampModelCoord(area.bottom + delta, area.top + minSize);
  setLive2dAreaForm(area);
  drawLive2dPreview();
}

function drawLive2dPreview(options = {}) {
  const overlayOnly = Boolean(options.overlayOnly);
  const characterLayer = Boolean(options.characterLayer) || !overlayOnly;
  if (characterLayer) {
    drawLive2dCharacterLayer();
  }
  const canvas = $("#live2dPreviewOverlay");
  if (!canvas || !window.Live2DHitTest) return;
  const ctx = canvas.getContext("2d");
  const width = canvas.width;
  const height = canvas.height;
  ctx.clearRect(0, 0, width, height);
  state.live2dHitAreas.forEach((area, index) => {
    const rect = live2dScreenRect(area);
    if (!rect) return;
    const x = rect.left;
    const y = rect.top;
    const w = rect.right - rect.left;
    const h = rect.bottom - rect.top;
    const selected = area.area_id === state.live2dSelectedAreaId;
    const color = LIVE2D_AREA_COLORS[index % LIVE2D_AREA_COLORS.length];
    ctx.fillStyle = selected ? `${color}66` : `${color}33`;
    ctx.strokeStyle = selected ? "#26313d" : color;
    ctx.lineWidth = selected ? 2 : 1;
    ctx.fillRect(x, y, w, h);
    ctx.strokeRect(x, y, w, h);
    ctx.fillStyle = selected ? "#26313d" : "#fff";
    ctx.font = "bold 13px sans-serif";
    ctx.fillText(area.label || area.area_id, x + 6, y + 18);
    if (selected) {
      for (const handle of live2dAreaHandles(rect)) {
        ctx.beginPath();
        ctx.arc(handle.x, handle.y, LIVE2D_HANDLE_RADIUS, 0, Math.PI * 2);
        ctx.fillStyle = "#ffffff";
        ctx.fill();
        ctx.strokeStyle = "#d95f76";
        ctx.lineWidth = 2;
        ctx.stroke();
      }
    }
  });
}

function live2dCanvasPoint(event) {
  const canvas = $("#live2dPreviewOverlay");
  const rect = canvas.getBoundingClientRect();
  const x = ((event.clientX - rect.left) / rect.width) * canvas.width;
  const y = ((event.clientY - rect.top) / rect.height) * canvas.height;
  return { x, y, width: canvas.width, height: canvas.height };
}

function live2dModelPoint(px, py, width, height) {
  const screenX = px / width;
  const screenY = py / height;
  return Live2DHitTest.mapScreenToModelSpace(screenX, screenY, state.live2dPlacement);
}

function live2dHitAtPoint(px, py, width, height) {
  const model = live2dModelPoint(px, py, width, height);
  const sorted = [...state.live2dHitAreas].sort((a, b) => b.priority - a.priority);
  for (const area of sorted) {
    if (model.x >= area.left && model.x <= area.right && model.y >= area.top && model.y <= area.bottom) {
      return area;
    }
  }
  return null;
}

function bindLive2dCanvas() {
  const canvas = $("#live2dPreviewOverlay");
  if (!canvas || canvas.dataset.bound === "1") return;
  canvas.dataset.bound = "1";
  canvas.addEventListener("pointerdown", (event) => {
    const point = live2dCanvasPoint(event);
    let area = live2dSelectedArea();
    let handle = area ? live2dHandleAtPoint(point.x, point.y, area) : null;
    if (!handle) {
      area = live2dHitAtPoint(point.x, point.y, point.width, point.height);
      if (!area) return;
      handle = live2dHandleAtPoint(point.x, point.y, area);
    }
    state.live2dSelectedAreaId = area.area_id;
    setLive2dAreaForm(area);
    renderLive2dAreaList();
    const mode = handle && handle !== "move" ? "resize" : "move";
    state.live2dDrag = {
      areaId: area.area_id,
      mode,
      resizeHandle: mode === "resize" ? handle : "",
      startModel: live2dModelPoint(point.x, point.y, point.width, point.height),
      startArea: { ...area },
    };
    canvas.setPointerCapture(event.pointerId);
    drawLive2dPreview({ overlayOnly: true });
  });
  canvas.addEventListener("pointermove", (event) => {
    if (!state.live2dDrag) return;
    const point = live2dCanvasPoint(event);
    const area = live2dSelectedArea();
    if (!area || area.area_id !== state.live2dDrag.areaId) return;
    const currentModel = live2dModelPoint(point.x, point.y, point.width, point.height);
    const start = state.live2dDrag.startArea;
    if (state.live2dDrag.mode === "resize") {
      live2dApplyResize(area, state.live2dDrag.resizeHandle, currentModel);
    } else {
      const dx = currentModel.x - state.live2dDrag.startModel.x;
      const dy = currentModel.y - state.live2dDrag.startModel.y;
      const widthNorm = start.right - start.left;
      const heightNorm = start.bottom - start.top;
      area.left = live2dClampModelCoord(start.left + dx, LIVE2D_MODEL_MIN, LIVE2D_MODEL_MAX - widthNorm);
      area.top = live2dClampModelCoord(start.top + dy, LIVE2D_MODEL_MIN, LIVE2D_MODEL_MAX - heightNorm);
      area.right = area.left + widthNorm;
      area.bottom = area.top + heightNorm;
    }
    setLive2dAreaForm(area);
    drawLive2dPreview({ overlayOnly: true });
  });
  const endDrag = () => {
    if (state.live2dDrag) {
      const area = live2dSelectedArea();
      if (area) {
        live2dNormalizeAreaBounds(area);
        setLive2dAreaForm(area);
        drawLive2dPreview({ overlayOnly: true });
      }
    }
    state.live2dDrag = null;
  };
  canvas.addEventListener("pointerup", endDrag);
  canvas.addEventListener("pointercancel", endDrag);
}

async function ensureLive2dPreviewEngine() {
  if (!window.Live2DPreview || state.live2dPreviewReady) return;
  const glCanvas = $("#live2dPreviewGl");
  if (!glCanvas) return;
  if (typeof window.PIXI === "undefined") {
    throw new Error("PIXI / Cubism Web 脚本未加载，无法预览 Live2D");
  }
  await Live2DPreview.init(glCanvas);
  state.live2dPreviewReady = true;
}

async function loadLive2dPreviewConfig() {
  const appearanceId = state.live2dAppearanceId || "neko";
  const payload = await api(`/api/admin/live2d/preview-config?appearance_id=${encodeURIComponent(appearanceId)}`);
  state.live2dPreviewConfig = payload;
  setLive2dPlacementInputs(payload.default_placement || state.live2dPlacement);
  if (payload.renderer_mode === "live2d") {
    await ensureLive2dPreviewEngine();
    Live2DPreview.setRendererMode("live2d");
    if (payload.model_url) {
      await Live2DPreview.loadModel(payload.model_url);
    }
    $("#live2dPreviewBg")?.style.setProperty("display", "none");
  } else {
    Live2DPreview?.setRendererMode?.("static_png");
    const bg = $("#live2dPreviewBg");
    if (bg) bg.style.display = "block";
  }
}

function loadLive2dAppearanceOptions() {
  const select = $("#live2dAppearanceId");
  if (!select) return;
  const previous = state.live2dAppearanceId || select.value || "neko";
  if ([...select.options].some((option) => option.value === previous)) {
    select.value = previous;
  }
  state.live2dAppearanceId = select.value;
}

function applyLive2dPlacementFromInputs() {
  const placement = live2dPlacementFromInputs();
  setLive2dPlacementInputs(placement);
  drawLive2dPreview({ characterLayer: true });
}

async function loadLive2dHitAreas() {
  const appearanceId = $("#live2dAppearanceId")?.value || state.live2dAppearanceId;
  state.live2dAppearanceId = appearanceId;
  const payload = await api(`/api/admin/live2d/hit-areas?appearance_id=${encodeURIComponent(appearanceId)}`);
  state.live2dHitAreas = (payload.items || []).map((item) => live2dNormalizeAreaBounds({ ...item }));
  if (!state.live2dSelectedAreaId && state.live2dHitAreas.length) {
    state.live2dSelectedAreaId = state.live2dHitAreas[0].area_id;
  }
  if (!state.live2dHitAreas.some((item) => item.area_id === state.live2dSelectedAreaId)) {
    state.live2dSelectedAreaId = state.live2dHitAreas[0]?.area_id || "";
  }
  $("#live2dStatus").textContent = `${state.live2dHitAreas.length} 个区域`;
  renderLive2dAreaList();
  setLive2dAreaForm(live2dSelectedArea());
  drawLive2dPreview();
}

async function loadLive2dReferenceImage() {
  const config = state.live2dPreviewConfig;
  if (!config?.reference_image_url) {
    state.live2dReferenceImage = null;
    return;
  }
  const image = new Image();
  image.src = `${config.reference_image_url}&t=${Date.now()}`;
  await new Promise((resolve, reject) => {
    image.onload = resolve;
    image.onerror = reject;
  });
  state.live2dReferenceImage = image;
}

async function saveLive2dArea(event) {
  event.preventDefault();
  const form = $("#live2dAreaForm");
  const areaId = form.elements.area_id.value.trim();
  const payload = {
    appearance_id: state.live2dAppearanceId,
    label: form.elements.label.value.trim(),
    left: live2dRoundCoord(form.elements.left.value),
    top: live2dRoundCoord(form.elements.top.value),
    right: live2dRoundCoord(form.elements.right.value),
    bottom: live2dRoundCoord(form.elements.bottom.value),
    priority: Number(form.elements.priority.value || 0),
    base_cooldown_ms: Number(form.elements.base_cooldown_ms.value || 1400),
    enabled: form.elements.enabled.checked,
    flirt_hint: form.elements.flirt_hint.checked,
  };
  const result = await api(`/api/admin/live2d/hit-areas/${encodeURIComponent(areaId)}?appearance_id=${encodeURIComponent(state.live2dAppearanceId)}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
  $("#live2dResult").textContent = pretty(result);
  await loadLive2dHitAreas();
}

async function createLive2dArea() {
  const areaId = window.prompt("新部位 area_id，例如 left_leg", "left_leg");
  if (!areaId) return;
  const label = window.prompt("显示名称", areaId) || areaId;
  const result = await api("/api/admin/live2d/hit-areas", {
    method: "POST",
    body: JSON.stringify({
      area_id: areaId.trim().toLowerCase(),
      appearance_id: state.live2dAppearanceId,
      label,
      left: 0.45,
      top: 0.45,
      right: 0.55,
      bottom: 0.55,
      priority: 20,
      enabled: true,
      base_cooldown_ms: 1400,
      tap_motions: [],
      reactions: [{ intensity: "soft", motion: "idle", expression: "calm", cooldown_ms: 1400 }],
    }),
  });
  state.live2dSelectedAreaId = result.item.area_id;
  $("#live2dResult").textContent = pretty(result);
  await loadLive2dHitAreas();
  await loadLive2dTouchPool();
}

async function deleteLive2dArea() {
  const area = live2dSelectedArea();
  if (!area) return;
  if (!window.confirm(`删除触摸区域 ${area.area_id}？`)) return;
  const result = await api(`/api/admin/live2d/hit-areas/${encodeURIComponent(area.area_id)}?appearance_id=${encodeURIComponent(state.live2dAppearanceId)}`, {
    method: "DELETE",
  });
  state.live2dSelectedAreaId = "";
  $("#live2dResult").textContent = pretty(result);
  await loadLive2dHitAreas();
}

function live2dTouchTierForRefresh() {
  const selected = $("#live2dTouchTier")?.value || "";
  if (selected === "all" || !selected) {
    return "";
  }
  return selected;
}

function renderLive2dTouchPoolLine(line) {
  const item = document.createElement("article");
  item.className = "live2d-pool-item";
  const jaLine = line.tts_text_ja ? `<small class="live2d-pool-ja">${escapeHtml(line.tts_text_ja)}</small>` : "";
  item.innerHTML = `
    <header>
      <strong>#${line.index + 1}${line.consumed ? " · 已消费" : ""}</strong>
      <span>${line.tts_duration_ms || 0} ms · cooldown ${line.cooldown_ms || 0} ms</span>
    </header>
    <p>${escapeHtml(line.text || "")}</p>
    ${jaLine}
    <div class="actions">
      ${line.tts_audio_url ? `<button type="button" data-play="${escapeHtml(line.tts_audio_url)}">试听</button>` : "<span>无 TTS</span>"}
    </div>
  `;
  const playButton = item.querySelector("[data-play]");
  if (playButton) {
    playButton.addEventListener("click", () => {
      const audio = new Audio(playButton.dataset.play);
      audio.play().catch(() => {});
    });
  }
  return item;
}

function updateLive2dTouchPoolMeta(payload) {
  const meta = $("#live2dTouchPoolMeta");
  if (!meta || !payload) return;
  const viewTier = $("#live2dTouchTier")?.value || "当前好感";
  meta.textContent = [
    `查看档：${viewTier}`,
    `用户好感档：${payload.relation_tier || "-"}`,
    `声线：${payload.voice_profile_id || "-"} (${payload.voice_language || "zh"})`,
    payload.pool_id ? `pool_id：${payload.pool_id}` : "",
  ].filter(Boolean).join(" · ");
}

async function loadLive2dTouchCoverage() {
  const meta = $("#live2dTouchCoverageMeta");
  const grid = $("#live2dTouchCoverageGrid");
  if (!meta || !grid) return;
  const query = new URLSearchParams({
    character_id: state.activeCharacterId || "atri",
    appearance_id: state.live2dAppearanceId || "neko",
  });
  try {
    const payload = await api(`/api/admin/live2d/touch-pools/coverage?${query.toString()}`);
    const preview = payload.bundle_preview || {};
    meta.textContent = [
      `App 当前好感档：${payload.relation_tier || "-"}`,
      `声线：${payload.voice_profile_id || "-"}`,
      `bundle 将下发 ${preview.line_count || 0} 条`,
      preview.missing_mp3 ? `缺 MP3：${preview.missing_mp3}` : "MP3 齐全",
      "改区域后 config_version 会变，App 回前台会 re-bootstrap 同步命中区域",
    ].join(" · ");
    grid.innerHTML = "";
    const hitAreas = payload.hit_areas || [];
    const tiers = ["low", "mid", "high"];
    const table = document.createElement("table");
    table.className = "live2d-coverage-table";
    const head = document.createElement("thead");
    const headRow = document.createElement("tr");
    headRow.innerHTML = "<th>部位</th>" + tiers.map((tier) => `<th>${tier}</th>`).join("");
    head.appendChild(headRow);
    table.appendChild(head);
    const body = document.createElement("tbody");
    for (const areaId of hitAreas) {
      const row = document.createElement("tr");
      const areaCell = document.createElement("td");
      areaCell.textContent = areaId;
      row.appendChild(areaCell);
      for (const tier of tiers) {
        const cell = document.createElement("td");
        const entry = (payload.matrix || []).find((item) => item.hit_area === areaId && item.tier === tier);
        if (!entry || !entry.ready) {
          cell.className = "coverage-miss";
          cell.textContent = entry ? `${entry.line_count}/${entry.mp3_count}` : "—";
          cell.title = "未就绪：需至少 2 条台词且 1 条 MP3";
        } else {
          cell.className = "coverage-ok";
          cell.textContent = `${entry.line_count}✓`;
          if (entry.shared) cell.title = "来自共享模板池";
        }
        row.appendChild(cell);
      }
      body.appendChild(row);
    }
    table.appendChild(body);
    grid.appendChild(table);
  } catch (error) {
    meta.textContent = `覆盖状态加载失败：${error.message}`;
    grid.innerHTML = "";
  }
}

async function regenerateAllLive2dTouchPools() {
  const body = {
    character_id: state.activeCharacterId || "atri",
    appearance_id: state.live2dAppearanceId || "neko",
    force: true,
    tiers: ["low", "mid", "high"],
  };
  const result = await api("/api/admin/live2d/touch-pools/refresh", {
    method: "POST",
    body: JSON.stringify(body),
  });
  $("#live2dResult").textContent = pretty(result);
  await loadLive2dTouchCoverage();
  await loadLive2dTouchPool();
}

async function loadLive2dTouchPool() {
  const area = live2dSelectedArea();
  const list = $("#live2dTouchPoolList");
  const meta = $("#live2dTouchPoolMeta");
  if (!area || !list) {
    if (list) list.textContent = "请选择左侧部位。";
    if (meta) meta.textContent = "请选择左侧部位。";
    return;
  }
  const tier = $("#live2dTouchTier")?.value || "";
  const query = new URLSearchParams({
    hit_area: area.area_id,
    character_id: state.activeCharacterId || "atri",
    appearance_id: state.live2dAppearanceId,
  });
  if (tier) query.set("tier", tier);
  const payload = await api(`/api/admin/live2d/touch-pools?${query.toString()}`);
  list.innerHTML = "";
  updateLive2dTouchPoolMeta(payload);

  if (payload.view_mode === "all") {
    let hasAny = false;
    for (const group of payload.tiers || []) {
      const section = document.createElement("section");
      section.className = "live2d-pool-tier-group";
      const title = document.createElement("h4");
      title.textContent = `${group.tier} · ${group.line_count || 0} 条${group.pool_id ? ` · ${group.pool_id}` : ""}`;
      section.appendChild(title);
      if (!group.lines?.length) {
        const empty = document.createElement("div");
        empty.className = "editor-placeholder";
        empty.textContent = "该档位尚未生成触摸语音池。";
        section.appendChild(empty);
      } else {
        hasAny = true;
        for (const line of group.lines) {
          section.appendChild(renderLive2dTouchPoolLine(line));
        }
      }
      list.appendChild(section);
    }
    if (!hasAny) {
      list.innerHTML = `<div class="editor-placeholder">三个档位都还没有触摸语音，可点击「重生当前档」或「重生全部档」。</div>`;
    }
    return;
  }

  if (!payload.lines?.length) {
    list.innerHTML = `<div class="editor-placeholder">当前档位还没有触摸语音，可点击「重生当前档」或「重生全部档」。</div>`;
    return;
  }
  for (const line of payload.lines) {
    list.appendChild(renderLive2dTouchPoolLine(line));
  }
}

async function regenerateLive2dTouchPool(options = {}) {
  const area = live2dSelectedArea();
  if (!area) return;
  const body = {
    hit_area: area.area_id,
    character_id: state.activeCharacterId || "atri",
    appearance_id: state.live2dAppearanceId,
    force: true,
  };
  if (options.allTiers) {
    body.tiers = ["low", "mid", "high"];
  } else {
    const tier = live2dTouchTierForRefresh();
    if (tier) body.tier = tier;
  }
  const result = await api("/api/admin/live2d/touch-pools/refresh", {
    method: "POST",
    body: JSON.stringify(body),
  });
  $("#live2dResult").textContent = pretty(result);
  await loadLive2dTouchPool();
}

function bindLive2dManager() {
  if ($("#live2dManager")?.dataset.bound === "1") return;
  $("#live2dManager").dataset.bound = "1";
  $("#live2dAreaForm")?.addEventListener("submit", saveLive2dArea);
  $("#live2dAddArea")?.addEventListener("click", () => createLive2dArea().catch((error) => {
    $("#live2dResult").textContent = pretty({ ok: false, message: error.message });
  }));
  $("#live2dRefreshAreas")?.addEventListener("click", () => loadLive2dManager().catch((error) => {
    $("#live2dResult").textContent = pretty({ ok: false, message: error.message });
  }));
  $("#live2dReloadPool")?.addEventListener("click", () => loadLive2dTouchPool().catch((error) => {
    $("#live2dResult").textContent = pretty({ ok: false, message: error.message });
  }));
  $("#live2dRegeneratePool")?.addEventListener("click", () => regenerateLive2dTouchPool().catch((error) => {
    $("#live2dResult").textContent = pretty({ ok: false, message: error.message });
  }));
  $("#live2dRegeneratePoolAllTiers")?.addEventListener("click", () => regenerateLive2dTouchPool({ allTiers: true }).catch((error) => {
    $("#live2dResult").textContent = pretty({ ok: false, message: error.message });
  }));
  $("#live2dRegeneratePoolAllAreas")?.addEventListener("click", () => regenerateAllLive2dTouchPools().catch((error) => {
    $("#live2dResult").textContent = pretty({ ok: false, message: error.message });
  }));
  $("#live2dTouchTier")?.addEventListener("change", () => loadLive2dTouchPool().catch((error) => {
    $("#live2dResult").textContent = pretty({ ok: false, message: error.message });
  }));
  $("#live2dAppearanceId")?.addEventListener("change", () => loadLive2dManager().catch((error) => {
    $("#live2dResult").textContent = pretty({ ok: false, message: error.message });
  }));
  $("#live2dAreaForm")?.querySelector('[data-action="delete-area"]')?.addEventListener("click", () => deleteLive2dArea().catch((error) => {
    $("#live2dResult").textContent = pretty({ ok: false, message: error.message });
  }));
  for (const id of ["live2dPlacementScale", "live2dPlacementOffsetX", "live2dPlacementOffsetY", "live2dPlacementBottomInset"]) {
    $("#" + id)?.addEventListener("input", () => applyLive2dPlacementFromInputs());
  }
  $("#live2dPlacementReset")?.addEventListener("click", () => {
    const defaults = state.live2dPreviewConfig?.default_placement || state.live2dPlacement;
    setLive2dPlacementInputs(defaults);
    drawLive2dPreview({ characterLayer: true });
  });
  $("#live2dPlacementShifted")?.addEventListener("click", () => {
    const base = state.live2dPreviewConfig?.default_placement || state.live2dPlacement;
    setLive2dPlacementInputs({
      scale: Number(base.scale || 1) * 1.2,
      offsetX: Number(base.offsetX || 0) + 48,
      offsetY: Number(base.offsetY || 0) - 18,
      bottomInset: Number(base.bottomInset || 0) + 8,
    });
    drawLive2dPreview({ characterLayer: true });
  });
  $("#live2dAreaFullBody")?.addEventListener("click", () => live2dApplyFullBodyBounds());
  $("#live2dAreaGrow")?.addEventListener("click", () => live2dNudgeSelectedArea(0.02));
  $("#live2dAreaShrink")?.addEventListener("click", () => live2dNudgeSelectedArea(-0.02));
  for (const name of ["left", "top", "right", "bottom"]) {
    $("#live2dAreaForm")?.elements[name]?.addEventListener("change", (event) => {
      const area = live2dSelectedArea();
      if (!area) return;
      area[name] = live2dRoundCoord(event.target.value);
      live2dNormalizeAreaBounds(area);
      setLive2dAreaForm(area);
      drawLive2dPreview({ overlayOnly: true });
    });
  }
  bindLive2dCanvas();
}

async function loadLive2dManager() {
  bindLive2dManager();
  loadLive2dAppearanceOptions();
  try {
    await loadLive2dPreviewConfig();
  } catch (error) {
    $("#live2dResult").textContent = pretty({ ok: false, message: error.message });
  }
  await loadLive2dHitAreas();
  try {
    await loadLive2dReferenceImage();
  } catch (_error) {
    state.live2dReferenceImage = null;
  }
  drawLive2dPreview();
  await loadLive2dTouchCoverage();
  await loadLive2dTouchPool();
}

async function boot() {
  try {
    state.presets = await api("/api/config/provider-presets");
    bindAdminNavigation();
    bindRuntimeLogs();
    await Promise.all([loadPairing(), loadStatus()]);
    await loadVoices();
    await loadCharacters();
    await loadTestData();
  } catch (error) {
    $("#debugResult").textContent = pretty({ ok: false, message: error.message });
  }
  $("#voiceForm").addEventListener("submit", saveVoice);
  $("#voiceNew").addEventListener("click", () => setVoiceForm(null));
  document.querySelectorAll("[data-debug]").forEach((button) => {
    button.addEventListener("click", () => runDebug(button.dataset.debug));
  });
}

boot();
