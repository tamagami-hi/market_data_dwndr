import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test } from "vitest";

import { ResponsiveDisclosure } from "@/components/ui/ResponsiveDisclosure";

test("keeps summary values visible and exposes details accessibly", async () => {
  const user = userEvent.setup();
  render(
    <ResponsiveDisclosure
      id="nifty-health"
      label="NIFTY stream details"
      summary={<span>NIFTY 0.00% loss</span>}
    >
      <span>Projected EOD 48 MB</span>
    </ResponsiveDisclosure>,
  );

  const button = screen.getByRole("button", { name: "NIFTY stream details" });
  expect(screen.getByText("NIFTY 0.00% loss")).toBeVisible();
  expect(button).toHaveAttribute("aria-expanded", "false");

  await user.click(button);
  expect(button).toHaveAttribute("aria-expanded", "true");
  expect(screen.getByText("Projected EOD 48 MB")).toBeVisible();
});
