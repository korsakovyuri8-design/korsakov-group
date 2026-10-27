import { render, screen } from "@testing-library/react";
import {
  afterEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";

import {
  configuredRevenueOffers,
  RevenuePanel,
} from "./RevenuePanel";

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("RevenuePanel", () => {
  it("renders nothing until a real configured URL exists", () => {
    const { container } = render(<RevenuePanel />);

    expect(container).toBeEmptyDOMElement();
    expect(configuredRevenueOffers()).toEqual([]);
  });

  it("renders configured affiliate links with disclosure and safe external attributes", () => {
    vi.stubEnv(
      "VITE_ATLAS_AFFILIATE_KIWITAXI_URL",
      "https://kiwitaxi.com/example-ref",
    );
    vi.stubEnv(
      "VITE_ATLAS_AFFILIATE_VIATOR_URL",
      "https://www.viator.com/example-ref",
    );

    render(<RevenuePanel />);

    const transfer = screen.getByRole("link", {
      name: /check transfer prices/i,
    });
    const experiences = screen.getByRole("link", {
      name: /find experiences/i,
    });

    expect(transfer).toHaveAttribute(
      "href",
      "https://kiwitaxi.com/example-ref",
    );
    expect(experiences).toHaveAttribute(
      "href",
      "https://www.viator.com/example-ref",
    );

    for (const link of [transfer, experiences]) {
      expect(link).toHaveAttribute("target", "_blank");
      expect(link.getAttribute("rel")).toContain("noopener");
      expect(link.getAttribute("rel")).toContain("noreferrer");
      expect(link.getAttribute("rel")).toContain("sponsored");
    }

    expect(
      screen.getByText(/Atlas may earn a commission/i),
    ).toBeInTheDocument();
  });

  it("falls back to the server runtime environment for SSR", () => {
    const key = "VITE_ATLAS_AFFILIATE_KIWITAXI_URL";
    const previous = process.env[key];

    process.env[key] = "https://kiwitaxi.com/runtime-ref";

    try {
      const offer = configuredRevenueOffers().find(
        (item) => item.id === "kiwitaxi",
      );

      expect(offer?.url).toBe(
        "https://kiwitaxi.com/runtime-ref",
      );
    } finally {
      if (previous === undefined) {
        delete process.env[key];
      } else {
        process.env[key] = previous;
      }
    }
  });

  it("ignores non-HTTPS and malformed URLs", () => {
    vi.stubEnv(
      "VITE_ATLAS_AFFILIATE_KIWITAXI_URL",
      "javascript:alert(1)",
    );
    vi.stubEnv(
      "VITE_ATLAS_AFFILIATE_DISCOVERCARS_URL",
      "http://example.com/not-secure",
    );
    vi.stubEnv(
      "VITE_ATLAS_AFFILIATE_VIATOR_URL",
      "not-a-url",
    );
    vi.stubEnv(
      "VITE_ATLAS_CONCIERGE_URL",
      "https://pay.example.com/atlas",
    );

    render(<RevenuePanel />);

    expect(
      screen.queryByRole("link", {
        name: /check transfer prices/i,
      }),
    ).not.toBeInTheDocument();

    expect(
      screen.queryByRole("link", {
        name: /compare rental cars/i,
      }),
    ).not.toBeInTheDocument();

    expect(
      screen.queryByRole("link", {
        name: /find experiences/i,
      }),
    ).not.toBeInTheDocument();

    const concierge = screen.getByRole("link", {
      name: /ask atlas concierge/i,
    });

    expect(concierge).toHaveAttribute(
      "href",
      "https://pay.example.com/atlas",
    );
    expect(concierge.getAttribute("rel")).not.toContain("sponsored");

    expect(
      screen.queryByText(/Atlas may earn a commission/i),
    ).not.toBeInTheDocument();
  });
});
