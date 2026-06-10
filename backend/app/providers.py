from __future__ import annotations

import base64
import json
import mimetypes
import time
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import secret_store
from .diagnostics import write_diagnostic
from .media import media_from_base64, save_media
from .models import MediaAsset, ProviderConfig
from .schemas import ProviderConfigIn, ProviderConfigOut, ProviderTestResult
from .utils import clamp, dump_json, load_json, stable_hash, uid, utc_now


class ProviderError(RuntimeError):
    pass


HTTP_TRANSPORT: httpx.BaseTransport | None = None


def _client(timeout: float) -> httpx.Client:
    return httpx.Client(timeout=timeout, transport=HTTP_TRANSPORT)


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y"}


def _as_int(value: Any, default: int = 0) -> int:
    if value is None or value == "":
        return default
    return int(value)


def _as_float(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    return float(value)


def _as_tts_rate(value: Any, default: int = 0) -> int:
    if value is None or value == "":
        return default
    return clamp(round(float(value)), -50, 100)


def _split_sources(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def _json_or_empty(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _llm_presets(*, task: bool = False) -> list[dict[str, Any]]:
    suffix = "任务模型" if task else "对话模型"
    return [
            {
                "provider": "volc_ark",
                "label": f"火山方舟 / Doubao {suffix}",
                "base_url": "https://ark.cn-beijing.volces.com/api/v3",
                "model": "",
                "docs": "https://www.volcengine.com/docs/82379",
                "supports_models": True,
                "description": "负责后台任务 JSON/文本输出。" if task else "只负责 Galgame 对话 JSON 输出，不承担联网搜索。",
                "fields": [
                    _field("label", "显示名称", "core", default=f"火山方舟 / Doubao {suffix}"),
                    _field("base_url", "API Base URL", "core", default="https://ark.cn-beijing.volces.com/api/v3", required=True),
                    _field("model", "Chat Completions 模型", "core", required=True, placeholder="例如 doubao-seed-1-6-250615"),
                    _field("api_key", "Ark API Key", "secret", required=True),
                    _field("timeout", "请求超时（秒）", "metadata", type_="number", default=30),
                    _field("extra_body", "额外 Chat Completions JSON", "advanced", type_="json", default={}),
                ],
            },
            {
                "provider": "deepseek",
                "label": f"DeepSeek Chat Completions {suffix}",
                "base_url": "https://api.deepseek.com",
                "model": "deepseek-chat",
                "docs": "https://api-docs.deepseek.com/zh-cn/",
                "supports_models": True,
                "description": "DeepSeek 作为后台任务 LLM 使用，不承担联网搜索。" if task else "DeepSeek 作为 LLM 使用；除非真实工具结果含来源，否则不作为联网搜索。",
                "fields": [
                    _field("label", "显示名称", "core", default=f"DeepSeek Chat Completions {suffix}"),
                    _field("base_url", "API Base URL", "core", default="https://api.deepseek.com", required=True),
                    _field("model", "Chat Completions 模型", "core", default="deepseek-chat", required=True),
                    _field("api_key", "DeepSeek API Key", "secret", required=True),
                    _field("timeout", "请求超时（秒）", "metadata", type_="number", default=30),
                    _field("extra_body", "额外 Chat Completions JSON", "advanced", type_="json", default={}),
                ],
            },
            {
                "provider": "openai_compatible",
                "label": f"OpenAI-compatible {suffix}",
                "base_url": "",
                "model": "",
                "docs": "https://platform.openai.com/docs/api-reference/chat",
                "supports_models": True,
                "description": "自定义 OpenAI-compatible Chat Completions 入口，用于后台任务。" if task else "自定义 OpenAI-compatible Chat Completions 入口。",
                "fields": [
                    _field("label", "显示名称", "core", default=f"OpenAI-compatible {suffix}"),
                    _field("base_url", "API Base URL", "core", required=True, placeholder="https://example.com/v1"),
                    _field("model", "Chat Completions 模型", "core", required=True),
                    _field("api_key", "API Key", "secret", required=True),
                    _field("timeout", "请求超时（秒）", "metadata", type_="number", default=30),
                    _field("extra_body", "额外 Chat Completions JSON", "advanced", type_="json", default={}),
                ],
            },
    ]


def provider_presets() -> dict[str, Any]:
    return {
        "llm": _llm_presets(),
        "llm_task": _llm_presets(task=True),
        "tts": [
            {
                "provider": "volc_seed_tts",
                "label": "豆包语音合成 V3",
                "base_url": "https://openspeech.bytedance.com/api/v3/tts/unidirectional",
                "model": "",
                "docs": "https://www.volcengine.com/docs/6561/1598757?lang=zh",
                "supports_models": False,
                "description": "按 V3 HTTP Chunked 接口提交 user.uid 与 req_params.speaker/audio_params；默认由 AI 台词情绪自动设置 TTS 情绪与语音风格。",
                "fields": [
                    _field("label", "显示名称", "core", default="豆包语音合成 V3"),
                    _field("base_url", "TTS Endpoint", "core", default="https://openspeech.bytedance.com/api/v3/tts/unidirectional", required=True),
                    _field(
                        "credential_mode",
                        "控制台凭据类型",
                        "metadata",
                        type_="select",
                        default="single_key",
                        options=[
                            {"value": "single_key", "label": "新控制台：X-Api-Key"},
                            {"value": "app_credentials", "label": "旧控制台：App ID + Access Key"},
                        ],
                        help_text="新控制台通常只给一个 X-Api-Key；旧控制台使用 App ID + Access Key，App Key 可选。",
                    ),
                    _field(
                        "x_api_key",
                        "X-Api-Key",
                        "secret",
                        placeholder="新控制台给出的单 Key",
                        show_when={"credential_mode": "single_key"},
                    ),
                    _field(
                        "app_id",
                        "X-Api-App-Id",
                        "secret",
                        placeholder="旧控制台 App ID",
                        show_when={"credential_mode": "app_credentials"},
                    ),
                    _field(
                        "access_key",
                        "X-Api-Access-Key",
                        "secret",
                        placeholder="旧控制台 Access Key",
                        show_when={"credential_mode": "app_credentials"},
                    ),
                    _field(
                        "app_key",
                        "X-Api-App-Key（可选）",
                        "secret",
                        placeholder="留空时使用 App ID",
                        show_when={"credential_mode": "app_credentials"},
                    ),
                    _field("resource_id", "默认 X-Api-Resource-Id（兼容旧配置）", "metadata", placeholder="新音色库会优先使用每个音色自己的资源 ID"),
                    _field("speaker", "默认 speaker 音色 ID（兼容旧配置）", "metadata", placeholder="新音色库会优先使用每个音色自己的 speaker"),
                    _field("format", "音频格式", "metadata", type_="select", default="mp3", options=["mp3", "ogg_opus", "pcm", "wav"], help_text="生成音频的编码格式；默认 mp3 兼容性最好。"),
                    _field("sample_rate", "采样率", "metadata", type_="number", default=24000, help_text="每秒采样点数量；默认 24000，通常不用改。"),
                    _field(
                        "parameter_mode",
                        "TTS 参数模式",
                        "metadata",
                        type_="select",
                        default="ai_auto",
                        options=[
                            {"value": "ai_auto", "label": "AI 自动：按台词情绪"},
                            {"value": "manual", "label": "手动固定：使用下方参数"},
                        ],
                        help_text="AI 自动模式会读取每句台词的 emotion，并自动套用官方 V3 的情绪、语速和音量；手动模式固定使用下方参数。",
                    ),
                    _field("speech_rate", "语速", "metadata", type_="number", default=0, help_text="官方 V3 档位：-50 到 100；100 表示 2 倍速，-50 表示 0.5 倍速。", show_when={"parameter_mode": "manual"}),
                    _field("loudness_rate", "音量/响度", "metadata", type_="number", default=0, help_text="官方 V3 档位：-50 到 100；100 表示 2 倍音量，-50 表示 0.5 倍音量。", show_when={"parameter_mode": "manual"}),
                    _field("emotion_scale", "情绪强度", "metadata", type_="number", default=4, help_text="官方 V3 emotion_scale，范围 1 到 5。", show_when={"parameter_mode": "manual"}),
                    _field("emotion", "固定情绪", "metadata", placeholder="happy / sad / angry ...", help_text="仅手动模式使用。AI 自动模式会优先使用每句台词的 emotion。", show_when={"parameter_mode": "manual"}),
                    _field("context_texts", "上下文文本（每行一条，可选）", "advanced", type_="textarea"),
                    _field(
                        "emotion_map",
                        "AI 情绪到 TTS 情绪映射",
                        "advanced",
                        type_="json",
                        default={"happy": "happy", "shy": "happy", "thinking": "calm", "calm": "calm", "sad": "sad"},
                    ),
                    _field("uid", "user.uid", "metadata", default="aigalgame"),
                    _field("timeout", "请求超时（秒）", "metadata", type_="number", default=30),
                ],
            }
        ],
        "search": [
            {
                "provider": "volc_ark_web_search",
                "label": "火山方舟 Web Search",
                "base_url": "https://ark.cn-beijing.volces.com/api/v3",
                "model": "",
                "docs": "https://www.volcengine.com/docs/82379/1756990",
                "supports_models": True,
                "description": "走 Ark Responses API tools:[{\"type\":\"web_search\"}]，测试必须返回来源与发布时间。",
                "fields": [
                    _field("label", "显示名称", "core", default="火山方舟 Web Search"),
                    _field("base_url", "Responses API Base URL", "core", default="https://ark.cn-beijing.volces.com/api/v3", required=True),
                    _field("model", "Responses 模型", "core", required=True, placeholder="支持 Responses/Web Search 的方舟模型", help_text="保存 API Key 后可点击“拉取模型”，像 LLM 一样从 /models 选择。"),
                    _field("api_key", "Ark API Key", "secret", required=True),
                    _field("max_keyword", "max_keyword", "metadata", type_="number", default=3),
                    _field("limit", "limit", "metadata", type_="number", default=5),
                    _field("max_tool_calls", "max_tool_calls", "metadata", type_="number", default=1),
                    _field("sources", "sources（逗号分隔，可选）", "metadata", placeholder="news,web"),
                    _field("user_location", "user_location JSON（可选）", "advanced", type_="json", default={}),
                    _field("timeout", "请求超时（秒）", "metadata", type_="number", default=40),
                ],
            },
            {
                "provider": "deepseek_anthropic_search_experimental",
                "label": "DeepSeek 工具搜索（实验，默认不用于验收）",
                "base_url": "https://api.deepseek.com",
                "model": "",
                "docs": "https://api-docs.deepseek.com/zh-cn/guides/anthropic_api",
                "supports_models": False,
                "description": "保留占位：只有真实 web_search_tool_result 含来源时才允许通过。当前 Demo 默认请用火山方舟 Web Search。",
                "fields": [
                    _field("label", "显示名称", "core", default="DeepSeek 工具搜索（实验）"),
                    _field("base_url", "API Base URL", "core", default="https://api.deepseek.com", required=True),
                    _field("model", "Anthropic-compatible 模型", "core", required=True),
                    _field("api_key", "DeepSeek API Key", "secret", required=True),
                    _field("timeout", "请求超时（秒）", "metadata", type_="number", default=40),
                ],
            },
        ],
        "weather": [
            {
                "provider": "qweather",
                "label": "和风天气 QWeather",
                "base_url": "",
                "model": "",
                "docs": "https://dev.qweather.com/docs/",
                "supports_models": False,
                "description": "使用和风天气 API Host，支持 GeoAPI、实时天气、逐小时、每日预报、灾害预警和可选分钟降水。",
                "fields": [
                    _field("label", "显示名称", "core", default="和风天气 QWeather"),
                    _field("base_url", "API Host", "core", required=True, placeholder="https://abcxyz.qweatherapi.com"),
                    _field(
                        "auth_mode",
                        "认证方式",
                        "metadata",
                        type_="select",
                        default="jwt",
                        options=[
                            {"value": "jwt", "label": "JWT（推荐）"},
                            {"value": "api_key", "label": "API Key（兼容）"},
                        ],
                    ),
                    _field("key_id", "JWT kid / 凭据 ID", "secret", show_when={"auth_mode": "jwt"}),
                    _field("project_id", "JWT sub / 项目 ID", "secret", show_when={"auth_mode": "jwt"}),
                    _field("private_key", "Ed25519 私钥 PEM", "secret", type_="textarea", show_when={"auth_mode": "jwt"}),
                    _field("api_key", "QWeather API Key", "secret", show_when={"auth_mode": "api_key"}),
                    _field("test_location", "测试 LocationID 或经纬度", "metadata", default="101010100", placeholder="101010100 或 116.41,39.92"),
                    _field("daily_days", "每日预报天数", "metadata", type_="select", default="3d", options=["3d", "7d"]),
                    _field("hourly_hours", "逐小时预报时长", "metadata", type_="select", default="24h", options=["24h", "72h", "168h"]),
                    _field("include_minutely", "分钟降水", "metadata", type_="checkbox", default=True),
                    _field("geo_range", "GeoAPI 搜索范围", "metadata", default="cn", placeholder="cn；留空表示全球"),
                    _field("lang", "语言", "metadata", default="zh"),
                    _field("unit", "单位", "metadata", type_="select", default="m", options=["m", "i"]),
                    _field("cache_minutes", "天气缓存分钟", "metadata", type_="number", default=120),
                    _field("timeout", "请求超时（秒）", "metadata", type_="number", default=30),
                ],
            }
        ],
        "image": [
            {
                "provider": "doubao_seedream",
                "label": "豆包 Seedream 图片生成",
                "base_url": "https://ark.cn-beijing.volces.com/api/v3",
                "model": "doubao-seedream-5-0-260128",
                "docs": "https://www.volcengine.com/docs/82379",
                "supports_models": False,
                "description": "调用火山方舟 /images/generations，默认 response_format=url，下载后保存为 MediaAsset。",
                "fields": [
                    _field("label", "显示名称", "core", default="豆包 Seedream 图片生成"),
                    _field("base_url", "Images API Base URL", "core", default="https://ark.cn-beijing.volces.com/api/v3", required=True),
                    _field("model", "Seedream 模型", "core", default="doubao-seedream-5-0-260128", required=True),
                    _field("api_key", "Ark API Key", "secret", required=True),
                    _field("size", "尺寸", "metadata", default="1024x1024"),
                    _field("output_format", "输出格式", "metadata", type_="select", default="png", options=["png", "jpeg", "webp"]),
                    _field("response_format", "响应格式", "metadata", type_="select", default="url", options=["url", "b64_json"]),
                    _field("watermark", "水印", "metadata", type_="checkbox", default=False),
                    _field("image", "参考图 URL（可选）", "advanced"),
                    _field("timeout", "请求超时（秒）", "metadata", type_="number", default=120),
                ],
            },
            {
                "provider": "openai_gpt_image",
                "label": "OpenAI GPT Image",
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-image-2",
                "docs": "https://developers.openai.com/api/docs/guides/image-generation",
                "supports_models": False,
                "description": "调用 /images/generations，解析 data[0].b64_json；不强塞旧 response_format。",
                "fields": [
                    _field("label", "显示名称", "core", default="OpenAI GPT Image"),
                    _field("base_url", "Images API Base URL", "core", default="https://api.openai.com/v1", required=True),
                    _field("model", "图片模型", "core", default="gpt-image-2", required=True),
                    _field("api_key", "OpenAI API Key", "secret", required=True),
                    _field("size", "尺寸", "metadata", default="1024x1024"),
                    _field("quality", "质量", "metadata", type_="select", default="auto", options=["auto", "low", "medium", "high"]),
                    _field("output_format", "输出格式", "metadata", type_="select", default="png", options=["png", "jpeg", "webp"]),
                    _field("background", "背景", "metadata", type_="select", default="auto", options=["auto", "transparent", "opaque"]),
                    _field("moderation", "审核强度", "metadata", type_="select", default="auto", options=["auto", "low"]),
                    _field("timeout", "请求超时（秒）", "metadata", type_="number", default=120),
                ],
            },
            {
                "provider": "gemini_image",
                "label": "Gemini Image",
                "base_url": "https://generativelanguage.googleapis.com/v1",
                "model": "gemini-3.1-flash-image",
                "docs": "https://ai.google.dev/gemini-api/docs/image-generation?hl=zh-cn",
                "supports_models": False,
                "description": "调用 models/{model}:generateContent，解析 candidates.content.parts.inlineData。",
                "fields": [
                    _field("label", "显示名称", "core", default="Gemini Image"),
                    _field("base_url", "Gemini API Base URL", "core", default="https://generativelanguage.googleapis.com/v1", required=True),
                    _field("model", "Gemini 图片模型", "core", default="gemini-3.1-flash-image", required=True),
                    _field("api_key", "Gemini API Key", "secret", required=True),
                    _field("response_modalities", "responseModalities JSON", "advanced", type_="json", default=["IMAGE"]),
                    _field("timeout", "请求超时（秒）", "metadata", type_="number", default=120),
                ],
            },
        ],
    }


def _field(
    name: str,
    label: str,
    section: str,
    *,
    type_: str = "text",
    default: Any = "",
    required: bool = False,
    placeholder: str = "",
    options: list[Any] | None = None,
    help_text: str = "",
    show_when: dict[str, str] | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "label": label,
        "section": section,
        "type": type_,
        "default": default,
        "required": required,
        "placeholder": placeholder,
        "options": options or [],
        "help": help_text,
        "show_when": show_when or {},
    }


def _preset(kind: str, provider: str) -> dict[str, Any] | None:
    for item in provider_presets().get(kind, []):
        if item["provider"] == provider:
            return item
    return None


def _field_names(kind: str, provider: str, section: str) -> list[str]:
    preset = _preset(kind, provider) or {}
    return [field["name"] for field in preset.get("fields", []) if field.get("section") == section]


def _required_secret_fields(kind: str, provider: str) -> list[str]:
    preset = _preset(kind, provider) or {}
    return [
        field["name"]
        for field in preset.get("fields", [])
        if field.get("section") == "secret" and field.get("required")
    ]


def _secret_prefix(config: ProviderConfig) -> str:
    return config.secret_ref or config.provider_id


def _secret(config: ProviderConfig, field: str) -> str:
    prefix = _secret_prefix(config)
    return secret_store.get_field(prefix, field) or (secret_store.get(prefix) if field == "api_key" else "")


def _has_tts_credentials(config: ProviderConfig) -> bool:
    return bool(_secret(config, "x_api_key") or _secret(config, "api_key") or (_secret(config, "app_id") and _secret(config, "access_key")))


def _missing_qweather_credentials(config: ProviderConfig) -> list[str]:
    metadata = load_json(config.metadata_json, {})
    auth_mode = str(metadata.get("auth_mode") or "jwt").strip()
    if auth_mode == "api_key":
        return [] if _secret(config, "api_key") else ["api_key"]
    required = ["key_id", "project_id", "private_key"]
    return [field for field in required if not _secret(config, field)]


def _missing_secret_fields(config: ProviderConfig) -> list[str]:
    if _preset(config.kind, config.provider) is None:
        return ["unsupported_provider"]
    if config.kind == "tts" and config.provider == "volc_seed_tts":
        metadata = load_json(config.metadata_json, {})
        credential_mode = str(metadata.get("credential_mode") or "").strip()
        if credential_mode == "single_key":
            return [] if (_secret(config, "x_api_key") or _secret(config, "api_key")) else ["x_api_key"]
        if credential_mode == "app_credentials":
            missing = []
            if not _secret(config, "app_id"):
                missing.append("app_id")
            if not _secret(config, "access_key"):
                missing.append("access_key")
            return missing
        return [] if _has_tts_credentials(config) else ["x_api_key 或 app_id/access_key"]
    if config.kind == "weather" and config.provider == "qweather":
        return _missing_qweather_credentials(config)
    fields = _required_secret_fields(config.kind, config.provider)
    return [field for field in fields if not _secret(config, field)]


def _missing_required_fields(config: ProviderConfig) -> list[str]:
    preset = _preset(config.kind, config.provider)
    if preset is None:
        return ["unsupported_provider"]
    metadata = load_json(config.metadata_json, {})
    missing: list[str] = []
    for field in preset.get("fields", []):
        if not field.get("required") or field.get("section") == "secret":
            continue
        name = field["name"]
        section = field.get("section")
        if section == "core":
            value = getattr(config, name, "")
        else:
            value = metadata.get(name)
        if value in (None, ""):
            missing.append(name)
    return missing


def _has_secret_fields(config: ProviderConfig) -> dict[str, bool]:
    names = sorted(set(_field_names(config.kind, config.provider, "secret") + ["api_key"]))
    return {name: bool(_secret(config, name)) for name in names}


def provider_ready(config: ProviderConfig) -> bool:
    return (
        config.enabled
        and _preset(config.kind, config.provider) is not None
        and not _missing_secret_fields(config)
        and not _missing_required_fields(config)
    )


def provider_to_out(config: ProviderConfig) -> ProviderConfigOut:
    has_secret_fields = _has_secret_fields(config)
    missing_secrets = _missing_secret_fields(config)
    missing_required = _missing_required_fields(config)
    metadata = load_json(config.metadata_json, {})
    if config.kind == "tts" and config.provider == "volc_seed_tts" and not metadata.get("credential_mode"):
        has_app_credentials = _secret(config, "app_id") and _secret(config, "access_key")
        has_single_key = _secret(config, "x_api_key") or _secret(config, "api_key")
        metadata["credential_mode"] = "app_credentials" if has_app_credentials and not has_single_key else "single_key"
    return ProviderConfigOut(
        provider_id=config.provider_id,
        kind=config.kind,
        provider=config.provider,
        label=config.label,
        base_url=config.base_url,
        model=config.model,
        metadata=metadata,
        enabled=config.enabled,
        has_secret=any(has_secret_fields.values()),
        has_secret_fields=has_secret_fields,
        missing_secret_fields=missing_secrets,
        missing_required_fields=missing_required,
        ready=config.enabled and not missing_secrets and not missing_required,
    )


def _default_metadata(kind: str, provider: str) -> dict[str, Any]:
    preset = _preset(kind, provider) or {}
    defaults: dict[str, Any] = {}
    for field in preset.get("fields", []):
        if field.get("section") in {"metadata", "advanced"} and field.get("default") not in (None, ""):
            defaults[field["name"]] = field["default"]
    return defaults


def upsert_provider(session: Session, payload: ProviderConfigIn) -> ProviderConfig:
    if _preset(payload.kind, payload.provider) is None:
        raise ProviderError(f"Unsupported provider: {payload.kind}/{payload.provider}")
    provider_id = payload.provider_id or f"{payload.kind}_{payload.provider}_{uid('cfg')[-8:]}"
    existing = session.get(ProviderConfig, provider_id)
    old_secret_ref = existing.secret_ref if existing is not None else ""
    if existing is None:
        existing = ProviderConfig(provider_id=provider_id, kind=payload.kind, provider=payload.provider, secret_ref=provider_id)
        session.add(existing)
    preset = _preset(payload.kind, payload.provider) or {}
    metadata = _default_metadata(payload.kind, payload.provider)
    metadata.update(payload.metadata or {})
    existing.kind = payload.kind
    existing.provider = payload.provider
    existing.label = payload.label or preset.get("label") or payload.provider
    existing.base_url = (payload.base_url or str(preset.get("base_url") or "")).rstrip("/")
    existing.model = payload.model or str(preset.get("model") or "")
    existing.metadata_json = dump_json(metadata)
    existing.enabled = payload.enabled
    existing.secret_ref = provider_id
    existing.updated_at = utc_now()

    if old_secret_ref and old_secret_ref != provider_id:
        old_value = secret_store.get(old_secret_ref)
        if old_value and not secret_store.get_field(provider_id, "api_key"):
            secret_store.set_many(provider_id, {"api_key": old_value})

    secrets = dict(payload.secrets or {})
    if payload.api_key is not None:
        secrets["api_key"] = payload.api_key
    if secrets:
        secret_store.set_many(provider_id, secrets)
    if payload.enabled:
        for other in session.execute(
            select(ProviderConfig).where(
                ProviderConfig.kind == payload.kind,
                ProviderConfig.provider_id != provider_id,
                ProviderConfig.enabled == True,  # noqa: E712
            )
        ).scalars():
            other.enabled = False
            other.updated_at = utc_now()
    session.commit()
    return existing


def _enabled_provider_score(config: ProviderConfig) -> tuple[int, int, str, str]:
    preset_supported = _preset(config.kind, config.provider) is not None
    ready = preset_supported and not _missing_secret_fields(config) and not _missing_required_fields(config)
    return (int(ready), int(preset_supported), config.updated_at or config.created_at or "", config.provider_id)


def get_enabled_provider(session: Session, kind: str) -> ProviderConfig | None:
    configs = session.execute(
        select(ProviderConfig).where(ProviderConfig.kind == kind, ProviderConfig.enabled == True)  # noqa: E712
    ).scalars().all()
    if not configs:
        return None
    selected = max(configs, key=_enabled_provider_score)
    if len(configs) > 1:
        for config in configs:
            if config.provider_id != selected.provider_id:
                config.enabled = False
                config.updated_at = utc_now()
        session.commit()
    return selected


def get_task_llm_provider(session: Session) -> ProviderConfig | None:
    return get_enabled_provider(session, "llm_task") or get_enabled_provider(session, "llm")


class OpenAICompatibleClient:
    def __init__(self, config: ProviderConfig) -> None:
        self.config = config
        self.api_key = _secret(config, "api_key")
        self.metadata = load_json(config.metadata_json, {})
        if not self.api_key:
            raise ProviderError("LLM API key is not configured")
        if not config.base_url:
            raise ProviderError("LLM base_url is not configured")

    def _url(self, suffix: str) -> str:
        return f"{self.config.base_url.rstrip('/')}/{suffix.lstrip('/')}"

    def list_models(self) -> list[str]:
        with _client(12.0) as client:
            response = client.get(self._url("models"), headers={"Authorization": f"Bearer {self.api_key}"})
            response.raise_for_status()
        data = response.json()
        return [str(item.get("id")) for item in data.get("data", []) if item.get("id")]

    def chat_json(self, messages: list[dict[str, str]], *, max_tokens: int = 800, temperature: float = 0.7) -> dict[str, Any]:
        if not self.config.model:
            raise ProviderError("LLM model is not selected")
        body: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }
        extra_body = self.metadata.get("extra_body")
        if isinstance(extra_body, dict):
            body.update(extra_body)
        started = time.monotonic()
        with _client(float(self.metadata.get("timeout", 30.0))) as client:
            try:
                response = client.post(
                    self._url("chat/completions"),
                    headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                    json=body,
                )
                response.raise_for_status()
            except Exception as exc:
                write_diagnostic(
                    "llm_error",
                    provider_id=self.config.provider_id,
                    model=self.config.model,
                    elapsed_ms=int((time.monotonic() - started) * 1000),
                    error_type=type(exc).__name__,
                    message=str(exc),
                )
                raise
        response_payload = response.json()
        choice = response_payload["choices"][0]
        content = choice["message"]["content"]
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            write_diagnostic(
                "llm_error",
                provider_id=self.config.provider_id,
                model=self.config.model,
                elapsed_ms=int((time.monotonic() - started) * 1000),
                error_type="json_decode",
                message=str(exc),
                raw_preview=content[:180],
            )
            raise ProviderError(f"LLM did not return valid JSON: {content[:180]}") from exc
        write_diagnostic(
            "llm_ok",
            provider_id=self.config.provider_id,
            model=self.config.model,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            keys=list(parsed.keys()) if isinstance(parsed, dict) else [],
            finish_reason=choice.get("finish_reason", ""),
            raw_preview=str(content)[:120],
        )
        return parsed

    def chat_text(self, messages: list[dict[str, str]], *, max_tokens: int = 240, temperature: float = 0.2) -> str:
        if not self.config.model:
            raise ProviderError("LLM model is not selected")
        body: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        extra_body = self.metadata.get("extra_body")
        if isinstance(extra_body, dict):
            body.update({key: value for key, value in extra_body.items() if key != "response_format"})
        started = time.monotonic()
        with _client(float(self.metadata.get("timeout", 30.0))) as client:
            try:
                response = client.post(
                    self._url("chat/completions"),
                    headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                    json=body,
                )
                response.raise_for_status()
            except Exception as exc:
                write_diagnostic(
                    "llm_text_error",
                    provider_id=self.config.provider_id,
                    model=self.config.model,
                    elapsed_ms=int((time.monotonic() - started) * 1000),
                    error_type=type(exc).__name__,
                    message=str(exc),
                )
                raise
        response_payload = response.json()
        choice = response_payload["choices"][0]
        content = str(choice["message"]["content"] or "").strip()
        write_diagnostic(
            "llm_text_ok",
            provider_id=self.config.provider_id,
            model=self.config.model,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            chars=len(content),
            finish_reason=choice.get("finish_reason", ""),
            raw_preview=content[:120],
        )
        return content


def _find_base64_audio(value: Any) -> str:
    if isinstance(value, str):
        candidate = value.strip()
        if len(candidate) > 100 and all(ch not in candidate[:32] for ch in "{}[]:"):
            return candidate
    if isinstance(value, dict):
        for key in ("data", "audio", "audio_base64", "binary_data"):
            found = _find_base64_audio(value.get(key))
            if found:
                return found
        for item in value.values():
            found = _find_base64_audio(item)
            if found:
                return found
    if isinstance(value, list):
        for item in value:
            found = _find_base64_audio(item)
            if found:
                return found
    return ""


def _json_values_from_text(text: str) -> list[Any]:
    values: list[Any] = []
    decoder = json.JSONDecoder()
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("data:"):
            line = line[5:].strip()
        if line in {"[DONE]", "DONE"}:
            continue
        index = 0
        while index < len(line):
            while index < len(line) and line[index].isspace():
                index += 1
            if index >= len(line):
                break
            try:
                value, end = decoder.raw_decode(line, index)
            except json.JSONDecodeError:
                break
            values.append(value)
            index = end
    if not values:
        try:
            values.append(json.loads(text))
        except json.JSONDecodeError:
            pass
    return values


def _decode_tts_response(response: httpx.Response) -> bytes:
    content_type = response.headers.get("content-type", "")
    content = response.content
    looks_text = "json" in content_type or "event-stream" in content_type or content.lstrip().startswith((b"{", b"data:"))
    if not looks_text:
        return content
    text = content.decode("utf-8", errors="ignore")
    messages: list[str] = []
    audio_parts: list[bytes] = []
    for value in _json_values_from_text(text):
        if isinstance(value, dict):
            code = value.get("code") or value.get("err_code")
            message = value.get("message") or value.get("err_msg")
            if code not in (None, 0, "0", 200, "200") and message:
                messages.append(f"{code}: {message}")
        audio_b64 = _find_base64_audio(value)
        if audio_b64:
            try:
                audio_parts.append(base64.b64decode(audio_b64))
            except Exception as exc:  # noqa: BLE001
                raise ProviderError(f"Volc TTS returned invalid base64 audio: {exc}") from exc
    if audio_parts:
        return b"".join(audio_parts)
    if messages:
        raise ProviderError("Volc TTS error: " + "; ".join(messages))
    raise ProviderError("Volc TTS response did not contain audio data")


class VolcSeedTtsClient:
    AUTO_STYLE_PARAMS: dict[str, dict[str, Any]] = {
        "happy": {"speech_rate": 8, "loudness_rate": 5, "emotion_scale": 4},
        "shy": {"speech_rate": -4, "loudness_rate": -4, "emotion_scale": 3},
        "thinking": {"speech_rate": -6, "loudness_rate": -2, "emotion_scale": 2},
        "calm": {"speech_rate": 0, "loudness_rate": 0, "emotion_scale": 2},
        "sad": {"speech_rate": -10, "loudness_rate": -8, "emotion_scale": 3},
    }

    def __init__(self, config: ProviderConfig) -> None:
        self.config = config
        self.metadata = load_json(config.metadata_json, {})

    def _headers(self, resource_id: str) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "X-Api-Resource-Id": resource_id,
            "X-Api-Request-Id": uid("tts"),
        }
        x_api_key = _secret(self.config, "x_api_key") or _secret(self.config, "api_key")
        if x_api_key:
            headers["X-Api-Key"] = x_api_key
            return headers
        app_id = _secret(self.config, "app_id") or str(self.metadata.get("app_id") or "")
        access_key = _secret(self.config, "access_key")
        app_key = _secret(self.config, "app_key") or str(self.metadata.get("app_key") or app_id)
        if not app_id or not access_key:
            raise ProviderError("TTS credentials require either X-Api-Key or App ID + Access Key")
        headers.update(
            {
                "X-Api-App-Id": app_id,
                "X-Api-App-Key": app_key,
                "X-Api-Access-Key": access_key,
            }
        )
        return headers

    def _style_for_line(self, line_emotion: str) -> tuple[dict[str, Any], str]:
        audio_params = {
            "format": str(self.metadata.get("format") or "mp3").strip(),
            "sample_rate": _as_int(self.metadata.get("sample_rate"), 24000),
            "speech_rate": _as_tts_rate(self.metadata.get("speech_rate"), 0),
            "loudness_rate": _as_tts_rate(self.metadata.get("loudness_rate"), 0),
        }
        if str(self.metadata.get("parameter_mode") or "ai_auto") != "ai_auto":
            scale = self.metadata.get("emotion_scale")
            if scale not in (None, ""):
                audio_params["emotion_scale"] = clamp(round(float(scale)), 1, 5)
            return audio_params, str(self.metadata.get("emotion") or "").strip()

        normalized = str(line_emotion or "calm").strip().lower()
        style = self.AUTO_STYLE_PARAMS.get(normalized, self.AUTO_STYLE_PARAMS["calm"])
        for field in ("speech_rate", "loudness_rate"):
            audio_params[field] = style[field]
        audio_params["emotion_scale"] = style["emotion_scale"]
        emotion_map = _json_or_empty(self.metadata.get("emotion_map")) or {
            "happy": "happy",
            "shy": "happy",
            "thinking": "calm",
            "calm": "calm",
            "sad": "sad",
        }
        return audio_params, str(emotion_map.get(normalized) or emotion_map.get("calm") or "").strip()

    def synthesize(
        self,
        session: Session,
        text: str,
        *,
        voice_type: str = "",
        resource_id: str = "",
        line_emotion: str = "calm",
    ) -> MediaAsset:
        endpoint = self.config.base_url or "https://openspeech.bytedance.com/api/v3/tts/unidirectional"
        resource_id = str(resource_id or self.metadata.get("resource_id") or "").strip()
        speaker = str(voice_type or self.metadata.get("speaker") or self.config.model or "").strip()
        if not resource_id:
            raise ProviderError("TTS resource_id is required")
        if not speaker:
            raise ProviderError("TTS speaker is required")
        audio_params, emotion = self._style_for_line(line_emotion)
        audio_format = str(audio_params["format"])
        req_params: dict[str, Any] = {
            "text": text,
            "speaker": speaker,
            "audio_params": audio_params,
        }
        if emotion:
            audio_params["emotion"] = emotion
        context_texts = self.metadata.get("context_texts")
        if isinstance(context_texts, str):
            context = [line.strip() for line in context_texts.splitlines() if line.strip()]
        elif isinstance(context_texts, list):
            context = [str(line).strip() for line in context_texts if str(line).strip()]
        else:
            context = []
        if context:
            req_params["context_texts"] = context
        uid_value = str(self.metadata.get("uid") or "aigalgame")
        cache_key = stable_hash("tts", text, speaker, resource_id, audio_format, dump_json(audio_params), emotion, dump_json(context))
        existing = session.execute(select(MediaAsset).where(MediaAsset.local_cache_key == cache_key)).scalar_one_or_none()
        if existing is not None:
            return existing
        body = {"user": {"uid": uid_value}, "req_params": req_params}
        headers = self._headers(resource_id)
        started = time.monotonic()
        with _client(float(self.metadata.get("timeout", 30.0))) as client:
            try:
                response = client.post(endpoint, headers=headers, json=body)
                response.raise_for_status()
            except Exception as exc:
                write_diagnostic(
                    "tts_error",
                    provider_id=self.config.provider_id,
                    endpoint=endpoint,
                    resource_id=resource_id,
                    speaker=speaker,
                    elapsed_ms=int((time.monotonic() - started) * 1000),
                    request_id=headers.get("X-Api-Request-Id"),
                    error_type=type(exc).__name__,
                    message=str(exc),
                    audio_params=audio_params,
                )
                raise
        audio_bytes = _decode_tts_response(response)
        if len(audio_bytes) < 128:
            raise ProviderError("Volc TTS returned too few audio bytes")
        write_diagnostic(
            "tts_ok",
            provider_id=self.config.provider_id,
            endpoint=endpoint,
            resource_id=resource_id,
            speaker=speaker,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            request_id=headers.get("X-Api-Request-Id"),
            bytes=len(audio_bytes),
            audio_params=audio_params,
        )
        return save_media(session, asset_type="tts_audio", content=audio_bytes, extension=audio_format, cache_key=cache_key)


VolcTtsClient = VolcSeedTtsClient


@dataclass
class SearchResult:
    title: str
    url: str
    published_at: str = ""
    summary: str = ""
    site_name: str = ""

    def model_dump(self) -> dict[str, str]:
        return {
            "title": self.title,
            "url": self.url,
            "published_at": self.published_at,
            "summary": self.summary,
            "site_name": self.site_name,
        }


def _normalize_source(item: Any) -> SearchResult | None:
    if not isinstance(item, dict):
        return None
    for nested_key in ("url_citation", "source", "citation"):
        nested = item.get(nested_key)
        if isinstance(nested, dict):
            found = _normalize_source(nested)
            if found:
                return found
    url = str(item.get("url") or item.get("link") or item.get("source_url") or "").strip()
    title = str(item.get("title") or item.get("name") or item.get("site_title") or "").strip()
    if not url or not title:
        return None
    return SearchResult(
        title=title,
        url=url,
        published_at=str(item.get("published_at") or item.get("publish_time") or item.get("publish_date") or item.get("date") or "").strip(),
        summary=str(item.get("summary") or item.get("snippet") or item.get("description") or "").strip(),
        site_name=str(item.get("site_name") or item.get("site") or item.get("hostname") or "").strip(),
    )


def _collect_sources(value: Any) -> list[SearchResult]:
    results: list[SearchResult] = []
    if isinstance(value, dict):
        normalized = _normalize_source(value)
        if normalized:
            results.append(normalized)
        for key in ("annotations", "sources", "references", "results"):
            if isinstance(value.get(key), list):
                for item in value[key]:
                    results.extend(_collect_sources(item))
        if isinstance(value.get("action"), dict):
            results.extend(_collect_sources(value["action"]))
        if isinstance(value.get("content"), list):
            results.extend(_collect_sources(value["content"]))
        if isinstance(value.get("output"), list):
            results.extend(_collect_sources(value["output"]))
    elif isinstance(value, list):
        for item in value:
            results.extend(_collect_sources(item))
    deduped: dict[str, SearchResult] = {}
    for result in results:
        deduped.setdefault(result.url, result)
    return list(deduped.values())


def _response_text(value: Any) -> str:
    if isinstance(value, dict):
        if isinstance(value.get("output_text"), str):
            return value["output_text"]
        texts: list[str] = []
        for item in value.get("output") or []:
            if not isinstance(item, dict):
                continue
            for part in item.get("content") or []:
                if isinstance(part, dict):
                    text = part.get("text") or part.get("output_text")
                    if isinstance(text, str):
                        texts.append(text)
        return "\n".join(texts)
    return ""


class VolcArkWebSearchClient:
    def __init__(self, config: ProviderConfig) -> None:
        self.config = config
        self.metadata = load_json(config.metadata_json, {})
        self.api_key = _secret(config, "api_key")
        if not self.api_key:
            raise ProviderError("Ark Web Search API key is not configured")
        if not config.model:
            raise ProviderError("Ark Responses model is not selected")

    def _url(self) -> str:
        base = self.config.base_url or "https://ark.cn-beijing.volces.com/api/v3"
        return f"{base.rstrip('/')}/responses"

    def search(self, query: str, *, require_published_at: bool = False) -> dict[str, Any]:
        tool: dict[str, Any] = {"type": "web_search"}
        for field in ("max_keyword", "limit"):
            if self.metadata.get(field) not in (None, ""):
                tool[field] = _as_int(self.metadata.get(field), 0)
        sources = _split_sources(self.metadata.get("sources"))
        if sources:
            tool["sources"] = sources
        user_location = _json_or_empty(self.metadata.get("user_location"))
        if user_location:
            tool["user_location"] = user_location
        body: dict[str, Any] = {
            "model": self.config.model,
            "input": [{"role": "user", "content": [{"type": "input_text", "text": query}]}],
            "tools": [tool],
        }
        if self.metadata.get("max_tool_calls") not in (None, ""):
            body["max_tool_calls"] = _as_int(self.metadata.get("max_tool_calls"), 1)
        with _client(float(self.metadata.get("timeout", 40.0))) as client:
            response = client.post(
                self._url(),
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json=body,
            )
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                write_diagnostic(
                    "search_error",
                    provider_id=self.config.provider_id,
                    provider=self.config.provider,
                    model=self.config.model,
                    endpoint=self._url(),
                    status_code=response.status_code,
                    error_type=type(exc).__name__,
                    message=str(exc),
                    response_preview=response.text[:300],
                )
                if response.status_code == 404:
                    raise ProviderError(
                        "Ark Web Search failed: model/Endpoint does not support Responses or Web Search, "
                        "or the region/model ID is unavailable. Check the 火山方舟 Responses/Web Search model "
                        f"and base URL. endpoint={self._url()} model={self.config.model}"
                    ) from exc
                raise
        payload = response.json()
        results = _collect_sources(payload)
        if require_published_at:
            results = [item for item in results if item.published_at]
        if not results:
            requirement = "title/url/published_at" if require_published_at else "title/url"
            raise ProviderError(f"Ark Web Search did not return verifiable sources with {requirement}")
        return {
            "summary": _response_text(payload),
            "sources": [item.model_dump() for item in results],
            "tool_usage": (payload.get("usage") or {}).get("tool_usage_details") if isinstance(payload, dict) else None,
            "raw_id": payload.get("id") if isinstance(payload, dict) else "",
        }


class QWeatherClient:
    def __init__(self, config: ProviderConfig) -> None:
        self.config = config
        self.metadata = load_json(config.metadata_json, {})
        self.auth_mode = str(self.metadata.get("auth_mode") or "jwt").strip()
        self.api_key = _secret(config, "api_key")
        if self.auth_mode == "api_key" and not self.api_key:
            raise ProviderError("QWeather API Key is not configured")
        if self.auth_mode != "api_key":
            self.key_id = _secret(config, "key_id")
            self.project_id = _secret(config, "project_id")
            self.private_key = _secret(config, "private_key")
            if not (self.key_id and self.project_id and self.private_key):
                raise ProviderError("QWeather JWT key_id/project_id/private_key are not configured")
        if not config.base_url:
            raise ProviderError("QWeather API Host is not configured")

    def _base_url(self) -> str:
        base = self.config.base_url.strip().rstrip("/")
        if not base.startswith(("http://", "https://")):
            base = f"https://{base}"
        return base

    def _url(self, path: str) -> str:
        return f"{self._base_url()}/{path.lstrip('/')}"

    def _jwt_token(self) -> str:
        try:
            import jwt
        except Exception as exc:  # noqa: BLE001
            raise ProviderError("QWeather JWT requires PyJWT and cryptography. Run pip install -r backend/requirements.txt") from exc
        now = int(time.time())
        payload = {"iat": now - 30, "exp": now + 900, "sub": self.project_id}
        return jwt.encode(payload, self.private_key, algorithm="EdDSA", headers={"kid": self.key_id})

    def _headers(self) -> dict[str, str]:
        if self.auth_mode == "api_key":
            return {"X-QW-Api-Key": self.api_key}
        return {"Authorization": f"Bearer {self._jwt_token()}"}

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        clean_params = {key: value for key, value in params.items() if value not in (None, "")}
        with _client(float(self.metadata.get("timeout", 30.0))) as client:
            response = client.get(self._url(path), headers=self._headers(), params=clean_params)
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                write_diagnostic(
                    "qweather_http_error",
                    provider_id=self.config.provider_id,
                    endpoint=self._url(path),
                    status_code=response.status_code,
                    response_preview=response.text[:300],
                )
                raise ProviderError(f"QWeather request failed: HTTP {response.status_code}") from exc
        payload = response.json()
        code = str(payload.get("code") or "")
        if code and code != "200":
            write_diagnostic("qweather_api_error", provider_id=self.config.provider_id, endpoint=self._url(path), code=code)
            raise ProviderError(f"QWeather API returned code={code}")
        return payload

    def city_lookup(self, location: str) -> dict[str, Any]:
        params = {
            "location": location,
            "number": 1,
            "range": str(self.metadata.get("geo_range") or "").strip(),
            "lang": str(self.metadata.get("lang") or "zh").strip(),
        }
        payload = self._get("/geo/v2/city/lookup", params)
        rows = payload.get("location") or []
        if not rows:
            raise ProviderError("QWeather GeoAPI did not return a matching location")
        return rows[0]

    def weather_now(self, location: str) -> dict[str, Any]:
        return self._get(
            "/v7/weather/now",
            {
                "location": location,
                "lang": str(self.metadata.get("lang") or "zh").strip(),
                "unit": str(self.metadata.get("unit") or "m").strip(),
            },
        )

    def weather_bundle(self, location: str, coordinate: str = "") -> dict[str, Any]:
        lang = str(self.metadata.get("lang") or "zh").strip()
        unit = str(self.metadata.get("unit") or "m").strip()
        daily_days = str(self.metadata.get("daily_days") or "3d").strip()
        hourly_hours = str(self.metadata.get("hourly_hours") or "24h").strip()
        common = {"location": location, "lang": lang, "unit": unit}
        bundle: dict[str, Any] = {
            "now": self.weather_now(location),
            "daily": self._get(f"/v7/weather/{daily_days}", common),
            "hourly": self._get(f"/v7/weather/{hourly_hours}", common),
        }
        try:
            bundle["warning"] = self._get("/v7/warning/now", common)
        except Exception as exc:  # noqa: BLE001
            write_diagnostic("qweather_warning_skipped", provider_id=self.config.provider_id, message=str(exc))
            bundle["warning"] = {}
        if _as_bool(self.metadata.get("include_minutely"), True) and coordinate:
            try:
                bundle["minutely"] = self._get("/v7/minutely/5m", {"location": coordinate, "lang": lang})
            except Exception as exc:  # noqa: BLE001
                write_diagnostic("qweather_minutely_skipped", provider_id=self.config.provider_id, message=str(exc))
                bundle["minutely"] = {}
        else:
            bundle["minutely"] = {}
        return bundle


def _image_extension_from_response(response: httpx.Response, fallback: str) -> str:
    content_type = response.headers.get("content-type", "").split(";")[0].strip()
    if content_type:
        guessed = mimetypes.guess_extension(content_type) or ""
        if guessed:
            return guessed.lstrip(".").replace("jpeg", "jpg")
    return fallback.lstrip(".") or "png"


class ImageProvider:
    def __init__(self, config: ProviderConfig) -> None:
        self.config = config
        self.api_key = _secret(config, "api_key")
        self.metadata = load_json(config.metadata_json, {})
        if not self.api_key:
            raise ProviderError("Image provider API key is not configured")

    def generate(self, session: Session, prompt: str) -> MediaAsset:
        if self.config.provider == "doubao_seedream":
            return self._generate_doubao_seedream(session, prompt)
        if self.config.provider == "openai_gpt_image":
            return self._generate_openai_image(session, prompt)
        if self.config.provider == "gemini_image":
            return self._generate_gemini_image(session, prompt)
        raise ProviderError(f"Unsupported image provider: {self.config.provider}")

    def _save_downloaded_url(self, session: Session, client: httpx.Client, url: str, prompt: str, fallback_ext: str) -> MediaAsset:
        image_response = client.get(url)
        image_response.raise_for_status()
        extension = _image_extension_from_response(image_response, fallback_ext)
        return save_media(
            session,
            asset_type="experience_cg",
            content=image_response.content,
            extension=extension,
            cache_key=stable_hash("image_url", url),
            prompt=prompt,
            ai_generated=True,
        )

    def _generate_doubao_seedream(self, session: Session, prompt: str) -> MediaAsset:
        base = self.config.base_url or "https://ark.cn-beijing.volces.com/api/v3"
        body: dict[str, Any] = {
            "model": self.config.model or "doubao-seedream-5-0-260128",
            "prompt": prompt,
            "size": self.metadata.get("size") or "1024x1024",
            "response_format": self.metadata.get("response_format") or "url",
            "watermark": _as_bool(self.metadata.get("watermark"), False),
        }
        output_format = str(self.metadata.get("output_format") or "png")
        if output_format:
            body["output_format"] = output_format
        if self.metadata.get("image"):
            body["image"] = self.metadata["image"]
        with _client(float(self.metadata.get("timeout", 120.0))) as client:
            response = client.post(
                f"{base.rstrip('/')}/images/generations",
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json=body,
            )
            response.raise_for_status()
            data = response.json()
            item = (data.get("data") or [{}])[0]
            if item.get("b64_json"):
                return media_from_base64(session, asset_type="experience_cg", b64=item["b64_json"], extension=output_format, prompt=prompt)
            if item.get("url"):
                return self._save_downloaded_url(session, client, str(item["url"]), prompt, output_format)
        raise ProviderError("Doubao Seedream did not return image url or b64_json")

    def _generate_openai_image(self, session: Session, prompt: str) -> MediaAsset:
        base = self.config.base_url or "https://api.openai.com/v1"
        output_format = str(self.metadata.get("output_format") or "png")
        body: dict[str, Any] = {
            "model": self.config.model or "gpt-image-2",
            "prompt": prompt,
            "size": self.metadata.get("size") or "1024x1024",
        }
        for field in ("quality", "output_format", "background", "moderation"):
            value = self.metadata.get(field)
            if value not in (None, ""):
                body[field] = value
        with _client(float(self.metadata.get("timeout", 120.0))) as client:
            response = client.post(
                f"{base.rstrip('/')}/images/generations",
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json=body,
            )
            response.raise_for_status()
            data = response.json()
            item = (data.get("data") or [{}])[0]
            if item.get("b64_json"):
                return media_from_base64(session, asset_type="experience_cg", b64=item["b64_json"], extension=output_format, prompt=prompt)
            if item.get("url"):
                return self._save_downloaded_url(session, client, str(item["url"]), prompt, output_format)
        raise ProviderError("OpenAI image response did not contain b64_json or url")

    def _generate_gemini_image(self, session: Session, prompt: str) -> MediaAsset:
        base = self.config.base_url or "https://generativelanguage.googleapis.com/v1"
        model = self.config.model or "gemini-3.1-flash-image"
        response_modalities = self.metadata.get("response_modalities") or ["IMAGE"]
        body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseModalities": response_modalities},
        }
        with _client(float(self.metadata.get("timeout", 120.0))) as client:
            response = client.post(
                f"{base.rstrip('/')}/models/{model}:generateContent",
                headers={"x-goog-api-key": self.api_key, "Content-Type": "application/json"},
                json=body,
            )
            response.raise_for_status()
        payload = response.json()
        for candidate in payload.get("candidates") or []:
            content = candidate.get("content") or {}
            for part in content.get("parts") or []:
                inline_data = part.get("inlineData") or part.get("inline_data")
                if not isinstance(inline_data, dict):
                    continue
                b64 = inline_data.get("data")
                if not b64:
                    continue
                mime_type = str(inline_data.get("mimeType") or inline_data.get("mime_type") or "image/png")
                extension = (mimetypes.guess_extension(mime_type) or ".png").lstrip(".").replace("jpeg", "jpg")
                return media_from_base64(session, asset_type="experience_cg", b64=str(b64), extension=extension, prompt=prompt)
        raise ProviderError("Gemini image response did not contain inlineData.data")


