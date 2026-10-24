"""Build an offline, country-qualified GeoNames city index.

Source: https://www.geonames.org/
License: CC BY 4.0. Attribution is required.
Coordinates describe city centres, not pickup addresses.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import os
import re
import sqlite3
import tempfile
import unicodedata
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

NORMALIZATION_POLICY = "latin-safe-country-qualified-v1"


def norm(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).casefold()
    value = "".join(c for c in value if not unicodedata.combining(c))
    if any(c.isalnum() and c not in "abcdefghijklmnopqrstuvwxyz0123456789" for c in value):
        return ""
    value = value.replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def build(
    cities_zip: Path,
    countries_txt: Path,
    output: Path,
    min_cities: int = 20000,
) -> tuple[int, int]:
    if output.exists():
        raise ValueError("Snapshot already exists")

    countries = {}
    for line in countries_txt.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) >= 5 and len(parts[0]) == 2 and parts[4].strip():
            countries[parts[0]] = parts[4].strip()
    if len(countries) < 200:
        raise ValueError("Country registry incomplete")

    output.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(
        prefix="geonames-build-", suffix=".sqlite", dir=output.parent,
    )
    os.close(fd)
    temp = Path(name)
    cities_count = 0
    aliases_count = 0
    valid_zones = set()
    invalid_zones = set()
    conn = None

    try:
        conn = sqlite3.connect(temp)
        conn.executescript("""
            PRAGMA journal_mode=OFF;
            PRAGMA synchronous=OFF;
            PRAGMA cache_size=-8192;
            CREATE TABLE cities (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                country TEXT NOT NULL,
                country_code TEXT NOT NULL,
                timezone TEXT NOT NULL,
                latitude REAL NOT NULL,
                longitude REAL NOT NULL,
                population INTEGER NOT NULL
            );
            CREATE TABLE aliases (
                alias_key TEXT NOT NULL,
                city_id INTEGER NOT NULL
            );
            CREATE TABLE metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        """)

        with zipfile.ZipFile(cities_zip) as archive:
            if "cities5000.txt" not in archive.namelist():
                raise ValueError("Not a GeoNames cities5000 archive")
            with archive.open("cities5000.txt") as raw:
                stream = io.TextIOWrapper(raw, encoding="utf-8")
                for line in stream:
                    fields = line.rstrip("\r\n").split("\t")
                    if len(fields) < 19 or fields[6] != "P":
                        continue
                    country_code = fields[8]
                    country = countries.get(country_code)
                    zone = fields[17]
                    if not country or not zone or zone in invalid_zones:
                        continue
                    if zone not in valid_zones:
                        try:
                            ZoneInfo(zone)
                        except (ZoneInfoNotFoundError, ValueError):
                            invalid_zones.add(zone)
                            continue
                        valid_zones.add(zone)
                    try:
                        ident = int(fields[0])
                        lat = float(fields[4])
                        lon = float(fields[5])
                        population = max(0, int(fields[14] or "0"))
                        if not (ident > 0 and -90 <= lat <= 90 and -180 <= lon <= 180):
                            continue
                    except ValueError:
                        continue
                    city = fields[1].strip()
                    if not city:
                        continue
                    names = (city, fields[2], *fields[3].split(",")[:100])
                    country_key = norm(country)
                    aliases = set()
                    for alias in names:
                        if not alias.strip() or len(alias) >= 150:
                            continue
                        city_key = norm(alias)
                        if city_key and country_key:
                            aliases.add(city_key + " " + country_key)
                    if not aliases:
                        continue
                    conn.execute(
                        "INSERT INTO cities VALUES (?,?,?,?,?,?,?,?)",
                        (ident, city, country, country_code, zone, lat, lon, population),
                    )
                    conn.executemany(
                        "INSERT INTO aliases VALUES (?,?)",
                        ((a, ident) for a in sorted(aliases)),
                    )
                    cities_count += 1
                    aliases_count += len(aliases)

        if cities_count < min_cities or aliases_count < cities_count:
            raise ValueError(f"Incomplete: {cities_count} cities")
        conn.executescript("""
            CREATE UNIQUE INDEX aliases_exact ON aliases(alias_key, city_id);
            CREATE INDEX aliases_lookup ON aliases(alias_key);
        """)
        metadata = {
            "source": "GeoNames cities5000",
            "license": "CC BY 4.0 - attribution required",
            "source_sha256": digest(cities_zip),
            "countries_sha256": digest(countries_txt),
            "built_at": datetime.now(timezone.utc).isoformat(),
            "coordinate_precision": "city_centroid",
            "normalization_policy": NORMALIZATION_POLICY,
            "cities": str(cities_count),
            "aliases": str(aliases_count),
        }
        conn.executemany("INSERT INTO metadata VALUES (?,?)", metadata.items())
        conn.commit()
        if conn.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise ValueError("SQLite integrity check failed")
        conn.close()
        conn = None
        # Both names are on the same filesystem; link is atomic and refuses
        # an existing destination, including one created during this build.
        os.link(temp, output)
        return cities_count, aliases_count
    finally:
        if conn is not None:
            conn.close()
        temp.unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--cities-zip", type=Path, required=True)
    parser.add_argument("--countries", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    cities, aliases = build(args.cities_zip, args.countries, args.output)
    print(f"CITY_RECORDS={cities}")
    print(f"COUNTRY_QUALIFIED_ALIASES={aliases}")
    print(f"CATALOG_PATH={args.output}")
    print(f"NORMALIZATION_POLICY={NORMALIZATION_POLICY}")
    print("ATLAS_GLOBAL_CATALOG_BUILD=PASS")
