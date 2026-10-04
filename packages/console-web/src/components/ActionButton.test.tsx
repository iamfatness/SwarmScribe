import { useState } from "react";
import { act, render, screen } from "@testing-library/react";
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
    expect(screen.getByRole("alertdialog", { name: "Cancel job?" })).toBeInTheDocument();
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
    const dialog = screen.getByRole("alertdialog", { name: "Cancel job?" });
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
    await userEvent.keyboard("{Escape}");
    expect(container).not.toHaveAttribute("inert");
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
    const first = screen.getByRole("alertdialog", { name: "First", hidden: true });
    expect(first).toHaveAttribute("inert");
    expect(screen.getByRole("alertdialog", { name: "Second" })).not.toHaveAttribute("inert");
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

describe("Dialog description, lifetime and focus", () => {
  it("describes the alert dialog by its message", () => {
    render(<ConfirmDialog title="T" message="This stops the job." confirmLabel="Go" onConfirm={vi.fn()} onClose={vi.fn()} />);
    expect(screen.getByRole("alertdialog", { name: "T" })).toHaveAccessibleDescription("This stops the job.");
  });

  it("a late result from a closed dialog never closes the dialog opened after it", async () => {
    let finish: () => void = () => undefined;
    const slow = () => new Promise<void>((resolve) => (finish = resolve));
    function Host() {
      const [which, setWhich] = useState<"a" | "b" | null>("a");
      return (
        <>
          <button onClick={() => setWhich("b")}>Open B</button>
          {which === "a" && (
            <ConfirmDialog title="A" message="m" confirmLabel="Go A" onConfirm={slow} onClose={() => setWhich(null)} />
          )}
          {which === "b" && (
            <ConfirmDialog title="B" message="m" confirmLabel="Go B" onConfirm={vi.fn()} onClose={() => setWhich(null)} />
          )}
        </>
      );
    }
    render(<Host />);
    await userEvent.click(screen.getByRole("button", { name: "Go A" }));
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Open B" }));
    expect(screen.getByRole("alertdialog", { name: "B" })).toBeInTheDocument();
    await act(async () => {
      finish();
    });
    expect(screen.getByRole("alertdialog", { name: "B" })).toBeInTheDocument();
  });

  it("keeps focus inside the dialog while busy and after a failure", async () => {
    let fail: (reason: unknown) => void = () => undefined;
    const onConfirm = () => new Promise<void>((_, reject) => (fail = reject));
    render(<ConfirmDialog title="T" message="m" confirmLabel="Go" onConfirm={onConfirm} onClose={vi.fn()} />);
    const dialog = screen.getByRole("alertdialog");
    await userEvent.click(screen.getByRole("button", { name: "Go" }));
    expect(screen.getByRole("button", { name: "Working…" })).toHaveAttribute("aria-disabled", "true");
    expect(dialog).toContainElement(document.activeElement as HTMLElement);
    await act(async () => {
      fail(new ApiError(503, "leader_unreachable", "x", 15));
    });
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(dialog).toContainElement(document.activeElement as HTMLElement);
  });

  it("ignores a second click while busy", async () => {
    const onConfirm = vi.fn(() => new Promise<void>(() => undefined));
    render(<ConfirmDialog title="T" message="m" confirmLabel="Go" onConfirm={onConfirm} onClose={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: "Go" }));
    await userEvent.click(screen.getByRole("button", { name: "Working…" }));
    expect(onConfirm).toHaveBeenCalledOnce();
  });

  it("returns focus to the opener, or to main, when unmounted while open", () => {
    const opener = document.createElement("button");
    const main = document.createElement("main");
    main.id = "main";
    main.tabIndex = -1;
    document.body.append(main, opener);
    opener.focus();
    const view = render(<ConfirmDialog title="T" message="m" confirmLabel="Go" onConfirm={vi.fn()} onClose={vi.fn()} />);
    view.unmount();
    expect(opener).toHaveFocus();

    opener.focus();
    const again = render(<ConfirmDialog title="T" message="m" confirmLabel="Go" onConfirm={vi.fn()} onClose={vi.fn()} />);
    opener.remove();
    again.unmount();
    expect(main).toHaveFocus();
    main.remove();
  });

  it("keeps a busy ActionButton focused", () => {
    const { rerender } = render(
      <ActionButton held="operator" action="jobs.retry" onClick={vi.fn()}>
        Retry
      </ActionButton>,
    );
    const button = screen.getByRole("button", { name: "Retry" });
    button.focus();
    rerender(
      <ActionButton held="operator" action="jobs.retry" busy onClick={vi.fn()}>
        Retry
      </ActionButton>,
    );
    expect(button).toHaveFocus();
    expect(button).toHaveAttribute("aria-disabled", "true");
  });
});
