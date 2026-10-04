import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { useDialogAction } from "./useDialogAction";

function Probe({ work, onClose }: { work: () => Promise<unknown>; onClose: () => void }) {
  const action = useDialogAction(onClose);
  return (
    <>
      <button type="button" onClick={() => void action.submit(work)}>
        go
      </button>
      <button type="button" onClick={action.close}>
        dismiss
      </button>
      <p>{action.busy ? "busy" : "idle"}</p>
      <p>{action.error instanceof Error ? action.error.message : "no error"}</p>
    </>
  );
}

function deferred() {
  let resolve!: () => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<void>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe("useDialogAction", () => {
  it("is busy while the work runs, then closes once on success", async () => {
    const gate = deferred();
    const onClose = vi.fn();
    render(<Probe work={() => gate.promise} onClose={onClose} />);
    await userEvent.click(screen.getByRole("button", { name: "go" }));
    expect(screen.getByText("busy")).toBeInTheDocument();
    await act(async () => gate.resolve());
    expect(screen.getByText("idle")).toBeInTheDocument();
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("keeps the dialog open and holds the error when the work fails", async () => {
    const onClose = vi.fn();
    render(<Probe work={() => Promise.reject(new Error("refused"))} onClose={onClose} />);
    await userEvent.click(screen.getByRole("button", { name: "go" }));
    expect(await screen.findByText("refused")).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
  });

  it("ignores a second submit while one runs", async () => {
    const gate = deferred();
    const work = vi.fn(() => gate.promise);
    render(<Probe work={work} onClose={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: "go" }));
    await userEvent.click(screen.getByRole("button", { name: "go" }));
    expect(work).toHaveBeenCalledTimes(1);
    await act(async () => gate.resolve());
  });

  it("does not close again when the work finishes after the dialog was dismissed", async () => {
    const gate = deferred();
    const onClose = vi.fn();
    render(<Probe work={() => gate.promise} onClose={onClose} />);
    await userEvent.click(screen.getByRole("button", { name: "go" }));
    await userEvent.click(screen.getByRole("button", { name: "dismiss" }));
    expect(onClose).toHaveBeenCalledTimes(1);
    await act(async () => gate.resolve());
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("does not close after unmount", async () => {
    const gate = deferred();
    const onClose = vi.fn();
    const { unmount } = render(<Probe work={() => gate.promise} onClose={onClose} />);
    await userEvent.click(screen.getByRole("button", { name: "go" }));
    unmount();
    await act(async () => gate.resolve());
    expect(onClose).not.toHaveBeenCalled();
  });
});
