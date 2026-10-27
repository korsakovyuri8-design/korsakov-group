import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { TransportAffiliateCta } from "./TransportAffiliateCta";

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("TransportAffiliateCta", () => {
  it("renders the configured Kiwitaxi handoff", () => {
    vi.stubEnv(
      "VITE_ATLAS_AFFILIATE_KIWITAXI_URL",
      "https://kiwitaxi.com/en?pap=test123",
    );

    render(<TransportAffiliateCta />);

    const link = screen.getByRole("link", {
      name: /check live transfer prices/i,
    });

    expect(link).toHaveAttribute(
      "href",
      "https://kiwitaxi.com/en?pap=test123",
    );
    expect(link.getAttribute("rel")).toContain("sponsored");
    expect(
      screen.getByText(/Atlas may earn a commission/i),
    ).toBeInTheDocument();
  });

  it("renders nothing without a configured partner URL", () => {
    const { container } = render(<TransportAffiliateCta />);
    expect(container).toBeEmptyDOMElement();
  });
});
