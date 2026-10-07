"""Iteration 6: fetch the raw source dumps for the Kotor + Budva pilot.

    python tools/pilot_fetch.py [--pilot data/pilot/kotor_budva] [--only osm|wikidata]
    python tools/pilot_fetch.py --print-queries          # the exact queries, to run elsewhere
    python tools/pilot_fetch.py --import osm kotor=FILE budva=FILE --fetched-at ISO
        # wrap response bodies fetched OUTSIDE this environment (the original
        # HTTP bodies, byte for byte - not a cleaned CSV) into the dump format

The ONLY step that touches the network. Each source's query is run once per
scope box; the exact response bodies are kept (with the query text, the
fetch time and a sha256 of each body) in the source's dump file. Everything
after this - ingest, audit, snapshot - runs offline from the dumps.

Endpoints and etiquette: one request per box, a descriptive User-Agent, and
no retries in a loop (Overpass / WDQS usage policies).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
import yaml

ENDPOINTS = {"osm.overpass.v1": "https://overpass-api.de/api/interpreter",
             "wikidata.sparql.v1": "https://query.wikidata.org/sparql"}
USER_AGENT = "hotelbot-world-pilot/0.1 (real-data pilot, Kotor/Budva; contact: repository owner)"


def query_for(src: dict, box: list[float]) -> str:
    min_lat, min_lon, max_lat, max_lon = box
    q = src["query"]
    if src["format"] == "osm.overpass.v1":
        return q.replace("{bbox}", f"{min_lat},{min_lon},{max_lat},{max_lon}")
    types = " ".join(f"wd:{t}" for t in src["category_map"])
    return (q.replace("{types}", types).replace("{min_lat}", str(min_lat)).replace("{min_lon}", str(min_lon))
            .replace("{max_lat}", str(max_lat)).replace("{max_lon}", str(max_lon)))


def fetch(src: dict, boxes: dict[str, list[float]], client: httpx.Client) -> dict:
    parts = []
    for name, box in boxes.items():
        q = query_for(src, box)
        if src["format"] == "osm.overpass.v1":
            r = client.post(ENDPOINTS[src["format"]], data={"data": q})
        else:
            r = client.get(ENDPOINTS[src["format"]], params={"query": q, "format": "json"},
                           headers={"Accept": "application/sparql-results+json"})
        r.raise_for_status()
        parts.append({"box": name, "query": q, "sha256": hashlib.sha256(r.content).hexdigest(),
                      "body": r.json()})
    return {"format": src["format"], "source_id": src["source_id"],
            "fetched_at": datetime.now(timezone.utc).isoformat(), "parts": parts, "response": merge(src["format"], parts)}


def import_bodies(src: dict, files: dict[str, Path], fetched_at: str, boxes: dict[str, list[float]]) -> dict:
    parts = []
    for box, path in files.items():
        if box not in boxes:
            raise SystemExit(f"unknown box {box!r} (pilot.yaml scope: {sorted(boxes)})")
        body = path.read_bytes()
        parts.append({"box": box, "query": query_for(src, boxes[box]), "sha256": hashlib.sha256(body).hexdigest(),
                      "imported_from": path.name, "body": json.loads(body)})
    datetime.fromisoformat(fetched_at)                     # must be a real time, with offset
    return {"format": src["format"], "source_id": src["source_id"], "fetched_at": fetched_at, "parts": parts,
            "response": merge(src["format"], parts), "fetched_outside_environment": True}


def write_dump(pilot: Path, src: dict, dump: dict) -> None:
    out = pilot / src["dump"]
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        raise SystemExit(f"{out} exists - raw dumps are frozen once written; move it away deliberately")
    out.write_text(json.dumps(dump, ensure_ascii=False, indent=1))
    sums = out.parent / "SHA256SUMS"
    lines = [f"{p['sha256']}  {src['source_id']}:{p['box']} (response body)" for p in dump["parts"]]
    lines.append(f"{hashlib.sha256(out.read_bytes()).hexdigest()}  {out.name}")
    with sums.open("a") as fh:
        fh.write("\n".join(lines) + "\n")
    n = len(dump["response"].get("elements", dump["response"].get("results", {}).get("bindings", [])))
    print(f"{src['source_id']}: {n} raw items -> {out}")


def merge(fmt: str, parts: list[dict]) -> dict:
    """One response per source (boxes may overlap): elements / rows de-duplicated."""
    if fmt == "osm.overpass.v1":
        seen, elements = set(), []
        for p in parts:
            for el in p["body"].get("elements", []):
                key = (el["type"], el["id"])
                if key not in seen:
                    seen.add(key)
                    elements.append(el)
        return {"elements": elements}
    seen_rows, rows = set(), []
    for p in parts:
        for row in p["body"].get("results", {}).get("bindings", []):
            key = json.dumps(row, sort_keys=True)
            if key not in seen_rows:
                seen_rows.add(key)
                rows.append(row)
    return {"results": {"bindings": rows}}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pilot", default="data/pilot/kotor_budva")
    ap.add_argument("--only")
    ap.add_argument("--print-queries", action="store_true")
    ap.add_argument("--import", dest="import_source", metavar="SOURCE_ID")
    ap.add_argument("files", nargs="*", help="BOX=FILE response bodies for --import")
    ap.add_argument("--fetched-at", help="ISO time the imported bodies were fetched")
    args = ap.parse_args()
    pilot = Path(args.pilot)
    cfg = yaml.safe_load((pilot / "pilot.yaml").read_text())
    if args.print_queries:
        for src in cfg["sources"]:
            for box, bounds in cfg["scope"].items():
                method = "POST data=" if src["format"] == "osm.overpass.v1" else "GET format=json&query="
                print(f"### {src['source_id']} / {box}  ->  {ENDPOINTS[src['format']]}  ({method}...)\n")
                print(query_for(src, bounds))
        return 0
    if args.import_source:
        src = next(x for x in cfg["sources"] if x["source_id"] == args.import_source)
        if not args.fetched_at:
            raise SystemExit("--fetched-at is required for imported bodies")
        files = dict((kv.split("=", 1)[0], Path(kv.split("=", 1)[1])) for kv in args.files)
        write_dump(pilot, src, import_bodies(src, files, args.fetched_at, cfg["scope"]))
        return 0
    with httpx.Client(timeout=240, headers={"User-Agent": USER_AGENT}) as client:
        for src in cfg["sources"]:
            if args.only and src["source_id"] != args.only:
                continue
            write_dump(pilot, src, fetch(src, cfg["scope"], client))
    return 0


if __name__ == "__main__":
    sys.exit(main())
