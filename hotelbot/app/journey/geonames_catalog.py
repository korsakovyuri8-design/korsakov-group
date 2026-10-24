"""Conservative, read-only lookup in a versioned GeoNames cities snapshot.

The snapshot is geography, not provider coverage or an exact pickup address.
Only aliases built without silently discarding significant characters are used.
"""

from __future__ import annotations

import logging
import math
import re
import sqlite3
import unicodedata
from contextlib import closing
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ACTIVE_CATALOG = Path("/opt/atlas-data/geography/current.sqlite")
NORMALIZATION_POLICY = "latin-safe-country-qualified-v1"
LOGGER = logging.getLogger("atlas.geonames")
_HASH = re.compile(r"[0-9a-f]{64}")


def normalize_alias(value: str) -> str | None:
    """Match the builder while refusing loss of unsupported letters/digits.

This snapshot supports Latin aliases and diacritics. Unsupported scripts must
remain unresolved rather than being deleted from the user's place name.
"""
    if not isinstance(value, str) or not value.strip() or len(value) > 512:
        return None
    text = unicodedata.normalize("NFKD", value).casefold()
    text = "".join(c for c in text if not unicodedata.combining(c))
    if any(c.isalnum() and c not in "abcdefghijklmnopqrstuvwxyz0123456789" for c in text):
        return None
    text = text.replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", " ", text).strip() or None


def _valid_metadata(metadata: dict) -> bool:
    expected = {
        "source": "GeoNames cities5000",
        "license": "CC BY 4.0 - attribution required",
        "coordinate_precision": "city_centroid",
        "normalization_policy": NORMALIZATION_POLICY,
    }
    if any(metadata.get(key) != value for key, value in expected.items()):
        return False
    for key in ("source_sha256", "countries_sha256"):
        value = metadata.get(key)
        if not isinstance(value, str) or not _HASH.fullmatch(value):
            return False
    try:
        stamp = metadata.get("built_at")
        if not isinstance(stamp, str):
            return False
        built_at = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        return built_at.tzinfo is not None and built_at.utcoffset() is not None
    except (ValueError, OverflowError):
        return False


def lookup(
    alias_key: str,
    *,
    catalog_path: Path | None = None,
) -> dict | None:
    """Resolve a unique qualified name; missing or invalid data is unresolved.

The input may be a raw name or an already normalized alias. The resolver passes
the raw input so unsupported characters can be detected before normalization.
No connection is cached across threads, requests or snapshot replacements.
"""
    key = normalize_alias(alias_key)
    if key is None:
        return None

    try:
        path = Path(catalog_path) if catalog_path is not None else ACTIVE_CATALOG
        if not path.is_file():
            return None
        uri = path.resolve(strict=True).as_uri() + "?mode=ro&immutable=1"
        with closing(sqlite3.connect(uri, uri=True, timeout=2)) as db:
            ids = db.execute(
                "SELECT DISTINCT city_id FROM aliases WHERE alias_key = ? LIMIT 2",
                (key,),
            ).fetchall()
            # Count identities before joining so an orphan cannot hide ambiguity.
            if len(ids) != 1 or type(ids[0][0]) is not int or ids[0][0] <= 0:
                return None
            row = db.execute(
                "SELECT id, name, country, country_code, timezone, latitude, longitude "
                "FROM cities WHERE id = ?",
                (ids[0][0],),
            ).fetchone()
            if row is None:
                return None
            metadata = dict(db.execute(
                "SELECT key, value FROM metadata WHERE key IN "
                "('source','license','coordinate_precision','normalization_policy',"
                "'source_sha256','countries_sha256','built_at')"
            ))
    except (sqlite3.Error, OSError, RuntimeError, ValueError, TypeError) as exc:
        LOGGER.warning("GeoNames catalog unavailable (%s)", type(exc).__name__)
        return None

    if not _valid_metadata(metadata):
        return None

    ident, name, country, code, zone, lat, lon = row
    if type(ident) is not int or ident <= 0:
        return None
    if any(not isinstance(v, str) or not v.strip() for v in (name, country, code, zone)):
        return None
    if not re.fullmatch(r"[A-Z]{2}", code):
        return None
    country_key = normalize_alias(country)
    if not country_key or not key.endswith(" " + country_key):
        return None
    if not key[:-(len(country_key) + 1)].strip():
        return None
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in (lat, lon)):
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    try:
        ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError):
        return None

    return {
        "status": "resolved",
        "entity_key": f"gn:{ident}",
        "canonical_name": f"{name}, {country}",
        "kind": "city",
        "address": f"{name}, {country}",
        "locality": name,
        "municipality": None,
        "country": country,
        "country_code": code,
        "timezone": zone,
        "coordinates": {"lat": lat, "lon": lon},
        "coordinate_precision": "city_centroid",
        "resolver": "atlas_geonames_snapshot",
        "registry_version": (
            "geonames-cities5000:" + metadata["source_sha256"][:12]
            + ":" + metadata["countries_sha256"][:12]
        ),
        "match": "exact_alias",
        "source_attribution": "GeoNames",
        "source_url": "https://www.geonames.org/",
        "source_license": "CC BY 4.0",
        "source_license_url": "https://creativecommons.org/licenses/by/4.0/",
        "source_sha256": metadata["source_sha256"],
        "source_countries_sha256": metadata["countries_sha256"],
        "source_built_at": metadata["built_at"],
        "source_modified": True,
    }
