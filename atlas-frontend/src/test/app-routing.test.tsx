import { QueryClient } from "@tanstack/react-query";
import { createMemoryHistory, createRouter, rootRouteId } from "@tanstack/react-router";
import { describe, expect, it, vi } from "vitest";

import { isAtlasPublicRoute } from "@/components/atlas/AppShell";
import { routeTree } from "@/routeTree.gen";

// Match routes without running loaders or rendering: loaders may need a server or
// network the test run lacks, and jsdom never loads the stylesheets React waits on.
describe("App routing", () => {
  it("matches a page for / instead of falling back to not found", () => {
    const router = createRouter({ routeTree, context: { queryClient: new QueryClient() } });

    const matches = router.matchRoutes("/");

    expect(matches.at(-1)?.routeId).not.toBe(rootRouteId);
  });

  it("keeps public marketing routes outside the live session shell", () => {
    expect(isAtlasPublicRoute("/demo")).toBe(true);
    expect(
      isAtlasPublicRoute("/transfer/podgorica-kotor"),
    ).toBe(true);

    expect(isAtlasPublicRoute("/")).toBe(false);
    expect(isAtlasPublicRoute("/trip")).toBe(false);
  });

  it("loads public routes in the browser without requesting private trip data", async () => {
    const queryClient = new QueryClient();
    const privateQuery = vi.spyOn(queryClient, "ensureQueryData");
    const router = createRouter({ routeTree, context: { queryClient }, history: createMemoryHistory({ initialEntries: ["/transfer/podgorica-kotor"] }) });
    await router.load();
    expect(router.state.matches.at(-1)?.status).toBe("success");
    expect(privateQuery).not.toHaveBeenCalled();
    await router.navigate({ to: "/transfer/$slug", params: { slug: "tashkent-airport-samarkand" } });
    expect(privateQuery).not.toHaveBeenCalled();
    queryClient.clear();
  });
});
