# Operations

## Processes

| Process | Command | Notes |
|---|---|---|
| API (+ in-process worker) | `uvicorn app.main:create_app --factory` | `HOTELBOT_WORKER_ENABLED=true` (default) runs a background worker thread. |
| Extra workers | `python -m app.worker` | Safe to run several on PostgreSQL (`FOR UPDATE SKIP LOCKED`). With SQLite use one worker. |

Jobs: `process_inbound`, `send_message`, `provider_submit`, `provider_cancel`, `notify_guest`. Inspect them with `GET /api/staff/jobs?job_status=dead|pending|running|succeeded`.

A DEAD `provider_submit` has already marked the booking FAILED, told the guest and opened a staff handoff. To retry by hand, set the job back to `pending`; its idempotency key prevents duplicate bookings.

## Migrations

| Revision | Content | Reversible |
|---|---|---|
| `0001_core_v1` | Core v1 schema | - |
| `0002_stay_engine` | Property / Stay / Action generalisation, data migration | **forward-only** |
| `0003_transactions` | providers, quotes, external transactions, provider events, jobs | additive |
| `0004_local_travel` | places, events, offerings, availability, itinerary; provider region | additive |
| `0005_marketplace` | inventory holds, property-provider relationships, offering pricing/policies, commercial metadata, quote terms | additive (reversible) |

The app migrates on startup when `HOTELBOT_AUTO_MIGRATE=true`:

- an empty DB is created from the models and stamped;
- a versioned DB is upgraded to head;
- an unversioned Core v1 DB is stamped `0001`, then upgraded.

To run it manually:

```bash
HOTELBOT_AUTO_MIGRATE=false alembic upgrade head
```

## Backup and restore (PostgreSQL)

Always back up before upgrading. `0002` is forward-only, and `0003`/`0004` are additive, but a restore is the only rollback we support.

```bash
# backup (custom format, compressed)
pg_dump -Fc -h localhost -U hotelbot -d hotelbot -f hotelbot-$(date +%F-%H%M).dump

# restore into an empty database
createdb -h localhost -U hotelbot hotelbot_restore
pg_restore -h localhost -U hotelbot -d hotelbot_restore --no-owner hotelbot-YYYY-MM-DD-HHMM.dump

# verify
psql -h localhost -U hotelbot -d hotelbot_restore -c "select version_num from alembic_version"
```

Roll back an upgrade:

1. Stop the API and workers.
2. Restore the pre-upgrade dump into a new database.
3. Point `HOTELBOT_DATABASE_URL` at it.
4. Deploy the previous release.

Jobs enqueued after the backup are lost with the rollback. Check `GET /api/staff/transactions` against the providers' records for bookings made in between.

Docker Compose:

```bash
docker compose exec db pg_dump -Fc -U hotelbot hotelbot > hotelbot.dump
docker compose exec -T db pg_restore -U hotelbot -d hotelbot --clean --no-owner < hotelbot.dump
```

## Inventory holds

Open quotes hold stock until they expire (15 minutes by default), so no sweeper job is needed. To see what is held, inspect `inventory_holds` (status `held` + `expires_at`, `confirmed`, `released`). Re-ingesting a region pack replaces slots (capacity) but never touches holds (bookings).

## Region data

Region packs (`HOTELBOT_REGION_PACK_PATHS`) are re-ingested idempotently on startup. Slots keep their held capacity across re-ingest. Every place and event shows a freshness caveat once its `last_verified_at` is old (dynamic data goes stale after 30 days, static after 365). The bundled `zabljak_demo.yaml` is **synthetic** and must be replaced with verified data before real use.
