# Korsakov Atlas frontend — server1 deployment

This handoff keeps Atlas API on `127.0.0.1:8091` and runs the TanStack Start frontend on `127.0.0.1:8092`.

## Build

This copy pins Nitro to `node-server`, so `npm run build` emits a self-contained `.output/` tree and `npm start` launches `.output/server/index.mjs`.

Recommended runtime: Node.js 22.

```bash
npm install
npm test
npm run build
HOST=127.0.0.1 PORT=8092 npm start
```

Verify locally:

```bash
curl -I http://127.0.0.1:8092/
curl -I http://127.0.0.1:8092/trip
curl -I http://127.0.0.1:8092/demo
```

## systemd

Copy `deploy/atlas-frontend.service` to `/etc/systemd/system/atlas-frontend.service`, create the `atlasfrontend` system user, chown `/opt/atlas-frontend`, then enable the service.

## nginx

Merge the API/frontend routing from `deploy/nginx-atlas.conf` into the existing HTTPS vhost. Keep the Certbot-managed TLS lines from the current production config.

Important routing:
- `/demo` -> frontend
- `/demo/sample` -> FastAPI
- `/demo/import` -> FastAPI
- `/demo/trip/*` -> FastAPI
- `/demo/reset` -> FastAPI
- `/health` -> FastAPI
- everything else -> frontend

## Provider

No environment variables are required in production. `ApiAtlasProvider` is selected by production build and calls relative same-origin `/demo/*` API URLs.
