"""Source adapter contract v2.

An adapter TRANSPORTS source-native records; it does not normalize them
(app/world/normalize.py does, by the descriptor's `format`). Contract:

    descriptor                      identity, authority class, licence terms
    discover_scope(scope)           what this source can cover there
    sync_full(scope, cursor)        pages of every record in scope (resumable)
    sync_incremental(scope, cursor) pages of changes since the cursor
    fetch_record(record_id)         one record
    health()                        adapter-side status

Each SyncPage carries a cursor: the pipeline checkpoints it after the page
is stored, so an interrupted sync resumes instead of restarting, and a
replayed page is idempotent (unchanged payload hash = no-op).

Implemented: V1Adapter (any Iteration 4 WorldSource, e.g. the synthetic
region pack) and FixtureAdapter (in-memory, versioned, failure injection -
for tests, evals and benchmarks). Real connectors implement the same five
calls; they must honour the source's terms (no "ignore restrictions"
scraping) - the descriptor's licence fields are mandatory.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol


class SourceError(Exception):
    """The source could not be read (network, auth, malformed page). The
    pipeline records it in source health and keeps the known world."""


@dataclass(frozen=True)
class SourceDescriptor:
    source_id: str
    source_type: str                     # synthetic | fixture | api | partner_feed | official_feed | correction ...
    source_class: str                    # authority class (data/world/policies.yaml)
    name: str
    format: str                          # how normalize.py reads its records
    license: str                         # mandatory: what we may do with the data
    attribution_required: bool = False
    attribution_text: str | None = None
    redistribution: str = "allowed"      # allowed | attribution | display_only | none
    retention_days: int | None = None    # how long the raw payload may be kept
    cache_raw: bool = True               # may the raw payload be stored at all?
    default_confidence: float = 0.8
    config: dict[str, Any] = field(default_factory=dict)   # category_map, country_code, timezone, region...

    def validate(self, source_classes: tuple[str, ...]) -> None:
        if not self.license or self.license.strip().lower() in ("unknown", "none", "?"):
            raise ValueError(f"source {self.source_id!r}: a licence must be declared before any data is used")
        if self.source_class not in source_classes:
            raise ValueError(f"source {self.source_id!r}: unknown source_class {self.source_class!r}")
        if self.redistribution not in ("allowed", "attribution", "display_only", "none"):
            raise ValueError(f"source {self.source_id!r}: bad redistribution {self.redistribution!r}")
        if self.attribution_required and not self.attribution_text:
            raise ValueError(f"source {self.source_id!r}: attribution required but no attribution text")


@dataclass(frozen=True)
class Scope:
    """What to sync: a coverage area (country / locality / ...), a bounding
    box, a radius, a provider-defined area, or everything."""

    kind: str = "all"                    # all | area | bbox | radius
    area_code: str | None = None
    bbox: tuple[float, float, float, float] | None = None     # min_lat, min_lon, max_lat, max_lon
    center: tuple[float, float] | None = None
    radius_km: float | None = None

    def contains(self, lat: float | None, lon: float | None) -> bool | None:
        """None = cannot tell (no coordinates, or an area resolved elsewhere)."""
        if self.kind == "all":
            return True
        if lat is None or lon is None:
            return None
        if self.bbox is not None:
            a, b, c, d = self.bbox
            return a <= lat <= c and b <= lon <= d
        if self.center is not None and self.radius_km is not None:
            from app.shared.geo import Point, distance_km

            return distance_km(Point(*self.center), Point(lat, lon)) <= self.radius_km
        return None


@dataclass(frozen=True)
class SourceRecord:
    record_id: str
    kind: str                            # place | event | taxonomy
    payload: dict[str, Any]              # source-native
    observed_at: datetime | None = None  # when the SOURCE last verified/updated it
    deleted: bool = False                # the source says it no longer exists
    url: str | None = None


@dataclass(frozen=True)
class SyncPage:
    records: list[SourceRecord]
    cursor: dict[str, Any]
    last: bool = False


class SourceAdapter(Protocol):
    descriptor: SourceDescriptor

    def discover_scope(self, scope: Scope) -> dict[str, Any]: ...

    def sync_full(self, scope: Scope, cursor: dict[str, Any] | None = None) -> Iterator[SyncPage]: ...

    def sync_incremental(self, scope: Scope, cursor: dict[str, Any]) -> Iterator[SyncPage]: ...

    def fetch_record(self, record_id: str) -> SourceRecord | None: ...

    def health(self) -> dict[str, Any]: ...


def _plain(value: Any) -> Any:
    """Dataclass/Decimal/datetime -> JSON-able source-native payload."""
    from decimal import Decimal

    if dataclasses.is_dataclass(value):
        return {f.name: _plain(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


class V1Adapter:
    """Any Iteration 4 WorldSource (region pack, FixtureSource) as a v2
    adapter. Format "hotelbot.world.v1"; one page; no incremental feed (an
    incremental request is served as a full read without tombstoning)."""

    def __init__(self, source: Any, *, source_class: str | None = None, license: str | None = None,
                 attribution_text: str | None = None) -> None:
        self.source = source
        region = source.region()
        synthetic = source.source_type in ("synthetic", "fixture")
        self.descriptor = SourceDescriptor(
            source_id=source.source_id, source_type=source.source_type,
            source_class=source_class or ("synthetic" if synthetic else "partner_feed"),
            name=getattr(source, "name", None) or region.name, format="hotelbot.world.v1",
            license=license or ("synthetic development data (internal, no restrictions)" if synthetic
                                else "internal"),
            attribution_text=attribution_text, default_confidence=1.0,
            config={"region": region.slug, "timezone": region.timezone, "center": list(region.center),
                    "synthetic": synthetic, "region_name": region.name},
        )

    def discover_scope(self, scope: Scope) -> dict[str, Any]:
        region = self.source.region()
        return {"areas": [region.slug], "center": region.center}

    def _records(self) -> list[SourceRecord]:
        out = [SourceRecord(f"taxonomy:{t.key}", "taxonomy", _plain(t)) for t in self.source.taxonomy()]
        for p in self.source.places():
            out.append(SourceRecord(p.slug, "place", _plain(p), observed_at=p.provenance.last_verified_at))
        for e in self.source.events():
            out.append(SourceRecord(f"event:{e.slug}", "event", _plain(e), observed_at=e.provenance.last_verified_at))
        return out

    def sync_full(self, scope: Scope, cursor: dict[str, Any] | None = None) -> Iterator[SyncPage]:
        yield SyncPage(self._records(), {"page": 1}, last=True)

    def sync_incremental(self, scope: Scope, cursor: dict[str, Any]) -> Iterator[SyncPage]:
        yield SyncPage(self._records(), {"page": 1}, last=True)

    def fetch_record(self, record_id: str) -> SourceRecord | None:
        return next((r for r in self._records() if r.record_id == record_id), None)

    def health(self) -> dict[str, Any]:
        return {"status": "ok"}


class FixtureAdapter:
    """In-memory source in the "generic.v1" (directory-like) format.

    `records`: the current full content. `changes`: an append-only change
    feed [{"version": n, "record": {...}} | {"version": n, "deleted": id}]
    served by sync_incremental from cursor {"version": v}. `fail_after`:
    raise SourceError after that many records of a sync (a broken feed)."""

    def __init__(self, descriptor: SourceDescriptor, records: list[dict[str, Any]] | None = None, *,
                 changes: list[dict[str, Any]] | None = None, page_size: int = 50,
                 fail_after: int | None = None) -> None:
        self.descriptor = descriptor
        self.records = list(records or [])
        self.changes = list(changes or [])
        self.page_size = page_size
        self.fail_after = fail_after
        self.served = 0

    @staticmethod
    def _record(raw: dict[str, Any]) -> SourceRecord:
        observed = raw.get("updated_at")
        when = datetime.fromisoformat(observed) if isinstance(observed, str) else observed
        return SourceRecord(str(raw["id"]), raw.get("type", "place"), dict(raw), observed_at=when,
                            deleted=bool(raw.get("_deleted")), url=raw.get("url"))

    def discover_scope(self, scope: Scope) -> dict[str, Any]:
        return {"records": len(self.records)}

    def _serve(self, records: list[SourceRecord], start: int, base_cursor: dict[str, Any]) -> Iterator[SyncPage]:
        for i in range(start, max(len(records), 1), self.page_size):
            page = records[i:i + self.page_size]
            for _ in page:
                if self.fail_after is not None and self.served >= self.fail_after:
                    raise SourceError(f"{self.descriptor.source_id}: connection reset after {self.served} records")
                self.served += 1
            nxt = i + self.page_size
            yield SyncPage(page, {**base_cursor, "offset": nxt}, last=nxt >= len(records))

    def sync_full(self, scope: Scope, cursor: dict[str, Any] | None = None) -> Iterator[SyncPage]:
        recs = [self._record(r) for r in self.records]
        recs = [r for r in recs if scope.contains(r.payload.get("lat"), r.payload.get("lon")) is not False]
        start = int((cursor or {}).get("offset", 0))
        latest = max((c["version"] for c in self.changes), default=0)
        yield from self._serve(recs, start, {"version": latest})

    def sync_incremental(self, scope: Scope, cursor: dict[str, Any]) -> Iterator[SyncPage]:
        since = int((cursor or {}).get("version", 0))
        recs = []
        for c in sorted(self.changes, key=lambda c: c["version"]):
            if c["version"] <= since:
                continue
            if "deleted" in c:
                recs.append(SourceRecord(str(c["deleted"]), c.get("type", "place"), {"id": c["deleted"]}, deleted=True))
            else:
                recs.append(self._record(c["record"]))
        latest = max((c["version"] for c in self.changes), default=since)
        if not recs:
            yield SyncPage([], {"version": latest}, last=True)
            return
        for page in self._serve(recs, 0, {"version": latest}):
            yield page

    def fetch_record(self, record_id: str) -> SourceRecord | None:
        raw = next((r for r in self.records if str(r["id"]) == record_id), None)
        return self._record(raw) if raw else None

    def health(self) -> dict[str, Any]:
        return {"status": "failing" if self.fail_after is not None else "ok"}


class RawDumpAdapter:
    """A REAL source read from a saved raw dump (Iteration 6): the exact
    response bytes of an Overpass or Wikidata SPARQL query, saved by
    tools/pilot_fetch.py with the query, the fetch time and a sha256. Ingest
    is offline and reproducible; the dump IS the evidence.

    Dump file: {"format": "osm.overpass.v1" | "wikidata.sparql.v1",
                "fetched_at": iso, "query": str, "sha256": str, "response": {...}}"""

    def __init__(self, descriptor: SourceDescriptor, dump: dict[str, Any], page_size: int = 200) -> None:
        if dump.get("format") != descriptor.format:
            raise SourceError(f"{descriptor.source_id}: dump format {dump.get('format')!r} != {descriptor.format!r}")
        self.descriptor, self.dump, self.page_size = descriptor, dump, page_size
        fetched = dump.get("fetched_at")
        self.fetched_at = datetime.fromisoformat(fetched) if isinstance(fetched, str) else None

    def _records(self) -> list[SourceRecord]:
        resp = self.dump["response"]
        if self.descriptor.format == "osm.overpass.v1":
            return [SourceRecord(f"{el['type']}/{el['id']}", "place", el,
                                 observed_at=_iso_or(el.get("timestamp"), self.fetched_at),
                                 url=f"https://www.openstreetmap.org/{el['type']}/{el['id']}")
                    for el in resp.get("elements", []) if el.get("tags")]
        from app.world.real_formats import wikidata_group

        return [SourceRecord(qid, "place", {"rows": rows}, observed_at=self.fetched_at,
                             url=f"https://www.wikidata.org/wiki/{qid}")
                for qid, rows in wikidata_group(resp.get("results", {}).get("bindings", [])).items()]

    def discover_scope(self, scope: Scope) -> dict[str, Any]:
        return {"records": len(self._records())}

    def sync_full(self, scope: Scope, cursor: dict[str, Any] | None = None) -> Iterator[SyncPage]:
        recs = self._records()
        start = int((cursor or {}).get("offset", 0))
        for i in range(start, max(len(recs), 1), self.page_size):
            nxt = i + self.page_size
            yield SyncPage(recs[i:nxt], {"offset": nxt, "sha256": self.dump.get("sha256")}, last=nxt >= len(recs))

    def sync_incremental(self, scope: Scope, cursor: dict[str, Any]) -> Iterator[SyncPage]:
        yield from self.sync_full(scope)          # a dump is a full snapshot

    def fetch_record(self, record_id: str) -> SourceRecord | None:
        return next((r for r in self._records() if r.record_id == record_id), None)

    def health(self) -> dict[str, Any]:
        return {"ok": True, "fetched_at": self.dump.get("fetched_at")}


def _iso_or(value: Any, default: datetime | None) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")) if value else default
    except ValueError:
        return default
