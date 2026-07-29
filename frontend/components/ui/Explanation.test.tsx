import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { Explanation } from "@/components/ui/Explanation";

describe("Explanation", () => {
  it("dismisses an open explanation with Escape and restores trigger focus", async () => {
    const user = userEvent.setup();
    render(<Explanation label="Explain telemetry">Telemetry details</Explanation>);

    const trigger = screen.getByRole("button", { name: "Explain telemetry" });
    await user.click(trigger);
    expect(screen.getByRole("note")).toBeVisible();

    await user.keyboard("{Escape}");

    expect(screen.queryByRole("note")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it("dismisses an open explanation after an outside click", async () => {
    const user = userEvent.setup();
    render(
      <div>
        <Explanation label="Explain telemetry">
          <section>
            <h2>Telemetry details</h2>
          </section>
        </Explanation>
        <button type="button">Outside</button>
      </div>,
    );

    await user.click(screen.getByRole("button", { name: "Explain telemetry" }));
    expect(screen.getByRole("note")).toBeVisible();

    await user.click(screen.getByRole("button", { name: "Outside" }));

    expect(screen.queryByRole("note")).not.toBeInTheDocument();
  });
});
