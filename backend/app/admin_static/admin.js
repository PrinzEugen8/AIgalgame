const kinds = [
  { id: "llm", title: "LLM 对话模型" },
  { id: "tts", title: "TTS 语音合成" },
  { id: "search", title: "联网搜索" },
  { id: "image", title: "图片生成" },
];

const state = {
  presets: {},
  providers: [],
  cards: new Map(),
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

  const title = document.createElement("span");
  title.textContent = field.required ? `${field.label} *` : field.label;
  label.appendChild(title);

  let input;
  if (field.type === "select") {
    input = document.createElement("select");
    for (const optionValue of field.options || []) {
      const option = document.createElement("option");
      option.value = optionValue;
      option.textContent = optionValue;
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
  return label;
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
  } catch (error) {
    setCardResult(card, { ok: false, message: error.message });
  } finally {
    $(".models", card).disabled = false;
  }
}

function renderFields(card, kind, providerName) {
  const preset = presetFor(kind, providerName);
  const existing = state.providers.find((item) => item.kind === kind && item.provider === providerName) || currentProvider(kind);
  card.dataset.providerId = existing && existing.provider === providerName ? existing.provider_id : "";
  $(".provider-title", card).textContent = preset.label || providerName;
  $(".description", card).textContent = preset.description || "";
  $(".doc-link", card).href = preset.docs || "#";
  $(".doc-link", card).style.display = preset.docs ? "" : "none";
  $(".models", card).style.display = preset.supports_models ? "" : "none";

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

  const badge = $(".badge", card);
  if (existing && existing.provider === providerName) {
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

function renderCards() {
  const grid = $("#providerGrid");
  grid.innerHTML = "";
  for (const kind of kinds) {
    const card = document.createElement("article");
    card.className = "card";
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
    node.textContent = configured[kind.id] ? "已就绪" : "未完成";
    node.style.color = configured[kind.id] ? "#286241" : "#a65359";
  }
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

async function boot() {
  try {
    state.presets = await api("/api/config/provider-presets");
    await Promise.all([loadPairing(), loadStatus()]);
  } catch (error) {
    $("#debugResult").textContent = pretty({ ok: false, message: error.message });
  }
  document.querySelectorAll("[data-debug]").forEach((button) => {
    button.addEventListener("click", () => runDebug(button.dataset.debug));
  });
}

boot();
