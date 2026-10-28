//
// Production handoff for atlas.korsakovgroup.com on server1.
//
// @lovable.dev/vite-tanstack-config owns the TanStack/Vite/Tailwind
// integration. Native Vite options are passed through `vite`.
//
import { defineConfig } from "@lovable.dev/vite-tanstack-config";

const revenueBuildConfig = {
  VITE_ATLAS_AFFILIATE_KIWITAXI_URL:
    process.env["VITE_ATLAS_AFFILIATE_KIWITAXI_URL"] ?? "",
  VITE_ATLAS_AFFILIATE_DISCOVERCARS_URL:
    process.env["VITE_ATLAS_AFFILIATE_DISCOVERCARS_URL"] ?? "",
  VITE_ATLAS_AFFILIATE_VIATOR_URL:
    process.env["VITE_ATLAS_AFFILIATE_VIATOR_URL"] ?? "",
  VITE_ATLAS_CONCIERGE_URL:
    process.env["VITE_ATLAS_CONCIERGE_URL"] ?? "",
};

export default defineConfig({
  vite: {
    define: {
      __ATLAS_REVENUE__: JSON.stringify(revenueBuildConfig),
    },
  },
  tanstackStart: {
    server: { entry: "server" },
  },
  nitro: {
    preset: "node-server",
  },
});
