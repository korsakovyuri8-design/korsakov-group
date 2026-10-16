"""Write the REAL /demo/* responses of the Atlas public view API to docs/atlas_api/.

    python tools/atlas_api_examples.py

Runs the service in-process along the sample path (sample -> transfer ->
flight change), plus a pasted supported e-mail and an unreadable one. Session
tokens are replaced by a placeholder; nothing else is edited. These files are
what a frontend adapter (e.g. the Lovable UI) should be tested against."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from app.atlas_web.app import create_app  # noqa: E402
from app.atlas_web.sessions import SessionStore  # noqa: E402

OUT = ROOT / "docs" / "atlas_api"
TOKEN = "<session-token>"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    f1 = (ROOT / "data/trips/fixtures/F1_airline_lgw_tgd.eml").read_text(encoding="utf-8").split("\n\n", 1)[1]
    with TestClient(create_app(store=SessionStore())) as c:
        sample = c.get("/demo/sample").json()
        sid = sample["session"]
        steps = [
            ("01_get_demo_sample", "GET /demo/sample", sample),
            ("02_post_import_sample_transfer", 'POST /demo/import {"session", "sample": "transfer"}',
             c.post("/demo/import", json={"session": sid, "sample": "transfer"}).json()),
            ("03_post_import_sample_flight_change", 'POST /demo/import {"session", "sample": "flight_change"}',
             c.post("/demo/import", json={"session": sid, "sample": "flight_change"}).json()),
            ("04_get_demo_trip", "GET /demo/trip/{session}", c.get(f"/demo/trip/{sid}").json()),
            ("05_post_import_pasted_email", 'POST /demo/import {"text": <Demo Air confirmation body>}',
             c.post("/demo/import", json={"text": f1}).json()),
            ("06_post_import_unreadable", 'POST /demo/import {"text": "not a booking"}',
             c.post("/demo/import", json={"text": "not a booking"}).json()),
        ]
    for name, request, body in steps:
        if body.get("session"):
            body["session"] = TOKEN
        (OUT / f"{name}.json").write_text(
            json.dumps({"request": request, "response": body}, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
        print(f"wrote docs/atlas_api/{name}.json")


if __name__ == "__main__":
    main()
