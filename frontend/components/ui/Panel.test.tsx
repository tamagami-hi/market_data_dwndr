import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";

import { Panel } from "@/components/ui/Panel";

test("keeps the panel title and subtitle on one compact title line", () => {
  render(
    <Panel title="Frame integrity" subtitle="baseline 23,520 frames / session">
      content
    </Panel>,
  );

  const title = screen.getByRole("heading", { name: "Frame integrity" });
  expect(title.parentElement).toHaveClass("panel-title-line");
  expect(title.parentElement).toHaveTextContent(
    "Frame integritybaseline 23,520 frames / session",
  );
});
