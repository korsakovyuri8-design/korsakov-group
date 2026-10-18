#!/usr/bin/env bash
set -Eeuo pipefail

BASE=/opt/atlas-frontend
REPO=/opt/atlas
SOURCE="$REPO/atlas-frontend"
SERVICE=atlas-frontend
KEEP_RELEASES=4
SMOKE_PORT=18092

die() {
  echo "ERROR: $*" >&2
  exit 1
}

for cmd in git rsync curl systemctl node; do
  command -v "$cmd" >/dev/null || die "missing command: $cmd"
done

BUN="$(command -v bun || true)"
if [[ -z "$BUN" && -x /root/.bun/bin/bun ]]; then
  BUN=/root/.bun/bin/bun
fi
[[ -n "$BUN" ]] || die "bun not found"

cd "$REPO"

if [[ -n "$(git status --porcelain -- atlas-frontend)" ]]; then
  die "atlas-frontend source tree is dirty; commit or revert it first"
fi

SHA="$(git rev-parse --short=12 HEAD)"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
REL="${STAMP}-${SHA}"
DEST="$BASE/releases/$REL"

OLD_LINK="$(readlink "$BASE/current" || true)"
OLD_REAL="$(readlink -f "$BASE/current" || true)"

[[ -n "$OLD_LINK" ]] || die "current release link is missing"
[[ -d "$OLD_REAL" ]] || die "current release target is missing"

echo "SOURCE_SHA=$SHA"
echo "OLD_RELEASE=$OLD_REAL"
echo "NEW_RELEASE=$DEST"

mkdir -p "$DEST"

cleanup_failed_release() {
  if [[ -d "$DEST" && "$(readlink -f "$BASE/current")" != "$DEST" ]]; then
    rm -rf "$DEST"
  fi
}

trap cleanup_failed_release ERR

rsync -a \
  --exclude='.git/' \
  --exclude='node_modules/' \
  --exclude='.output/' \
  --exclude='dist/' \
  --exclude='.cache/' \
  --exclude='.env' \
  --exclude='.env.*' \
  "$SOURCE/" \
  "$DEST/"

cd "$DEST"

echo "=== INSTALL ==="
"$BUN" install --frozen-lockfile

echo "=== TEST ==="
"$BUN" run test

echo "=== BUILD ==="
"$BUN" run build

test -f "$DEST/.output/server/index.mjs" \
  || die "build produced no server entrypoint"

# Keep old hashed static files available to browsers that loaded the
# previous HTML immediately before deployment.
if [[ -d "$OLD_REAL/.output/public" && -d "$DEST/.output/public" ]]; then
  rsync -a --ignore-existing \
    "$OLD_REAL/.output/public/" \
    "$DEST/.output/public/"
fi

chown -R atlasfrontend:atlasfrontend "$DEST"

# Smoke-test the new release before touching production.
if ss -ltn | grep -q ":${SMOKE_PORT} "; then
  die "smoke port ${SMOKE_PORT} is already in use"
fi

SMOKE_LOG="/tmp/atlas-frontend-${REL}.log"

NODE_ENV=production \
HOST=127.0.0.1 \
PORT="$SMOKE_PORT" \
/usr/bin/node "$DEST/.output/server/index.mjs" \
  >"$SMOKE_LOG" 2>&1 &

SMOKE_PID=$!

smoke_cleanup() {
  kill "$SMOKE_PID" 2>/dev/null || true
  wait "$SMOKE_PID" 2>/dev/null || true
}

SMOKE_OK=0

for _ in $(seq 1 30); do
  if curl -fsS \
    -o /dev/null \
    "http://127.0.0.1:${SMOKE_PORT}/"; then
    SMOKE_OK=1
    break
  fi
  sleep 0.5
done

smoke_cleanup

if [[ "$SMOKE_OK" != 1 ]]; then
  cat "$SMOKE_LOG" >&2 || true
  die "pre-switch smoke test failed"
fi

rm -f "$SMOKE_LOG"

echo "PRE_SWITCH_SMOKE=OK"

# Atomic symlink replacement.
NEXT="$BASE/current.next.$$"
ln -s "releases/$REL" "$NEXT"
mv -Tf "$NEXT" "$BASE/current"

echo "CURRENT=$(readlink "$BASE/current")"

if ! systemctl restart "$SERVICE"; then
  echo "Restart failed; rolling back" >&2
  ROLLBACK="$BASE/current.rollback.$$"
  ln -s "$OLD_LINK" "$ROLLBACK"
  mv -Tf "$ROLLBACK" "$BASE/current"
  systemctl restart "$SERVICE"
  exit 1
fi

LIVE_OK=0

for _ in $(seq 1 20); do
  if systemctl is-active --quiet "$SERVICE" \
    && curl -fsS -o /dev/null http://127.0.0.1:8092/; then
    LIVE_OK=1
    break
  fi
  sleep 0.5
done

if [[ "$LIVE_OK" != 1 ]]; then
  echo "Live smoke failed; rolling back" >&2

  ROLLBACK="$BASE/current.rollback.$$"
  ln -s "$OLD_LINK" "$ROLLBACK"
  mv -Tf "$ROLLBACK" "$BASE/current"

  systemctl restart "$SERVICE"
  sleep 1

  curl -fsS -o /dev/null http://127.0.0.1:8092/ \
    || die "rollback also failed"

  exit 1
fi

curl -fsS -o /dev/null https://atlas.korsakovgroup.com/ \
  || {
    echo "Public smoke failed; rolling back" >&2

    ROLLBACK="$BASE/current.rollback.$$"
    ln -s "$OLD_LINK" "$ROLLBACK"
    mv -Tf "$ROLLBACK" "$BASE/current"

    systemctl restart "$SERVICE"
    exit 1
  }

trap - ERR

echo "LOCAL_SMOKE=OK"
echo "PUBLIC_SMOKE=OK"

# Keep the newest releases plus the currently active one.
mapfile -t ALL_RELEASES < <(
  find "$BASE/releases" \
    -mindepth 1 \
    -maxdepth 1 \
    -type d \
    -printf '%T@ %p\n' \
    | sort -nr \
    | cut -d' ' -f2-
)

ACTIVE="$(readlink -f "$BASE/current")"
kept=0

for release in "${ALL_RELEASES[@]}"; do
  if [[ "$release" == "$ACTIVE" ]]; then
    ((kept+=1))
    continue
  fi

  if (( kept < KEEP_RELEASES )); then
    ((kept+=1))
    continue
  fi

  rm -rf "$release"
done

PID="$(systemctl show "$SERVICE" -p MainPID --value)"

echo "DEPLOYED=$REL"
echo "PID=$PID"
echo "CWD=$(readlink -f "/proc/$PID/cwd")"
echo "DONE"
