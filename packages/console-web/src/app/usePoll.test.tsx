import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import { IDLE_AFTER_MS, noteActivity, setLastInputForTests } from "./activity";
import { usePoll } from "./usePoll";

function Probe({
  load,
  interval,
  id,
}: {
  load: (s: AbortSignal) => Promise<string>;
  interval: number | null;
  id: string;
}) {
  const { data, error, loading } = usePoll(load, interval, id);
  return (
    <p>
      {loading ? "loading" : "ready"}|{data ?? "-"}|{error instanceof ApiError ? error.code : "-"}
    </p>
  );
}

async function flush() {
  await act(async () => {
    await Promise.resolve();
  });
}

describe("usePoll", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    setLastInputForTests(Date.now());
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("loads at once and again every interval", async () => {
    let n = 0;
    const load = vi.fn(async () => `v${++n}`);
    render(<Probe load={load} interval={10_000} id="a" />);
    await flush();
    expect(screen.getByText("ready|v1|-")).toBeInTheDocument();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(screen.getByText("ready|v2|-")).toBeInTheDocument();
    expect(load).toHaveBeenCalledTimes(2);
  });

  it("keeps the last good data when a load fails", async () => {
    let n = 0;
    const load = vi.fn(async () => {
      n += 1;
      if (n === 2) throw new ApiError(503, "unavailable", "down");
      return `v${n}`;
    });
    render(<Probe load={load} interval={10_000} id="a" />);
    await flush();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(screen.getByText("ready|v1|unavailable")).toBeInTheDocument();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(screen.getByText("ready|v3|-")).toBeInTheDocument();
  });

  it("stops loading while the person is idle and resumes on input", async () => {
    const load = vi.fn(async () => "v");
    render(<Probe load={load} interval={10_000} id="a" />);
    await flush();
    expect(load).toHaveBeenCalledTimes(1);
    setLastInputForTests(Date.now() - IDLE_AFTER_MS);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });
    expect(load).toHaveBeenCalledTimes(1);
    await act(async () => {
      noteActivity();
      await Promise.resolve();
    });
    expect(load).toHaveBeenCalledTimes(2);
  });

  it("drops the old key's data when the key changes", async () => {
    const load = vi.fn(async () => "v");
    const never = vi.fn(() => new Promise<string>(() => undefined));
    const { rerender } = render(<Probe load={load} interval={null} id="a" />);
    await flush();
    expect(screen.getByText("ready|v|-")).toBeInTheDocument();
    rerender(<Probe load={never} interval={null} id="b" />);
    expect(screen.getByText("loading|-|-")).toBeInTheDocument();
  });
});
