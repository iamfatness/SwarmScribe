import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import { resetSessionEndedForTests } from "./navigation";
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
    resetSessionEndedForTests();
  });
  afterEach(() => {
    vi.useRealTimers();
    resetSessionEndedForTests();
    setVisibility("visible");
  });

  function setVisibility(state: "visible" | "hidden") {
    Object.defineProperty(document, "visibilityState", { configurable: true, get: () => state });
    document.dispatchEvent(new Event("visibilitychange"));
  }

  function deferred() {
    let resolve!: (value: string) => void;
    const promise = new Promise<string>((r) => {
      resolve = r;
    });
    return { promise, resolve };
  }

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

  it("does not start a second request when the tab comes back during a slow one", async () => {
    const slow = deferred();
    const load = vi.fn(() => slow.promise);
    render(<Probe load={load} interval={10_000} id="a" />);
    await flush();
    act(() => setVisibility("hidden"));
    act(() => setVisibility("visible"));
    await flush();
    expect(load).toHaveBeenCalledTimes(1);
    slow.resolve("v");
    await flush();
    expect(screen.getByText("ready|v|-")).toBeInTheDocument();
    expect(vi.getTimerCount()).toBe(1);
  });

  it("does not start a second request on an idle resume during a slow one", async () => {
    const slow = deferred();
    const load = vi.fn(() => slow.promise);
    render(<Probe load={load} interval={10_000} id="a" />);
    await flush();
    setLastInputForTests(Date.now() - IDLE_AFTER_MS);
    await act(async () => {
      noteActivity();
      await Promise.resolve();
    });
    expect(load).toHaveBeenCalledTimes(1);
  });

  it("does nothing after unmount: no timers, no further loads, the request is aborted", async () => {
    const slow = deferred();
    let seen: AbortSignal | undefined;
    const load = vi.fn((signal: AbortSignal) => {
      seen = signal;
      return slow.promise;
    });
    const { unmount } = render(<Probe load={load} interval={10_000} id="a" />);
    await flush();
    unmount();
    expect(seen?.aborted).toBe(true);
    slow.resolve("late");
    await flush();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });
    expect(load).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("ignores a response that arrives after the key changed", async () => {
    const slow = deferred();
    const first = vi.fn(() => slow.promise);
    const second = vi.fn(async () => "new");
    const { rerender } = render(<Probe load={first} interval={null} id="a" />);
    await flush();
    rerender(<Probe load={second} interval={null} id="b" />);
    await flush();
    expect(screen.getByText("ready|new|-")).toBeInTheDocument();
    slow.resolve("old");
    await flush();
    expect(screen.getByText("ready|new|-")).toBeInTheDocument();
  });

  it("stops for good after a 401", async () => {
    const load = vi.fn(async () => {
      throw new ApiError(401, "unauthenticated", "no session");
    });
    render(<Probe load={load} interval={10_000} id="a" />);
    await flush();
    expect(screen.getByText("ready|-|unauthenticated")).toBeInTheDocument();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });
    expect(load).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("does not poll once the session has ended elsewhere", async () => {
    const load = vi.fn(async () => "v");
    render(<Probe load={load} interval={10_000} id="a" />);
    await flush();
    const { goToSignIn } = await import("./navigation");
    vi.stubGlobal("location", { pathname: "/sign-in", search: "", assign: vi.fn() });
    goToSignIn();
    vi.unstubAllGlobals();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });
    expect(load).toHaveBeenCalledTimes(1);
  });
});
