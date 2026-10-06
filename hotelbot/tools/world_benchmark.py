"""Large synthetic world benchmark for the data fabric.

    python tools/world_benchmark.py --entities 10000 [--db URL]

Generates cities of businesses with a KNOWN ground truth, publishes them
through three sources with realistic noise, ingests everything through the
fabric and reports:
  * ingest throughput (records/s) per source;
  * entity resolution vs ground truth: FALSE MERGES (different businesses in
    one entity - must be 0), missed duplicates (one business, several
    entities - tolerated, conservative by design), ambiguous reviews opened;
  * spatial query latency: geohash index vs full region scan (same results).

Noise: spelling variants, transliteration (Cyrillic), translated names
(only with a corroborating phone), coordinate jitter (<= 25 m), phone
formats, missing fields; distractors: similar names nearby (different
businesses), chains (same brand + website in several places).
"""

from __future__ import annotations

import argparse
import random
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.db.models import Place, SourceEntity  # noqa: E402
from app.db.session import create_schema, make_engine  # noqa: E402
from app.shared.geo import Point, distance_km  # noqa: E402
from app.world import coverage  # noqa: E402
from app.world.adapters import FixtureAdapter, SourceDescriptor  # noqa: E402
from app.world.ingest import active_canonical, run_sync  # noqa: E402
from app.world.spatial import GeohashIndex  # noqa: E402

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
CITIES = [("podgorica", 42.441, 19.263), ("niksic", 42.773, 18.944), ("budva", 42.288, 18.840),
          ("kotor", 42.425, 18.771), ("bar", 42.098, 19.100), ("zabljak", 43.155, 19.121),
          ("tbilisi", 41.715, 44.793), ("lyon", 45.764, 4.835)]
WORDS = ["Stari", "Grad", "Mlin", "Lipa", "Sunce", "More", "Planina", "Jezero", "Kamen", "Vidikovac", "Most",
         "Ribar", "Maslina", "Bor", "Zora", "Luka", "Kula", "Vrelo", "Sjenka", "Orah", "Gora", "Polje", "Breza",
         "Tvrdjava", "Ruza", "Sokol", "Vuk", "Zvijezda", "Mjesec", "Bijela", "Crna", "Zlatna", "Plava", "Nova"]
KINDS = [("restaurant", ["Restoran", "Restaurant"]), ("konoba", ["Konoba", "Tavern"]), ("cafe", ["Kafe", "Cafe"]),
         ("bar", ["Bar", "Bar"]), ("pharmacy", ["Apoteka", "Pharmacy"]), ("gallery", ["Galerija", "Gallery"]),
         ("bakery", ["Pekara", "Bakery"]), ("dessert", ["Poslastičarnica", "Desserts"])]
_LAT2CYR = {"a": "а", "b": "б", "v": "в", "g": "г", "d": "д", "e": "е", "z": "з", "i": "и", "j": "ј", "k": "к",
            "l": "л", "m": "м", "n": "н", "o": "о", "p": "п", "r": "р", "s": "с", "t": "т", "u": "у", "f": "ф",
            "h": "х", "c": "ц"}


def cyr(text: str) -> str:
    return "".join((_LAT2CYR.get(ch.lower(), ch).upper() if ch.isupper() else _LAT2CYR.get(ch, ch)) for ch in text)


