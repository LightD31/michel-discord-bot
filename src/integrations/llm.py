"""OpenAI-compatible LLM APIs (OpenRouter, NanoGPT) — no Discord imports.

Both providers speak the OpenAI chat-completions protocol, so one
:class:`LlmApi` wrapper around ``AsyncOpenAI`` serves them; what differs is
where the credentials live in config, the extra headers, and the shape of the
``/models`` catalog (OpenRouter prices per token as strings, NanoGPT per
million tokens as numbers).

Settings are read from the reactive config store on every :func:`get_api`
call, and a client is rebuilt when they change, so a key or URL edited in the
dashboard applies without a restart. Nothing here hardcodes a provider URL:
each API's ``baseUrl`` comes from config, and an API without one is simply
unavailable.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from typing import Any

from openai import AsyncOpenAI

from src.core import logging as logutil
from src.core.config import config_store
from src.core.errors import IntegrationError
from src.core.http import fetch

logger = logutil.init_logger(__name__)

API_OPENROUTER = "openrouter"
API_NANOGPT = "nanogpt"
SUPPORTED_APIS: tuple[str, ...] = (API_OPENROUTER, API_NANOGPT)
API_LABELS: dict[str, str] = {API_OPENROUTER: "OpenRouter", API_NANOGPT: "NanoGPT"}

DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_APP_TITLE = "Michel Discord Bot"

# Catalog lookups are cheap but not free: memoize per API.
_CATALOG_TTL_SECONDS = 600
# A model whose price is still unknown after a refresh is not re-fetched for this long.
_PRICE_REFRESH_SECONDS = 3600


class LlmError(IntegrationError):
    """Raised when an LLM API is unconfigured or a call to it fails."""


# =============================================================================
# Pricing & catalog
# =============================================================================


@dataclass(frozen=True)
class ModelPricing:
    """USD cost per token, input and output."""

    input_cost_per_token: float
    output_cost_per_token: float

    def calculate_cost(self, input_tokens: int, output_tokens: int) -> float:
        return self.input_cost_per_token * input_tokens + self.output_cost_per_token * output_tokens


@dataclass(frozen=True)
class CatalogModel:
    """One entry of a provider's model catalog, normalised across providers."""

    id: str
    name: str
    api: str
    prompt_per_m: float | None = None
    completion_per_m: float | None = None
    context_length: int | None = None

    @property
    def free(self) -> bool:
        return self.prompt_per_m == 0 and self.completion_per_m == 0

    @property
    def pricing(self) -> ModelPricing | None:
        if self.prompt_per_m is None or self.completion_per_m is None:
            return None
        return ModelPricing(self.prompt_per_m / 1e6, self.completion_per_m / 1e6)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "api": self.api,
            "prompt_per_m": self.prompt_per_m,
            "completion_per_m": self.completion_per_m,
            "context_length": self.context_length,
            "free": self.free,
        }


def _to_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    # OpenRouter marks dynamically-priced router models with "-1".
    return number if number >= 0 else None


def _to_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_openrouter_catalog(data: Any) -> list[CatalogModel]:
    """Parse OpenRouter's ``/models`` payload (prices are USD per token, as strings)."""
    models: list[CatalogModel] = []
    for entry in (data or {}).get("data") or []:
        if not isinstance(entry, dict) or not entry.get("id"):
            continue
        pricing = entry.get("pricing") or {}
        prompt = _to_float(pricing.get("prompt"))
        completion = _to_float(pricing.get("completion"))
        models.append(
            CatalogModel(
                id=str(entry["id"]),
                name=str(entry.get("name") or entry["id"]),
                api=API_OPENROUTER,
                prompt_per_m=None if prompt is None else prompt * 1e6,
                completion_per_m=None if completion is None else completion * 1e6,
                context_length=_to_int(entry.get("context_length")),
            )
        )
    return models


_NANOGPT_UNIT_TO_PER_M = {
    "per_million_tokens": 1.0,
    "per_thousand_tokens": 1e3,
    "per_token": 1e6,
}


def parse_nanogpt_catalog(data: Any) -> list[CatalogModel]:
    """Parse NanoGPT's ``/models?detailed=true`` payload.

    Prices come as numbers with an explicit ``unit`` (``per_million_tokens``
    today) and ``currency``; anything that isn't USD in a known unit is kept in
    the catalog but without a price.
    """
    models: list[CatalogModel] = []
    for entry in (data or {}).get("data") or []:
        if not isinstance(entry, dict) or not entry.get("id"):
            continue
        pricing = entry.get("pricing") or {}
        factor = _NANOGPT_UNIT_TO_PER_M.get(str(pricing.get("unit") or "per_million_tokens"))
        currency = str(pricing.get("currency") or "USD").upper()
        prompt = completion = None
        if factor is not None and currency == "USD":
            raw_prompt = _to_float(pricing.get("prompt"))
            raw_completion = _to_float(pricing.get("completion"))
            prompt = None if raw_prompt is None else raw_prompt * factor
            completion = None if raw_completion is None else raw_completion * factor
        models.append(
            CatalogModel(
                id=str(entry["id"]),
                name=str(entry.get("name") or entry["id"]),
                api=API_NANOGPT,
                prompt_per_m=prompt,
                completion_per_m=completion,
                context_length=_to_int(entry.get("context_length")),
            )
        )
    return models


