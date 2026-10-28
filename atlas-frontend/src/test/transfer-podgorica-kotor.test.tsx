import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { TransferPage, loadTransferRoute } from "@/routes/transfer.$slug";

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("Podgorica to Kotor landing", () => {
  it("exposes the configured Kiwitaxi partner handoff", () => {
    vi.stubEnv(
      "VITE_ATLAS_AFFILIATE_KIWITAXI_URL",
      "https://kiwitaxi.com/en?pap=test123",
    );

    render(<TransferPage route={loadTransferRoute("podgorica-kotor")} />);

    expect(
      screen.getByRole("heading", {
        name: /Podgorica.*Kotor/i,
      }),
    ).toBeInTheDocument();

    const link = screen.getByRole("link", {
      name: /check live transfer prices/i,
    });

    expect(link).toHaveAttribute(
      "href",
      "https://kiwitaxi.com/en?pap=test123",
    );

    expect(
      screen.getByText(/external partner/i),
    ).toBeInTheDocument();
  });
});
