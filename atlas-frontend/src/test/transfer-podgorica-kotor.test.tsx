import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { PodgoricaKotorTransferPage } from "@/routes/transfer.podgorica-kotor";

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("Podgorica to Kotor landing", () => {
  it("exposes the configured Kiwitaxi partner handoff", () => {
    vi.stubEnv(
      "VITE_ATLAS_AFFILIATE_KIWITAXI_URL",
      "https://kiwitaxi.com/en?pap=test123",
    );

    render(<PodgoricaKotorTransferPage />);

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