def generate(n: int, seed: int = 11) -> tuple[list[dict], dict[str, list[dict]]]:
    rng = random.Random(seed)
    truth: list[dict] = []
    for i in range(n):
        city, clat, clon = rng.choice(CITIES)
        kind, (local_word, en_word) = rng.choice(KINDS)
        core = " ".join(rng.sample(WORDS, 2)) + (f" {i % 97}" if rng.random() < 0.15 else "")
        truth.append({"truth": f"T{i}", "city": city, "kind": kind, "local": f"{local_word} {core}",
                      "en": f"{core} {en_word}", "lat": clat + rng.uniform(-0.05, 0.05),
                      "lon": clon + rng.uniform(-0.07, 0.07), "phone": f"0{rng.randint(67, 69)} {rng.randint(100, 999)} {rng.randint(100, 999)}",
                      "site": f"{core.lower().replace(' ', '-')}-{i}.me" if rng.random() < 0.5 else None})
    # distractors: a DIFFERENT business with a similar name 40-150 m away
    for t in rng.sample(truth, n // 20):
        words = t["local"].split()
        words[-1] = rng.choice(WORDS)
        truth.append({**t, "truth": t["truth"] + "-d", "local": " ".join(words), "en": None,
                      "lat": t["lat"] + rng.uniform(0.0004, 0.0013), "phone": f"067 {rng.randint(100, 999)} 000",
                      "site": None})
    # chains: same brand + website in another city
    for t in rng.sample(truth, n // 50):
        city, clat, clon = rng.choice(CITIES)
        truth.append({**t, "truth": t["truth"] + "-c", "lat": clat + rng.uniform(-0.05, 0.05),
                      "lon": clon + rng.uniform(-0.05, 0.05), "phone": None})

    def jitter(v: float) -> float:
        return v + rng.uniform(-0.0002, 0.0002)          # <= ~25 m

    sources: dict[str, list[dict]] = defaultdict(list)
    for t in truth:
        base = {"category": t["kind"], "truth": t["truth"]}
        if rng.random() < 0.9:                            # directory: local name, sometimes misspelled
            name = t["local"]
            if rng.random() < 0.1:
                pos = rng.randrange(1, len(name) - 1)
                name = name[:pos] + name[pos + 1:]
            sources["dir"].append({**base, "id": f"d-{t['truth']}", "name": name, "lat": jitter(t["lat"]),
                                   "lon": jitter(t["lon"]), "phone": t["phone"] if rng.random() < 0.7 else None,
                                   "opening_hours": "Mo-Su 09:00-22:00" if rng.random() < 0.6 else None})
        if rng.random() < 0.4:                            # partner: formal phone, website, Cyrillic names
            name = cyr(t["local"]) if rng.random() < 0.3 else t["local"]
            sources["partner"].append({**base, "id": f"p-{t['truth']}", "name": name, "lat": jitter(t["lat"]),
                                       "lon": jitter(t["lon"]),
                                       "phone": ("+382 " + t["phone"][1:]) if t["phone"] else None,
                                       "website": t["site"], "opening_hours": "Mo-Su 09:00-23:00"})
        if rng.random() < 0.3 and t["en"]:                # tourism feed: English (translated) name + phone
            sources["tour"].append({**base, "id": f"t-{t['truth']}", "name": t["en"], "lat": jitter(t["lat"]),
                                    "lon": jitter(t["lon"]), "phone": t["phone"]})
    return truth, sources


def _pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    v = sorted(values)
    return round(v[min(len(v) - 1, int(q * len(v)))] * 1000, 2)


def run(entities: int, db: str, *, queries: int = 200) -> dict:
    from sqlalchemy import func

    from app.db.models import CanonicalEntity, FactAssertion, MatchReview
    from app.world import matching, projection

    engine = make_engine(db)
    with engine.begin() as conn:                       # clean slate (benchmark database only)
        from sqlalchemy import MetaData

        meta = MetaData()
        meta.reflect(conn)
        meta.drop_all(conn)
    create_schema(engine)
    out: dict = {"db": db.split(":")[0], "truth_seed_entities": entities}
    proj_times: list[float] = []
    original = projection.project

    def timed(*a, **kw):  # noqa: ANN002, ANN003
        t0 = time.perf_counter()
        try:
            return original(*a, **kw)
        finally:
            proj_times.append(time.perf_counter() - t0)

    projection.project = timed
    for k in matching.STATS:
        matching.STATS[k] = 0
    page_times: list[float] = []
    with sessionmaker(engine)() as s:
        for code, lat, lon in CITIES:
            coverage.upsert_area(s, code=code, kind="locality", name=code.title(), center=(lat, lon), radius_km=15,
                                 country_code="ME" if code not in ("tbilisi", "lyon") else None)
        s.commit()
        truth, sources = generate(entities)
        out["truth_businesses"] = len(truth)
        classes = {"dir": "directory", "partner": "partner_feed", "tour": "tourism_feed"}
        total_records, total_seconds = 0, 0.0
        for name, records in sources.items():
            d = SourceDescriptor(source_id=f"bench:{name}", source_type="fixture", source_class=classes[name],
                                 name=name, format="generic.v1", license="benchmark (synthetic)",
                                 config={"country_code": "ME", "timezone": "Europe/Podgorica"})
            last = [time.perf_counter()]

            def checkpoint(sess, last=last):  # noqa: ANN001
                sess.commit()
                now = time.perf_counter()
                page_times.append(now - last[0])
                last[0] = now

            t0 = time.perf_counter()
            run_sync(s, FixtureAdapter(d, records, page_size=500), now=NOW, checkpoint=checkpoint)
            s.commit()
            dt = time.perf_counter() - t0
            total_records += len(records)
            total_seconds += dt
            out[f"ingest_{name}"] = {"records": len(records), "seconds": round(dt, 1),
                                     "records_per_s": round(len(records) / dt, 1)}
        projection.project = original
        out["ingest_total"] = {
            "source_records": total_records, "seconds": round(total_seconds, 1),
            "records_per_s": round(total_records / total_seconds, 1),
            "canonical_entities": s.scalar(select(func.count()).select_from(CanonicalEntity)),
            "assertions": s.scalar(select(func.count()).select_from(FactAssertion)),
            "er_decisions": matching.STATS["decisions"],
            "er_candidates_evaluated": matching.STATS["candidates_evaluated"],
            "er_candidates_per_decision": round(matching.STATS["candidates_evaluated"]
                                                / max(matching.STATS["decisions"], 1), 2),
            "page_500_p50_ms": _pct(page_times, 0.5), "page_500_p95_ms": _pct(page_times, 0.95),
            "projection_p50_ms": _pct(proj_times, 0.5), "projection_p95_ms": _pct(proj_times, 0.95)}
        # ---- entity resolution vs ground truth (pairwise)
        clusters: dict[str, list[str]] = defaultdict(list)
        by_truth: dict[str, list[str]] = defaultdict(list)
        for se in s.scalars(select(SourceEntity).where(SourceEntity.source_id.like("bench:%"))):
            tr = (se.raw or {}).get("truth")
            clusters[active_canonical(s, se.id)].append(tr)
            by_truth[tr].append(se.id)

        def pairs(n: int) -> int:
            return n * (n - 1) // 2

        predicted = sum(pairs(len(m)) for m in clusters.values())
        correct = sum(sum(pairs(c) for c in Counter(m).values()) for m in clusters.values())
        true_pairs = sum(pairs(len(v)) for v in by_truth.values())
        false_merge_entities = sum(1 for m in clusters.values() if len(set(m)) > 1)
        reviews = s.scalar(select(func.count()).select_from(MatchReview))
        out["entity_resolution"] = {
            "precision": round(correct / predicted, 4) if predicted else 1.0,
            "recall": round(correct / true_pairs, 4) if true_pairs else 1.0,
            "false_merges_entities": false_merge_entities,
            "false_merge_pairs": predicted - correct,
            "missed_links_pairs": true_pairs - correct,
            "ambiguous_rate": round(reviews / total_records, 4),
            "match_rate": round(matching.STATS["match"] / max(matching.STATS["decisions"], 1), 4)}
        # ---- spatial: index vs full scan, nearest-N
        idx = GeohashIndex()
        rng = random.Random(3)
        t_idx, t_scan, t_near, mismatches = [], [], [], 0
        for i in range(queries):
            _, lat, lon = rng.choice(CITIES)
            c = Point(lat + rng.uniform(-0.03, 0.03), lon + rng.uniform(-0.03, 0.03))
            t0 = time.perf_counter()
            a = {p.id for p, _ in idx.within_radius(s, c, 1.0)}
            t_idx.append(time.perf_counter() - t0)
            t0 = time.perf_counter()
            idx.nearest(s, c, 5)
            t_near.append(time.perf_counter() - t0)
            if i < 20:                                  # full scans are slow: correctness sample
                t0 = time.perf_counter()
                b = {p.id for p in s.scalars(select(Place).where(Place.active))
                     if p.latitude is not None and distance_km(c, Point(p.latitude, p.longitude)) <= 1.0}
                t_scan.append(time.perf_counter() - t0)
                mismatches += a != b
        out["spatial"] = {"places": s.scalar(select(func.count()).select_from(Place)),
                          "radius_1km_p50_ms": _pct(t_idx, 0.5), "radius_1km_p95_ms": _pct(t_idx, 0.95),
                          "nearest_5_p50_ms": _pct(t_near, 0.5), "nearest_5_p95_ms": _pct(t_near, 0.95),
                          "full_scan_p50_ms": _pct(t_scan, 0.5), "index_vs_scan_mismatches": mismatches}
    engine.dispose()
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--entities", type=int, default=2000)
    ap.add_argument("--db", default="sqlite://")
    args = ap.parse_args()
    import json

    print(json.dumps(run(args.entities, args.db), indent=2))
