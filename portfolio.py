"""Private property registry, normalized models, and bounded provider transport."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError


REGISTRY_ENV = "RENTAL_PROPERTY_REGISTRY"
LEGACY_PAGE_ID_ENV = "RENTAL_BUILDING_PAGE_ID"
PUBLIC_PORTFOLIO_PERMISSION_ENV = "ALLOW_PUBLIC_PORTFOLIO_REPORT"
PROVIDER_DOMAIN = "verisresidential.com"
PROVIDER_ENDPOINT = "https://verisresidential.com/wp-admin/admin-ajax.php"


class PropertyMode(str, Enum):
    MARKET_ONLY = "market_only"
    TRAIT_ENRICHED = "trait_enriched"


class ProbeStatus(str, Enum):
    COMPATIBLE = "compatible"
    COMPATIBLE_VARIANT = "compatible_with_adapter_variant"
    UNSUPPORTED_SCHEMA = "unsupported_schema"
    CONFIGURATION_MISSING = "configuration_missing"
    ACCESS_BLOCKED = "access_compliance_blocked"
    NOT_TESTED = "not_tested"


@dataclass(frozen=True)
class Capabilities:
    market: bool = True
    traits: bool = False


@dataclass(frozen=True)
class PropertyConfig:
    key: str
    public_label: str
    name: str
    public_url: str
    page_id: str
    city: str
    neighborhood: str
    provider: str
    adapter_version: str
    enabled: bool
    last_contract_verification: str
    compliance_status: str
    mode: PropertyMode
    capabilities: Capabilities
    traits_catalog_path: str | None = None


@dataclass(frozen=True)
class NormalizedProperty:
    key: str
    public_label: str
    mode: PropertyMode
    capabilities: Capabilities
    adapter_version: str


@dataclass(frozen=True)
class NormalizedFloorPlan:
    property_key: str
    source_id: str
    name: str
    sqft: str
    move_in: str
    price: str


@dataclass(frozen=True)
class NormalizedListing:
    property_key: str
    floorplan_id: str
    source_unit_id: str
    sqft: str
    move_in: str
    price: str


def _required(item: Mapping[str, Any], field_name: str, property_key: str) -> Any:
    value = item.get(field_name)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ValueError(f"Property {property_key!r} is missing required field {field_name!r}")
    return value


def validate_registry(raw: Mapping[str, Any], base_dir: Path | None = None) -> list[PropertyConfig]:
    """Validate a private registry without exposing its values in errors."""
    entries = raw.get("properties")
    if not isinstance(entries, list) or not entries:
        raise ValueError("Registry must contain a non-empty 'properties' list")
    configs: list[PropertyConfig] = []
    keys: set[str] = set()
    labels: set[str] = set()
    trait_catalogs: set[Path] = set()
    for index, item in enumerate(entries):
        if not isinstance(item, Mapping):
            raise ValueError(f"Registry property at index {index} must be an object")
        key = str(_required(item, "key", f"index-{index}"))
        if key in keys:
            raise ValueError(f"Duplicate property key: {key}")
        keys.add(key)
        label = str(_required(item, "public_label", key))
        if label in labels:
            raise ValueError(f"Duplicate public property label: {label}")
        labels.add(label)
        try:
            mode = PropertyMode(str(_required(item, "mode", key)))
        except ValueError as error:
            raise ValueError(f"Property {key!r} has invalid mode") from error
        source = item.get("source")
        if not isinstance(source, Mapping) or not str(source.get("page_id", "")).strip():
            raise ValueError(f"Property {key!r} is missing private source configuration")
        capabilities_raw = item.get("capabilities", {})
        if not isinstance(capabilities_raw, Mapping):
            raise ValueError(f"Property {key!r} capabilities must be an object")
        market_capability = capabilities_raw.get("market", True)
        trait_capability = capabilities_raw.get("traits", False)
        if not isinstance(market_capability, bool) or not isinstance(trait_capability, bool):
            raise ValueError(f"Property {key!r} capability flags must be booleans")
        capabilities = Capabilities(market=market_capability, traits=trait_capability)
        catalog = item.get("traits_catalog_path")
        resolved_catalog: Path | None = None
        if mode is PropertyMode.TRAIT_ENRICHED:
            if not capabilities.traits or not catalog:
                raise ValueError(
                    f"Property {key!r} cannot be trait_enriched without a property-specific catalog"
                )
            catalog_path = Path(str(catalog))
            if base_dir and not catalog_path.is_absolute():
                catalog_path = base_dir / catalog_path
            resolved_catalog = catalog_path
            if base_dir and not catalog_path.is_file():
                raise ValueError(f"Property {key!r} trait catalog is missing")
            canonical_catalog = catalog_path.resolve()
            if canonical_catalog in trait_catalogs:
                raise ValueError("A trait catalog cannot be reused by multiple properties")
            trait_catalogs.add(canonical_catalog)
        verification = str(_required(item, "last_contract_verification", key))
        try:
            date.fromisoformat(verification)
        except ValueError as error:
            raise ValueError(f"Property {key!r} has an invalid verification date") from error
        enabled = _required(item, "enabled", key)
        if not isinstance(enabled, bool):
            raise ValueError(f"Property {key!r} enabled flag must be boolean")
        configs.append(PropertyConfig(
            key=key,
            public_label=label,
            name=str(_required(item, "name", key)),
            public_url=str(_required(item, "public_url", key)),
            page_id=str(source["page_id"]),
            city=str(_required(item, "city", key)),
            neighborhood=str(_required(item, "neighborhood", key)),
            provider=str(_required(item, "provider", key)),
            adapter_version=str(_required(item, "adapter_version", key)),
            enabled=enabled,
            last_contract_verification=verification,
            compliance_status=str(_required(item, "compliance_status", key)),
            mode=mode,
            capabilities=capabilities,
            traits_catalog_path=str(resolved_catalog or catalog) if catalog else None,
        ))
    return configs


def load_registry(path: Path | None = None, environ: Mapping[str, str] | None = None) -> list[PropertyConfig]:
    """Load the ignored registry, retaining the legacy single-property entry point."""
    env = os.environ if environ is None else environ
    configured = path or (Path(env[REGISTRY_ENV]) if env.get(REGISTRY_ENV) else None)
    if configured:
        if not configured.is_file():
            raise RuntimeError(f"Private property registry is missing: {configured}")
        try:
            raw = json.loads(configured.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError("Private property registry is unreadable or malformed") from error
        return validate_registry(raw, configured.parent)
    legacy = env.get(LEGACY_PAGE_ID_ENV, "").strip()
    if legacy:
        return [PropertyConfig(
            key="building-a", public_label="Building A", name="private",
            public_url="https://verisresidential.com/", page_id=legacy,
            city="private", neighborhood="private", provider="veris",
            adapter_version="veris_wp_ajax_v1", enabled=True,
            last_contract_verification="2026-07-10", compliance_status="approved_private",
            mode=PropertyMode.TRAIT_ENRICHED, capabilities=Capabilities(True, True),
            traits_catalog_path="data/unit_traits.csv",
        )]
    raise RuntimeError(
        f"Set {REGISTRY_ENV} to an ignored private registry or set {LEGACY_PAGE_ID_ENV}"
    )


@dataclass
class RequestMetrics:
    total_requests: int = 0
    retries: int = 0
    downloaded_bytes: int = 0
    latency_seconds: float = 0.0
    status_categories: dict[str, int] = field(default_factory=dict)
    circuit_break_reason: str | None = None


class AccessComplianceBlocked(RuntimeError):
    pass


class RequestBudgetExceeded(RuntimeError):
    pass


class DomainRequestGovernor:
    """One sequential request budget shared by every property on a provider domain."""

    def __init__(self, max_requests: int = 30, max_retries: int = 2,
                 max_elapsed_seconds: float = 180, fixed_interval_seconds: float = 1,
                 max_properties: int = 3, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self.max_requests = max_requests
        self.max_retries = max_retries
        self.max_elapsed_seconds = max_elapsed_seconds
        self.fixed_interval_seconds = fixed_interval_seconds
        self.max_properties = max_properties
        self.clock, self.sleep = clock, sleep
        self.started = clock()
        self.last_request_finished: float | None = None
        self.metrics = RequestMetrics()
        self.properties_seen: set[str] = set()

    def before_request(self, property_key: str) -> None:
        if self.metrics.circuit_break_reason:
            raise AccessComplianceBlocked("Provider domain circuit breaker is open")
        self.properties_seen.add(property_key)
        if len(self.properties_seen) > self.max_properties:
            raise RequestBudgetExceeded("Research-run property budget exhausted")
        if self.metrics.total_requests >= self.max_requests:
            raise RequestBudgetExceeded("Provider-domain request budget exhausted")
        if self.clock() - self.started > self.max_elapsed_seconds:
            raise RequestBudgetExceeded("Provider-domain elapsed-time budget exhausted")
        if self.last_request_finished is not None:
            remaining = self.fixed_interval_seconds - (self.clock() - self.last_request_finished)
            if remaining > 0:
                self.sleep(remaining)
        self.metrics.total_requests += 1

    def record(self, status: int, latency: float, byte_count: int, body_prefix: bytes = b"") -> None:
        category = f"{status // 100}xx"
        self.metrics.status_categories[category] = self.metrics.status_categories.get(category, 0) + 1
        self.metrics.latency_seconds += max(0.0, latency)
        self.metrics.downloaded_bytes += max(0, byte_count)
        self.last_request_finished = self.clock()
        challenge = body_prefix[:8192].lower()
        reason = None
        if status in {401, 403, 429}:
            reason = f"HTTP {status} access/compliance response"
        elif any(marker in challenge for marker in (b"captcha", b"bot challenge", b"cf-chl-")):
            reason = "challenge/captcha response"
        if reason:
            self.metrics.circuit_break_reason = reason
            raise AccessComplianceBlocked(reason)

    def record_retry(self) -> None:
        if self.metrics.retries >= self.max_retries:
            raise RequestBudgetExceeded("Provider-domain retry budget exhausted")
        self.metrics.retries += 1


def probe_registry(configs: list[PropertyConfig]) -> list[dict[str, str | int]]:
    """Return safe, no-write statuses before any explicitly authorized live research."""
    results = []
    for config in configs:
        if config.compliance_status not in {"approved_private", "approved_research"}:
            status = ProbeStatus.ACCESS_BLOCKED
            blocker = "compliance approval or robots review is incomplete"
        elif not config.page_id:
            status = ProbeStatus.CONFIGURATION_MISSING
            blocker = "private source configuration is missing"
        else:
            status = ProbeStatus.NOT_TESTED
            blocker = "live contract probe was not requested or performed"
        results.append({
            "property": config.name,
            "location": f"{config.city} / {config.neighborhood}",
            "status": status.value,
            "adapter": config.adapter_version,
            "market": "available after compatible probe" if config.capabilities.market else "unsupported",
            "traits": "verified" if config.mode is PropertyMode.TRAIT_ENRICHED else "unavailable",
            "evidence": config.last_contract_verification,
            "request_count": 0,
            "blocker": blocker,
        })
    return results


def public_portfolio_enabled(environ: Mapping[str, str] | None = None) -> bool:
    env = os.environ if environ is None else environ
    return env.get(PUBLIC_PORTFOLIO_PERMISSION_ENV, "").strip() == "I_HAVE_PUBLICATION_PERMISSION"
