"""Model names and API protocols come from provider.jsonc; secrets come from env."""
import os
import re
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PositiveFloat,
    PositiveInt,
    ValidationError,
    model_validator,
)
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.profiles import ModelProfile
from pydantic_ai.providers.openai import OpenAIProvider
from integrations.model_http import model_client


_JSON_STRING = r'"(?:[^"\\]|\\.)*"'
_JSONC_COMMENTS = re.compile(rf'({_JSON_STRING})|//[^\r\n]*|/\*[\s\S]*?\*/')
_JSONC_TRAILING_COMMA = re.compile(rf'({_JSON_STRING}|[0-9e\]}}])(?:\s*,(?=\s*[\]}}]))?')


class ModelTier(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    provider: Literal["openai", "anthropic", "openrouter"]
    model_name: str | None = Field(default=None, min_length=1, pattern=r"^\S+$")
    protocol: Literal["openai", "anthropic", "decisions"] | None = None
    context_window: PositiveInt | None = None
    timeout: PositiveFloat = 30

    @model_validator(mode="after")
    def supported_protocol(self):
        if self.provider == "openrouter":
            if self.protocol == "decisions" and (not self.model_name or "/" not in self.model_name):
                raise ValueError("Decisions 必须指定完整 model_name")
            if self.protocol is None:
                raise ValueError("OpenRouter 必须指定 protocol")
        elif self.protocol is not None and self.protocol != self.provider:
            raise ValueError("直接服务的 protocol 必须与 provider 一致")
        return self

    @property
    def profile(self) -> ModelProfile:
        return ModelProfile(context_window=self.context_window) if self.context_window else ModelProfile()


class ProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    default_model: str
    models: dict[str, ModelConfig]
    model_tiers: dict[ModelTier, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def known_default(self):
        if not self.default_model.strip() or self.default_model not in self.models:
            raise ValueError("default_model 必须引用 models 中的模型")
        if any(not name.strip() for name in self.models):
            raise ValueError("模型名不能为空")
        if any(name not in self.models for name in self.model_tiers.values()):
            raise ValueError("model_tiers 必须引用 models 中的模型")
        if any(self.models[name].protocol == "decisions" for name in (self.default_model, *self.model_tiers.values())):
            raise ValueError("default_model 和 model_tiers 仅允许聊天模型")
        return self

    def model_for_tier(self, tier: ModelTier) -> str:
        if tier not in self.model_tiers:
            raise ValueError(f"provider.jsonc 未配置模型档位：{tier}")
        return self.model_tiers[tier]


def load_provider_config() -> ProviderConfig:
    path = Path(__file__).resolve().parents[1] / "provider.jsonc"
    try:
        source = path.read_text(encoding="utf-8")
        source = _JSONC_COMMENTS.sub(lambda match: match[1] or " ", source)
        source = _JSONC_TRAILING_COMMA.sub(r"\1", source)
        return ProviderConfig.model_validate_json(source)
    except (ValidationError, UnicodeError) as exc:
        raise ValueError(
            "provider.jsonc 无效：default_model 必须存在，provider 为 openai、anthropic 或 openrouter，"
            "OpenRouter 必须指定 openai、anthropic 或 decisions 协议，context_window 和 timeout 必须为正数，"
            "model_tiers 必须使用 low、medium、high 并引用已定义模型，且不允许未知字段。"
        ) from exc


def model_config(model_name: str | None = None) -> ModelConfig:
    config = load_provider_config()
    name = model_name if model_name is not None else config.default_model
    if name not in config.models:
        raise ValueError(f"provider.jsonc 未定义模型：{name}")
    return config.models[name]


def model_profile(model_name: str) -> ModelProfile:
    model = load_provider_config().models.get(model_name)
    return model.profile if model else ModelProfile()


def build_model(model_name: str | None = None, *, openai_model=OpenAIChatModel):
    config = load_provider_config()
    name = model_name if model_name is not None else config.default_model
    if name not in config.models:
        raise ValueError(f"provider.jsonc 未定义模型：{name}")
    model = config.models[name]
    if model.protocol == "decisions":
        raise ValueError("Decisions 模型只能用于分类请求")
    model_id = model.model_name or name
    if model.provider == "openrouter":
        upstream, _, slug = model_id.partition("/")
        if not upstream or not slug:
            raise ValueError(
                f"provider.jsonc 的 models[{name!r}].model_name 需要完整的 OpenRouter 模型 ID，"
                "例如 openai/gpt-6.1-sol。"
            )
    prefix = model.provider.upper()
    base_url = os.getenv(f"{prefix}_BASE_URL", "").strip().rstrip("/")
    key = os.getenv(f"{prefix}_API_KEY", "")
    if not base_url or not key.strip():
        raise ValueError(f"请配置 {prefix}_BASE_URL 和 {prefix}_API_KEY。")
    if model.provider == "openrouter":
        if model.protocol == "openai":
            from openai import AsyncOpenAI
            from pydantic_ai.models.openrouter import OpenRouterModel
            from pydantic_ai.providers.openrouter import OpenRouterProvider
            client = AsyncOpenAI(api_key=key, base_url=base_url, http_client=model_client(base_url))
            return OpenRouterModel(model_id, profile=model.profile, provider=OpenRouterProvider(openai_client=client))
        from anthropic import AsyncAnthropic
        from pydantic_ai.models.anthropic import AnthropicModel
        from pydantic_ai.providers.anthropic import AnthropicProvider
        client = AsyncAnthropic(auth_token=key, base_url=base_url.removesuffix("/v1"),
                                http_client=model_client(base_url))
        return AnthropicModel(model_id, profile=model.profile, provider=AnthropicProvider(anthropic_client=client))
    if model.provider == "openai":
        return openai_model(model_id, profile=model.profile, provider=OpenAIProvider(
            base_url=base_url, api_key=key, http_client=model_client(base_url),
        ))
    from pydantic_ai.models.anthropic import AnthropicModel
    from pydantic_ai.providers.anthropic import AnthropicProvider
    return AnthropicModel(model_id, profile=model.profile, provider=AnthropicProvider(
        api_key=key, base_url=base_url.removesuffix("/v1"), http_client=model_client(base_url),
    ))
