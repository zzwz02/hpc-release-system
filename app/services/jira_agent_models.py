"""Combine server-default models with explicitly configured provider catalogs.

Credentials and model instructions stay on server B. The website only exposes
provider names, model identifiers and the effort options needed by its picker.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from app.domain.jira_agent import AgentGroup
from app.integrations.codex_app_server import CodexAppServerClient, CodexAppServerError

_MAX_CATALOG_BYTES = 8 * 1024 * 1024


def _model_view(model: dict, provider: str) -> dict:
    """Whitelist UI metadata, including for custom catalogs with full prompts."""
    name = model.get("model")
    efforts = model.get("supportedReasoningEfforts")
    default = model.get("defaultReasoningEffort")
    if (not isinstance(name, str) or not name or not isinstance(default, str)
            or not isinstance(efforts, list) or not efforts):
        raise ValueError("模型目录缺少模型名称或 effort")
    options = []
    for entry in efforts:
        effort = entry.get("reasoningEffort") if isinstance(entry, dict) else None
        if not isinstance(effort, str) or not effort:
            raise ValueError("模型目录包含无效 effort")
        options.append({"reasoningEffort": effort, "description": str(entry.get("description") or "")})
    if default not in [entry["reasoningEffort"] for entry in options]:
        raise ValueError("模型默认 effort 不在支持列表中")
    return {
        "provider": provider, "model": name,
        "displayName": str(model.get("displayName") or name),
        "defaultReasoningEffort": default, "supportedReasoningEfforts": options,
    }


def _catalog_models(data: bytes, provider: str) -> list[dict]:
    if len(data) > _MAX_CATALOG_BYTES:
        raise ValueError("模型目录过大")
    raw = json.loads(data)
    if not isinstance(raw, dict) or not isinstance(raw.get("models"), list):
        raise ValueError("模型目录必须包含 models 数组")
    result = []
    for model in raw["models"]:
        if not isinstance(model, dict):
            raise ValueError("无效模型目录")
        if model.get("visibility") == "hide":
            continue
        result.append(_model_view({
            "model": model.get("slug"), "displayName": model.get("display_name"),
            "defaultReasoningEffort": model.get("default_reasoning_level"),
            "supportedReasoningEfforts": [
                {"reasoningEffort": e.get("effort"), "description": e.get("description")}
                for e in model.get("supported_reasoning_levels", []) if isinstance(e, dict)
            ],
        }, provider))
    if not result or len({model["model"] for model in result}) != len(result):
        raise ValueError("模型目录为空或包含重复模型")
    return result


@dataclass
class ModelCatalog:
    models: list[dict]
    providers: list[dict]
    default_provider: str
    errors: list[dict]

    def provider(self, name: str) -> dict:
        provider = next((p for p in self.providers if p["id"] == name), None)
        if provider is None:
            raise ValueError("服务商不可用，请刷新模型列表或检查该组 MODEL_CATALOGS 配置")
        return provider

    def select(self, provider: str, model: str = "", effort: str = "") -> tuple[str, str]:
        defaults = self.provider(provider)
        selected = next((m for m in self.models if m["provider"] == provider
                         and m["model"] == (model or defaults["default_model"])), None)
        if selected is None:
            raise ValueError("所选服务商下没有该模型，请刷新模型列表")
        effort = effort or (selected["defaultReasoningEffort"] if model else defaults["default_effort"])
        if effort not in [e["reasoningEffort"] for e in selected["supportedReasoningEfforts"]]:
            raise ValueError("该模型不支持所选 effort")
        return selected["model"], effort

    def view(self) -> dict:
        default = self.provider(self.default_provider)
        return {"models": self.models, "providers": self.providers,
                "default_provider": self.default_provider, "errors": self.errors,
                "default_model": default["default_model"], "default_effort": default["default_effort"]}


def thread_config(group: AgentGroup, provider: str) -> dict:
    path = dict(group.model_catalogs).get(provider)
    # Third-party catalogs describe tools and context limits, and must also be
    # supplied when starting/resuming their threads. Built-in web search isn't
    # assumed to be supported by a Responses-compatible third-party endpoint.
    return {"model_catalog_json": path, "web_search": "disabled"} if path else {}


async def read_catalog(client: CodexAppServerClient, group: AgentGroup) -> ModelCatalog:
    settings = await client.model_settings(group.workspace_root)
    default_provider = settings["provider"]
    default_models = await client.list_models()
    models = [_model_view(m, default_provider) for m in default_models if not m.get("hidden")]
    if not models:
        raise ValueError("app-server 未返回可用模型")
    default_model = group.model or settings["model"] or next(
        (m["model"] for m in default_models if m.get("isDefault")), models[0]["model"]
    )
    selected = next((m for m in models if m["model"] == default_model), None)
    if selected is None:
        raise ValueError("默认模型不在 app-server 模型列表中，请检查 MODEL 配置")
    providers = [{"id": default_provider,
                  "name": settings["providers"].get(default_provider, {}).get("name") or default_provider,
                  "default_model": default_model,
                  "default_effort": settings["effort"] or selected["defaultReasoningEffort"]}]
    errors = []
    for provider, path in group.model_catalogs:
        if provider == default_provider:
            continue
        if provider not in settings["providers"]:
            errors.append({"provider": provider, "message": "app-server 尚未配置该 provider"})
            continue
        try:
            extra = _catalog_models(await client.read_file(path), provider)
        except (CodexAppServerError, ValueError, TypeError, UnicodeError):
            errors.append({"provider": provider, "message": "无法读取有效的模型目录，请检查 B 上的目录文件"})
            continue
        models.extend(extra)
        providers.append({"id": provider, "name": settings["providers"][provider]["name"],
                          "default_model": extra[0]["model"], "default_effort": extra[0]["defaultReasoningEffort"]})
    return ModelCatalog(models, providers, default_provider, errors)
