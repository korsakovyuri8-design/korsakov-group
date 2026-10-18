# Korsakov Atlas frontend — server1 deployment

Atlas API: 127.0.0.1:8091
Frontend: 127.0.0.1:8092

## Production layout

Live frontend releases are stored under:

/opt/atlas-frontend/releases/

The active release is selected by:

/opt/atlas-frontend/current

systemd runs:

/opt/atlas-frontend/current/.output/server/index.mjs

Frontend source of truth:

/opt/atlas/atlas-frontend

## Deployment

Never build directly in /opt/atlas-frontend/.output.

Use:

/opt/atlas/atlas-frontend/deploy/deploy-atomic.sh

The deploy script:
- requires a clean Git frontend tree
- creates a new immutable release
- installs locked dependencies
- runs tests
- builds outside the live release
- smoke-tests the new release on a temporary port
- preserves previous hashed assets
- atomically switches current
- restarts atlas-frontend
- runs local and public smoke tests
- rolls back automatically on failure
- retains recent releases for rollback

## Verification

Check:

systemctl is-active atlas-frontend

curl -I http://127.0.0.1:8092/

curl -I https://atlas.korsakovgroup.com/

The process working directory must resolve under:

/opt/atlas-frontend/releases/

## nginx routing

/demo -> frontend
/demo/sample -> FastAPI
/demo/import -> FastAPI
/demo/trip/* -> FastAPI
/demo/reset -> FastAPI
/demo/plan/* -> FastAPI
/health -> FastAPI
everything else -> frontend

## Rollback

deploy-atomic.sh automatically rolls back after a failed post-switch smoke test.

For manual rollback, atomically repoint /opt/atlas-frontend/current to a previous release and restart atlas-frontend.
