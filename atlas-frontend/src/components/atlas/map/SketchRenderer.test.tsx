import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { SketchRenderer } from "./SketchRenderer";

describe("SketchRenderer map evidence boundary", () => {
  it("does not imply Boka Kotorska when Atlas has no resolved map evidence", () => {
    render(<SketchRenderer places={[]} />);

    expect(screen.getByText("Location not resolved yet.")).toBeTruthy();
    expect(screen.queryByText("Boka Kotorska")).toBeNull();
    expect(screen.queryByText("Illustrative map")).toBeNull();
  });

  it("keeps the illustrative demo map when sketch evidence exists", () => {
    render(
      <SketchRenderer
        places={[
          {
            id: "hotel",
            label: "Hotel Demo Kotor",
            role: "stay",
            sketch: { x: 50, y: 50 },
          },
        ]}
      />,
    );

    expect(screen.getByText("Boka Kotorska")).toBeTruthy();
    expect(screen.getByText("Illustrative map")).toBeTruthy();
  });
});