def run_provider_test(session: Session, payload: ProviderConfigIn, test_text: str) -> ProviderTestResult:
    started = time.monotonic()
    config: ProviderConfig | None = None
    try:
        config = upsert_provider(session, payload)
        if config.kind in {"llm", "llm_task"}:
            result = OpenAICompatibleClient(config).chat_json(
                [
                    {"role": "system", "content": "只返回 JSON。"},
                    {"role": "user", "content": '返回 {"ok": true, "reply": "测试通过"}，不要输出多余文字。'},
                ],
                max_tokens=80,
            )
            ok = bool(result.get("ok"))
            message = "LLM JSON test passed" if ok else "LLM JSON test returned ok=false"
            details: dict[str, Any] = result
        elif config.kind == "tts":
            asset = VolcSeedTtsClient(config).synthesize(session, test_text)
            ok = True
            message = "TTS test generated playable audio"
            details = {"asset_id": asset.asset_id, "url": asset.url}
        elif config.kind == "search":
            if config.provider != "volc_ark_web_search":
                raise ProviderError("DeepSeek experimental search is not enabled for demo acceptance; use Volc Ark Web Search.")
            result = VolcArkWebSearchClient(config).search(
                "请联网搜索今天 AI 游戏或 AI 陪伴应用相关的一条新闻，返回带标题、链接、发布时间的来源。",
                require_published_at=True,
            )
            ok = True
            message = "Ark Web Search returned verifiable sources"
            details = result
        elif config.kind == "weather":
            if config.provider != "qweather":
                raise ProviderError("Unsupported weather provider")
            location = str(load_json(config.metadata_json, {}).get("test_location") or "101010100").strip()
            result = QWeatherClient(config).weather_now(location)
            ok = True
            message = "QWeather returned current weather"
            details = {"location": location, "now": result.get("now") or {}, "updateTime": result.get("updateTime")}
        elif config.kind == "image":
            asset = ImageProvider(config).generate(session, "adult anime galgame heroine in a sunny classroom, cherry blossoms, safe")
            ok = True
            message = "Image provider generated and saved a real image"
            details = {"asset_id": asset.asset_id, "url": asset.url}
        else:
            raise ProviderError(f"Unsupported provider kind: {config.kind}")
    except Exception as exc:  # noqa: BLE001
        kind = payload.kind
        provider = payload.provider
        ok = False
        message = str(exc)
        details = {"error_type": type(exc).__name__}
        if payload.kind == "search":
            base_url = (config.base_url if config is not None else payload.base_url).rstrip("/")
            details.update(
                {
                    "provider_id": config.provider_id if config is not None else payload.provider_id,
                    "model": config.model if config is not None else payload.model,
                    "endpoint": f"{base_url}/responses" if base_url else "",
                }
            )
    else:
        kind = config.kind
        provider = config.provider
    elapsed = int((time.monotonic() - started) * 1000)
    if config is not None:
        metadata = load_json(config.metadata_json, {})
        metadata.update(
            {
                "last_test_ok": ok,
                "last_test_message": message,
                "last_test_elapsed_ms": elapsed,
                "last_test_at": utc_now(),
            }
        )
        if config.kind == "search":
            metadata.update(
                {
                    "last_test_provider": config.provider,
                    "last_test_model": config.model,
                    "last_test_endpoint": f"{config.base_url.rstrip('/')}/responses",
                }
            )
        config.metadata_json = dump_json(metadata)
        config.updated_at = utc_now()
        session.commit()
    return ProviderTestResult(ok=ok, kind=kind, provider=provider, message=message, elapsed_ms=elapsed, details=details)
