import { useState } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import { ActionButton } from "./ActionButton";
import { ConfirmDialog } from "./ConfirmDialog";

describe("ActionButton", () => {
  it("is enabled when the role reaches the action", async () => {
    const onClick = vi.fn();
    render(
      <ActionButton held="operator" action="jobs.retry" onClick={onClick}>
        Retry
      </ActionButton>,
    );
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(onClick).toHaveBeenCalledOnce();
    expect(screen.queryByText(/needs/)).not.toBeInTheDocument();
  });

  it("is disabled and names the role needed otherwise", () => {
    render(
      <ActionButton held="operator" action="tokens.create" onClick={vi.fn()}>
        Create join token
      </ActionButton>,
    );
    const button = screen.getByRole("button", { name: "Create join token" });
    expect(button).toBeDisabled();
    expect(button).toHaveAccessibleDescription("needs admin");
  });
});

describe("ConfirmDialog", () => {
  it("runs the action and closes", async () => {
    const onConfirm = vi.fn(async () => undefined);
    const onClose = vi.fn();
    render(
      <ConfirmDialog
        title="Cancel job?"
        message="The job stops."
        confirmLabel="Cancel job"
        onConfirm={onConfirm}
        onClose={onClose}
      />,
    );
    expect(screen.getByRole("dialog", { name: "Cancel job?" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Cancel job" }));
    expect(onConfirm).toHaveBeenCalledOnce();
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("stays open and shows the error when the action fails", async () => {
    const onClose = vi.fn();
    render(
      <ConfirmDialog
        title="Revoke?"
        message="m"
        confirmLabel="Revoke"
        onConfirm={async () => {
          throw new ApiError(503, "leader_unreachable", "leader eu-1 cannot be reached; try again", 15);
        }}
        onClose={onClose}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "Revoke" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The leader cannot be reached right now.");
    expect(onClose).not.toHaveBeenCalled();
  });
});
describe("Dialog accessibility", () => {
  it("labels the dialog, marks it modal and moves focus to Close", () => {
    render(<ConfirmDialog title="Cancel job?" message="m" confirmLabel="Cancel job" onConfirm={vi.fn()} onClose={vi.fn()} />);
    const dialog = screen.getByRole("dialog", { name: "Cancel job?" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(screen.getByRole("button", { name: "Close" })).toHaveFocus();
  });

  it("wraps Tab and Shift+Tab inside the dialog", async () => {
    render(<ConfirmDialog title="T" message="m" confirmLabel="Go" onConfirm={vi.fn()} onClose={vi.fn()} />);
    const close = screen.getByRole("button", { name: "Close" });
    const go = screen.getByRole("button", { name: "Go" });
    await userEvent.tab();
    expect(go).toHaveFocus();
    await userEvent.tab();
    expect(close).toHaveFocus();
    await userEvent.tab({ shift: true });
    expect(go).toHaveFocus();
  });

  it("closes on Escape", async () => {
    const onClose = vi.fn();
    render(<ConfirmDialog title="T" message="m" confirmLabel="Go" onConfirm={vi.fn()} onClose={onClose} />);
    await userEvent.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledOnce();
  });

  function Host({ remove = false }: { remove?: boolean }) {
    const [open, setOpen] = useState(false);
    return (
      <>
        <div id="main" tabIndex={-1}>
          {!(remove && open) && <button onClick={() => setOpen(true)}>Open</button>}
          <button>Other</button>
        </div>
        {open && <ConfirmDialog title="T" message="m" confirmLabel="Go" onConfirm={vi.fn()} onClose={() => setOpen(false)} />}
      </>
    );
  }

  it("makes the background inert while open and restores it and focus after", async () => {
    const { container } = render(<Host />);
    const opener = screen.getByRole("button", { name: "Open" });
    await userEvent.click(opener);
    expect(container).toHaveAttribute("inert");
    expect(container).toHaveAttribute("aria-hidden", "true");
    await userEvent.keyboard("{Escape}");
    expect(container).not.toHaveAttribute("inert");
    expect(container).not.toHaveAttribute("aria-hidden");
    expect(opener).toHaveFocus();
  });

  it("falls back to the main region when the opener is gone", async () => {
    function Gone() {
      const [open, setOpen] = useState(false);
      const [shown, setShown] = useState(true);
      return (
        <>
          <main id="main" tabIndex={-1}>
            {shown && <button onClick={() => setOpen(true)}>Open</button>}
          </main>
          {open && (
            <ConfirmDialog
              title="T"
              message="m"
              confirmLabel="Go"
              onConfirm={async () => setShown(false)}
              onClose={() => setOpen(false)}
            />
          )}
        </>
      );
    }
    render(<Gone />);
    await userEvent.click(screen.getByRole("button", { name: "Open" }));
    await userEvent.click(screen.getByRole("button", { name: "Go" }));
    expect(document.getElementById("main")).toHaveFocus();
  });

  it("stacks a second dialog on top and makes the first inert", async () => {
    render(
      <>
        <ConfirmDialog title="First" message="m" confirmLabel="Go" onConfirm={vi.fn()} onClose={vi.fn()} />
        <ConfirmDialog title="Second" message="m" confirmLabel="Go2" onConfirm={vi.fn()} onClose={vi.fn()} />
      </>,
    );
    const first = document.querySelector("dialog[aria-labelledby]") as HTMLElement;
    expect(first).toHaveAttribute("inert");
    expect(screen.getByRole("dialog", { name: "Second" })).not.toHaveAttribute("inert");
    expect(screen.getAllByRole("button", { name: "Close" }).at(-1)).toHaveFocus();
  });
});

describe("ActionButton keyboard and screen-reader access", () => {
  it("keeps the reason as visible text next to the disabled button", () => {
    render(
      <ActionButton held="viewer" action="jobs.retry" name="Retry job 1a2b" onClick={vi.fn()}>
        Retry
      </ActionButton>,
    );
    const button = screen.getByRole("button", { name: "Retry job 1a2b" });
    expect(button).toBeDisabled();
    expect(button).toHaveAccessibleDescription("needs operator");
    expect(screen.getByText("needs operator")).toBeVisible();
  });
});
