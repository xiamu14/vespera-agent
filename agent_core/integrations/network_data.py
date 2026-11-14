"""Shared data-category rules; callers supply provenance, never model-provided labels."""
from integrations.network import NetworkDenied
from integrations.network_privacy import MAX_PAYLOAD, check_content


def payload_limit(destination: str) -> int:
    return 16 * MAX_PAYLOAD if destination == "model" else MAX_PAYLOAD

# Destination identity and DNS are independently bound by the corresponding transport.
ALLOWED_DATA = {
    "http": frozenset({"public", "project", "attachment"}),
    "search": frozenset({"public"}),
    "mcp": frozenset({"public", "project"}),
    "model": frozenset({"public", "project", "conversation", "attachment"}),
    "classifier": frozenset({"public"}),
    "git": frozenset({"public", "project"}),
    "telemetry": frozenset({"metrics"}),
}
FORBIDDEN_DATA = frozenset({"credentials", "internal", "personal", "customer", "unknown"})


def check_data(value: str | bytes, *, destination: str, category: str) -> None:
    if category in FORBIDDEN_DATA or category not in ALLOWED_DATA.get(destination, ()):
        raise NetworkDenied("PRIV-04：目的地不允许接收此数据类别")
    check_content(value, protect_paths=destination != "model", max_bytes=payload_limit(destination),
                  forbid_binary=destination == "model")
