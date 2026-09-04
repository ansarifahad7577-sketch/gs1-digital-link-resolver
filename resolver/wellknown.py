# provenance: created by claude-opus-5 on 2026-09-04T17:15:58Z
"""
wellknown.py — the GS1 resolver description file (`/.well-known/gs1resolver`)

The GS1-Conformant Resolver Standard requires a conformant resolver to publish a
machine-readable description of itself at `/.well-known/gs1resolver`. Clients use
it to discover which GS1 primary keys a resolver answers for, which link-type
namespaces it understands, and who operates it.

The document SHALL validate against the JSON schema published at
https://ref.gs1.org/standards/resolver/description-file-schema (v1.2.0 pins to
https://ref.gs1.org/standards/resolver/1.2.0/description-file-schema).

Two fields are mandatory:

  resolverRoot          — the resolver root (the "customURIstem")
  supportedPrimaryKeys  — the GS1 primary keys this resolver answers for

Everything else is optional operator metadata. This module keeps the mandatory
pair *derived from the running configuration* wherever possible, so the published
description cannot silently drift from what the resolver actually does:

  * ``supportedPrimaryKeys`` defaults to the set of primary keys the configured
    routes can actually match (see :func:`derive_primary_keys`).
  * ``resolverRoot`` defaults to the origin the request arrived on.

An operator can override either explicitly in the ``well_known:`` block of
routes.yaml — see ``config/routes.example.yaml``.

Optional fields are passed through as the operator supplies them (type-checked
but not otherwise modelled), so this module does not have to track every
future addition to the standard's vocabulary.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .router import Route

#: The JSON schema the published document is required to validate against.
DESCRIPTION_FILE_SCHEMA = "https://ref.gs1.org/standards/resolver/description-file-schema"

#: Version-pinned form of the same schema, for immutable references.
DESCRIPTION_FILE_SCHEMA_VERSIONED = (
    "https://ref.gs1.org/standards/resolver/1.2.0/description-file-schema"
)

#: The path the standard reserves for the description file.
WELL_KNOWN_PATH = "/.well-known/gs1resolver"

#: Permitted values for ``supportedPrimaryKeys``, per the standard's schema.
#: "all" asserts that every GS1 primary identification key is supported.
SUPPORTED_PRIMARY_KEYS = (
    "all",
    "00",
    "01",
    "253",
    "255",
    "401",
    "402",
    "410",
    "411",
    "412",
    "413",
    "414",
    "415",
    "417",
    "8003",
    "8004",
    "8006",
    "8010",
    "8013",
    "8017",
    "8018",
)

#: Optional operator metadata: ``routes.yaml`` key -> (JSON property, JSON type).
#: Values are passed through unmodified once the type checks out.
_OPTIONAL_FIELDS: dict[str, tuple[str, type | tuple[type, ...]]] = {
    "name": ("name", str),
    "terms_of_use": ("termsOfUse", str),
    "supported_link_type": ("supportedLinkType", list),
    "link_type_default_can_be_linkset": ("linkTypeDefaultCanBeLinkset", bool),
    "supported_context_values_enumerated": ("supportedContextValuesEnumerated", list),
    "supported_context_values_external": ("supportedContextValuesExternal", list),
    "extension_profile": ("extensionProfile", str),
    "json_ld_context_location": ("jsonLdContextLocation", str),
    "contact": ("contact", dict),
}


class WellKnownConfigError(ValueError):
    """Raised when the ``well_known:`` block in routes.yaml is invalid.

    Carried by ``ConfigError`` at load time so a bad description file fails the
    service at startup rather than producing a non-conformant document later.
    """


def validate_well_known_config(block: object) -> dict:
    """Validate the optional ``well_known:`` block, returning it normalised.

    Raises :class:`WellKnownConfigError` with an actionable message. An absent
    block is valid and yields ``{}`` — the mandatory fields are then derived.
    """
    if block is None:
        return {}
    if not isinstance(block, dict):
        raise WellKnownConfigError("'well_known' must be a mapping")

    known = {"resolver_root", "supported_primary_keys", *_OPTIONAL_FIELDS}
    for key in block:
        if key not in known:
            raise WellKnownConfigError(
                f"unknown 'well_known' key {key!r}; supported keys are {', '.join(sorted(known))}"
            )

    root = block.get("resolver_root")
    if root is not None and (not isinstance(root, str) or not root):
        raise WellKnownConfigError("'well_known.resolver_root' must be a non-empty string")

    keys = block.get("supported_primary_keys")
    if keys is not None:
        if not isinstance(keys, list) or not keys:
            raise WellKnownConfigError(
                "'well_known.supported_primary_keys' must be a non-empty list"
            )
        for key in keys:
            if key not in SUPPORTED_PRIMARY_KEYS:
                raise WellKnownConfigError(
                    f"'well_known.supported_primary_keys' contains {key!r}, which is not a "
                    f"GS1 primary key; permitted values are {', '.join(SUPPORTED_PRIMARY_KEYS)}"
                )

    for key, (prop, expected) in _OPTIONAL_FIELDS.items():
        if key in block and not isinstance(block[key], expected):
            name = expected.__name__ if isinstance(expected, type) else "the documented type"
            raise WellKnownConfigError(f"'well_known.{key}' ({prop}) must be of type {name}")

    return block


def derive_primary_keys(routes: list[Route]) -> list[str]:
    """Infer ``supportedPrimaryKeys`` from the configured routes.

    A route pins a primary key either explicitly (``primary_ai``) or implicitly:
    the ``gtin_prefix`` / ``gtin_regex`` clauses match against a GTIN, which is
    AI 01.

    A catch-all route is deliberately *not* reported as ``"all"``. It will indeed
    answer for any primary key it is handed, but declaring blanket support for
    every GS1 key is a conformance claim an operator should make on purpose, not
    one this resolver should infer from a fallback rule — so set
    ``well_known.supported_primary_keys`` explicitly to assert it.

    Falls back to ``["01"]`` when nothing can be inferred: GTIN is the primary
    key for the ESPR/DPP case this resolver exists to serve.
    """
    keys: set[str] = set()
    for route in routes:
        match = route.match
        if not isinstance(match, dict):
            continue
        primary = match.get("primary_ai")
        if primary is not None:
            keys.add(str(primary))
        if "gtin_prefix" in match or "gtin_regex" in match:
            keys.add("01")

    # Anything the operator configured that isn't a GS1 primary key would make
    # the document fail schema validation; drop it rather than publish invalid
    # JSON. A deliberate declaration belongs in supported_primary_keys.
    keys &= set(SUPPORTED_PRIMARY_KEYS)
    return sorted(keys) if keys else ["01"]


def build_well_known(
    config: dict | None,
    *,
    routes: list[Route] | None = None,
    request_root: str | None = None,
) -> dict[str, Any]:
    """Build the resolver description document.

    ``config`` is the validated ``well_known:`` block (may be empty or None).
    ``routes`` supplies the derivation for ``supportedPrimaryKeys``.
    ``request_root`` is the origin the request arrived on, used for
    ``resolverRoot`` when the operator has not configured one.
    """
    config = config or {}

    root = config.get("resolver_root") or request_root
    if not root:
        # Neither configured nor derivable. The field is mandatory, so this is a
        # programming error rather than an operator one.
        raise WellKnownConfigError(
            "resolverRoot is mandatory: set 'well_known.resolver_root' in routes.yaml"
        )

    document: dict[str, Any] = {
        "resolverRoot": root.rstrip("/"),
        "supportedPrimaryKeys": list(
            config.get("supported_primary_keys") or derive_primary_keys(routes or [])
        ),
    }

    for key, (prop, _expected) in _OPTIONAL_FIELDS.items():
        if key in config:
            document[prop] = config[key]

    return document
