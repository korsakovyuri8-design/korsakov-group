// Production handoff for atlas.korsakovgroup.com on server1.
// Lovable's wrapper keeps the TanStack/Vite/Tailwind setup, while Nitro is
// pinned to a self-hosted Node server output for systemd + nginx deployment.
import { defineConfig } from "@lovable.dev/vite-tanstack-config";

export default defineConfig({
  tanstackStart: {
    server: { entry: "server" },
  },
  nitro: {
    preset: "node-server",
  },
});
