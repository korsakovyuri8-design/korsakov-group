import { render, screen } from "@testing-library/react";
import {
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { describe, expect, it } from "vitest";

import { PlanningNeeds } from "./PlanningNeeds";

describe("PlanningNeeds unresolved origin", () => {
  it("asks the traveller to correct the origin", () => {
    const client = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });

    render(
      <QueryClientProvider client={client}>
        <PlanningNeeds
          needs={[
            {
              id: "origin",
              planKey: "plan|test",
              code: "arrival_origin_unresolved",
              category: "clarification",
              state: "needs_confirmation",
            },
          ]}
        />
      </QueryClientProvider>,
    );

    expect(
      screen.getByText("Atlas can't match your origin yet"),
    ).toBeTruthy();

    expect(
      screen.getByPlaceholderText("e.g. Podgorica"),
    ).toBeTruthy();

    expect(
      screen.getByRole("button", { name: "Update" }),
    ).toBeTruthy();
  });
});
