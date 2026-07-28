import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { expect, test } from "vitest";

import { Dialog } from "@/components/ui/Dialog";

function Harness() {
  const [isOpen, setIsOpen] = useState(false);
  return (
    <>
      <button onClick={() => setIsOpen(true)}>Open logs</button>
      <Dialog isOpen={isOpen} title="Session logs" onOpenChange={setIsOpen}>
        <p>Captured log line</p>
      </Dialog>
    </>
  );
}

test("opens a named native dialog and restores focus after close", async () => {
  const user = userEvent.setup();
  render(<Harness />);
  const trigger = screen.getByRole("button", { name: "Open logs" });

  await user.click(trigger);
  const dialog = screen.getByRole("dialog", { name: "Session logs" });
  expect(dialog).toBeVisible();
  expect(screen.getByRole("button", { name: "Close Session logs" })).toHaveFocus();
  expect(document.body).toHaveStyle({ overflow: "hidden" });

  await user.click(screen.getByRole("button", { name: "Close Session logs" }));
  expect(trigger).toHaveFocus();
  expect(document.body.style.overflow).toBe("");
});