def model_id_candidates(model_id: str) -> list[str]:
    """Likely catalog ids for *model_id*: as-is, without ``:route``, without a date suffix."""
    candidates = [model_id]
    base = model_id.split(":", 1)[0]
    if base not in candidates:
        candidates.append(base)
    without_date = re.sub(r"-\d{8}$", "", base)
    if without_date and without_date not in candidates:
        candidates.append(without_date)
    return candidates


def resolve_pricing(model_id: str, prices: dict[str, ModelPricing]) -> ModelPricing | None:
    """Price for *model_id*: exact id first, then tolerant fallbacks.

    Responses often name a dated or routed variant (``foo-20260406``,
    ``foo:free``) of the id the catalog lists.
    """
    candidates = model_id_candidates(model_id)
    for candidate in candidates:
        if candidate in prices:
            return prices[candidate]
    normalized = candidates[-1]
    for known_id, pricing in prices.items():
        if normalized.startswith(f"{known_id}-"):
            return pricing
    return None


# =============================================================================
# Settings
# =============================================================================


@dataclass(frozen=True)
class ApiSettings:
    """Snapshot of what one API needs, read from the global config."""

    name: str
    api_key: str = ""
    base_url: str = ""
    headers: tuple[tuple[str, str], ...] = ()
    timeout: float = DEFAULT_TIMEOUT_SECONDS

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.base_url)

    @property
    def catalog_url(self) -> str:
        return f"{self.base_url}/models"

    @property
    def catalog_params(self) -> dict[str, str] | None:
        # NanoGPT's default listing is minimal; pricing needs ``detailed=true``.
        return {"detailed": "true"} if self.name == API_NANOGPT else None


def _section(config: dict[str, Any], name: str) -> dict[str, Any]:
    raw = config.get(name)
    return raw if isinstance(raw, dict) else {}


def request_timeout(config: dict[str, Any] | None = None) -> float:
    """``AI.requestTimeoutSeconds`` from the global config, clamped to a sane range."""
    if config is None:
        config = config_store.get().get("config", {})
    raw = _section(config, "AI").get("requestTimeoutSeconds")
    value = _to_float(raw)
    if not value:
        return DEFAULT_TIMEOUT_SECONDS
    return min(max(value, 5.0), 300.0)


def get_api_settings(name: str) -> ApiSettings:
    """Read *name*'s credentials and URLs from the reactive config store."""
    config = config_store.get().get("config", {})
    timeout = request_timeout(config)
    if name == API_OPENROUTER:
        section = _section(config, "OpenRouter")
        headers: list[tuple[str, str]] = []
        app_url = str(section.get("appUrl") or "").strip()
        if app_url:
            headers.append(("HTTP-Referer", app_url))
        title = str(_section(config, "AI").get("appTitle") or DEFAULT_APP_TITLE).strip()
        if title:
            headers.append(("X-Title", title))
        return ApiSettings(
            name=name,
            api_key=str(section.get("openrouterApiKey") or "").strip(),
            base_url=str(section.get("baseUrl") or "").strip().rstrip("/"),
            headers=tuple(headers),
            timeout=timeout,
        )
    if name == API_NANOGPT:
        section = _section(config, "NanoGPT")
        return ApiSettings(
            name=name,
            api_key=str(section.get("apiKey") or "").strip(),
            base_url=str(section.get("baseUrl") or "").strip().rstrip("/"),
            timeout=timeout,
        )
    return ApiSettings(name=name)


# =============================================================================
# Catalog fetching
# =============================================================================

_PARSERS = {API_OPENROUTER: parse_openrouter_catalog, API_NANOGPT: parse_nanogpt_catalog}
_catalog_cache: dict[ApiSettings, tuple[float, list[CatalogModel]]] = {}


async def fetch_catalog(name: str, *, max_age: float = _CATALOG_TTL_SECONDS) -> list[CatalogModel]:
    """Return *name*'s model catalog, memoized for *max_age* seconds.

    Raises :class:`LlmError` when the API isn't configured or the catalog
    can't be fetched. Safe on any event loop (the shared HTTP session is
    per-loop).
    """
    if name not in _PARSERS:
        raise LlmError(f"API inconnue : {name}")
    settings = get_api_settings(name)
    if not settings.configured:
        raise LlmError(f"API {name} non configurée")

    cached = _catalog_cache.get(settings)
    if cached and time.monotonic() - cached[0] < max_age:
        return cached[1]

    try:
        data = await fetch(
            settings.catalog_url,
            return_type="json",
            headers={"Authorization": f"Bearer {settings.api_key}", **dict(settings.headers)},
            params=settings.catalog_params,
            retries=2,
        )
    except Exception as e:
        raise LlmError(f"Catalogue {name} indisponible : {e}") from e

    models = _PARSERS[name](data)
    _catalog_cache[settings] = (time.monotonic(), models)
    return models


# =============================================================================
# Completion client
# =============================================================================


@dataclass
class Completion:
    """Raw result of one chat completion."""

    content: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    reported_cost: float | None = None


@dataclass
class LlmApi:
    """One configured OpenAI-compatible API."""

    settings: ApiSettings
    client: AsyncOpenAI = field(init=False)
    prices: dict[str, ModelPricing] = field(default_factory=dict, init=False)
    _prices_loaded_at: float = field(default=0.0, init=False)

    def __post_init__(self) -> None:
        self.client = AsyncOpenAI(
            api_key=self.settings.api_key,
            base_url=self.settings.base_url,
            timeout=self.settings.timeout,
            max_retries=1,
            default_headers=dict(self.settings.headers) or None,
        )

    @property
    def name(self) -> str:
        return self.settings.name

    async def complete(
        self, model_id: str, messages: list[dict[str, str]], max_tokens: int
    ) -> Completion:
        """One chat completion, bounded by the configured timeout.

        Raises whatever the SDK raises (or :class:`TimeoutError`); callers
        that fan out over several models catch per model.
        """
        extra_body: dict[str, Any] | None = None
        if self.name == API_OPENROUTER:
            # Ask OpenRouter to report the billed cost alongside token usage.
            extra_body = {"usage": {"include": True}}
        async with asyncio.timeout(self.settings.timeout):
            response = await self.client.chat.completions.create(
                model=model_id,
                max_tokens=max_tokens,
                messages=messages,  # type: ignore[arg-type]
                extra_body=extra_body,
            )
        choice = response.choices[0] if response.choices else None
        content = (choice.message.content if choice and choice.message else None) or ""
        usage = response.usage
        reported_cost = _to_float(getattr(usage, "cost", None)) if usage else None
        return Completion(
            content=content,
            model=response.model or model_id,
            input_tokens=(usage.prompt_tokens or 0) if usage else 0,
            output_tokens=(usage.completion_tokens or 0) if usage else 0,
            reported_cost=reported_cost,
        )

    async def load_prices(self) -> None:
        """(Re)load the price table from the catalog; failures only log."""
        self._prices_loaded_at = time.monotonic()
        try:
            catalog = await fetch_catalog(self.name)
        except LlmError as e:
            logger.warning("Could not load %s prices: %s", self.name, e)
            return
        self.prices = {m.id: m.pricing for m in catalog if m.pricing is not None}
        logger.info("Loaded %s pricing for %d models", self.name, len(self.prices))

    async def cost_for(self, completion: Completion) -> float | None:
        """USD cost of *completion*: as reported by the API, else from the price table."""
        if completion.reported_cost is not None:
            return completion.reported_cost
        pricing = resolve_pricing(completion.model, self.prices)
        if pricing is None and time.monotonic() - self._prices_loaded_at > _PRICE_REFRESH_SECONDS:
            await self.load_prices()
            pricing = resolve_pricing(completion.model, self.prices)
        if pricing is None:
            return None
        return pricing.calculate_cost(completion.input_tokens, completion.output_tokens)

    async def close(self) -> None:
        await self.client.close()


_apis: dict[str, LlmApi] = {}


async def get_api(name: str) -> LlmApi | None:
    """The client for *name*, rebuilt when its settings changed; ``None`` if unconfigured.

    A replaced client is closed so its connection pool doesn't leak.
    """
    settings = get_api_settings(name)
    current = _apis.get(name)
    if current is not None and current.settings == settings:
        return current
    if current is not None:
        _apis.pop(name, None)
        await _close_quietly(current)
    if not settings.configured:
        return None
    api = LlmApi(settings)
    _apis[name] = api
    if current is not None:
        logger.info("%s settings changed, client rebuilt", name)
    return api


async def _close_quietly(api: LlmApi) -> None:
    try:
        await api.close()
    except Exception as e:
        logger.debug("Error closing %s client: %s", api.name, e)


def configured_apis() -> list[str]:
    """Names of the APIs that currently have a key and a base URL."""
    return [name for name in SUPPORTED_APIS if get_api_settings(name).configured]


async def close_all() -> None:
    """Close every cached client (extension unload)."""
    apis = list(_apis.values())
    _apis.clear()
    for api in apis:
        await _close_quietly(api)
