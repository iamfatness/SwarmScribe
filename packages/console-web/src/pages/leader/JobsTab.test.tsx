import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { JobOut } from "../../api/types";
import { fail, reply } from "../../test/fetchMock";
import { leader } from "../../test/fixtures";
import { renderApp } from "../../test/renderApp";

const JOB_FAILED: JobOut = {
  id: "11111111-1111-4111-8111-111111111111",
  state: "failed",
  location: "intake",
  key: "incoming/a.wav",
  priority: 0,
  attempts: 3,
  max_attempts: 3,
  pool: "default",
  leased_by: null,
  failure_reason: "the engine stopped",
  cancelled_by: null,
  no_speech: null,
  created_at: "2026-10-04T11:00:00Z",
  completed_at: null,
};
const JOB_QUEUED: JobOut = {
  ...JOB_FAILED,
  id: "22222222-2222-4222-8222-222222222222",
  state: "queued",
  attempts: 0,
  failure_reason: null,
};
const JOBS = "GET /api/leaders/eu-1/jobs?limit=100";
const PRIORITY = `POST /api/leaders/eu-1/jobs/${JOB_QUEUED.id}/priority`;
const CANCEL = `POST /api/leaders/eu-1/jobs/${JOB_QUEUED.id}/cancel`;

describe("jobs tab", () => {
  it("filters jobs by state through the address and the leader query", async () => {
    const mock = renderApp("/leaders/eu-1/jobs")
      .on(JOBS, reply(200, [JOB_FAILED, JOB_QUEUED]))
      .on("GET /api/leaders/eu-1/jobs?state=failed&limit=100", reply(200, [JOB_FAILED]));
    await screen.findByRole("rowheader", { name: "22222222" });
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "State" }), "failed");
    await waitFor(() =>
      expect(screen.queryByRole("rowheader", { name: "22222222" })).not.toBeInTheDocument(),
    );
    expect(window.location.search).toBe("?state=failed");
    expect(mock.callsTo("GET /api/leaders/eu-1/jobs?state=failed&limit=100")).toHaveLength(1);
  });

  it("retries a failed job and cancels a queued one after confirming", async () => {
    const mock = renderApp("/leaders/eu-1/jobs")
      .on(JOBS, reply(200, [JOB_FAILED, JOB_QUEUED]))
      .on(
        `POST /api/leaders/eu-1/jobs/${JOB_FAILED.id}/retry`,
        reply(200, { ...JOB_FAILED, state: "queued" }),
      )
      .on(CANCEL, reply(200, { ...JOB_QUEUED, state: "cancelled" }));
    await userEvent.click(await screen.findByRole("button", { name: "Retry job 11111111" }));
    expect(await screen.findByText("Job 11111111 is queued again.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Cancel job 22222222" }));
    const dialog = screen.getByRole("alertdialog", { name: "Cancel job 22222222?" });
    expect(mock.callsTo(CANCEL)).toHaveLength(0);
    await userEvent.click(within(dialog).getByRole("button", { name: "Cancel job" }));
    expect(await screen.findByText("Job 22222222 is cancelled.")).toBeInTheDocument();
    expect(mock.callsTo(CANCEL)).toHaveLength(1);
  });

  it("offers retry on failed and cancelled jobs, the rest on open ones only", async () => {
    const cancelled = {
      ...JOB_FAILED,
      id: "33333333-3333-4333-8333-333333333333",
      state: "cancelled",
    };
    const leased = { ...JOB_QUEUED, id: "44444444-4444-4444-8444-444444444444", state: "leased" };
    const done = { ...JOB_FAILED, id: "55555555-5555-4555-8555-555555555555", state: "completed" };
    renderApp("/leaders/eu-1/jobs").on(JOBS, reply(200, [JOB_FAILED, cancelled, leased, done]));
    await screen.findByRole("rowheader", { name: "55555555" });
    const names = screen.getAllByRole("button").map((b) => b.getAttribute("aria-label"));
    expect(names).toContain("Retry job 11111111");
    expect(names).toContain("Retry job 33333333");
    expect(names).toContain("Cancel job 44444444");
    expect(names).toContain("Priority of job 44444444");
    expect(names.filter((n) => n?.includes("55555555"))).toEqual([]);
    expect(names).not.toContain("Cancel job 11111111");
    expect(names).not.toContain("Priority of job 11111111");
    expect(names).not.toContain("Retry job 44444444");
  });

  it("sets a job's priority as an integer", async () => {
    const mock = renderApp("/leaders/eu-1/jobs")
      .on(JOBS, reply(200, [JOB_QUEUED]))
      .on(PRIORITY, reply(200, { ...JOB_QUEUED, priority: 5 }));
    await userEvent.click(await screen.findByRole("button", { name: "Priority of job 22222222" }));
    const input = screen.getByRole("spinbutton", { name: /Priority/ });
    await userEvent.clear(input);
    await userEvent.type(input, "5");
    await userEvent.click(screen.getByRole("button", { name: "Set priority" }));
    await waitFor(() => expect(mock.callsTo(PRIORITY)[0]?.body).toEqual({ priority: 5 }));
    expect(await screen.findByText("Priority of job 22222222 is set.")).toBeInTheDocument();
  });

  it.each(["1001", "-1001", "1.5", ""])(
    "refuses priority %j in the dialog without calling the leader",
    async (typed) => {
      const mock = renderApp("/leaders/eu-1/jobs")
        .on(JOBS, reply(200, [JOB_QUEUED]))
        .on(PRIORITY, reply(200, JOB_QUEUED));
      const opener = await screen.findByRole("button", { name: "Priority of job 22222222" });
      await userEvent.click(opener);
      const dialog = screen.getByRole("dialog", { name: "Priority of job 22222222" });
      const input = within(dialog).getByRole("spinbutton", { name: /Priority/ });
      await userEvent.clear(input);
      if (typed !== "") await userEvent.type(input, typed);
      await userEvent.click(within(dialog).getByRole("button", { name: "Set priority" }));
      expect(await within(dialog).findByRole("alert")).toHaveTextContent(
        "Enter a whole number from -1000 to 1000.",
      );
      expect(input).toHaveAttribute("aria-invalid", "true");
      expect(mock.callsTo(PRIORITY)).toHaveLength(0);
    },
  );

  it("accepts the ends of the priority range", async () => {
    const mock = renderApp("/leaders/eu-1/jobs")
      .on(JOBS, reply(200, [JOB_QUEUED]))
      .on(PRIORITY, reply(200, JOB_QUEUED));
    await userEvent.click(await screen.findByRole("button", { name: "Priority of job 22222222" }));
    const input = screen.getByRole("spinbutton", { name: /Priority/ });
    await userEvent.clear(input);
    await userEvent.type(input, "-1000");
    await userEvent.click(screen.getByRole("button", { name: "Set priority" }));
    await waitFor(() => expect(mock.callsTo(PRIORITY)[0]?.body).toEqual({ priority: -1000 }));
  });

  it("announces the leader's refusal of a priority in the dialog and keeps it open", async () => {
    renderApp("/leaders/eu-1/jobs")
      .on(JOBS, reply(200, [JOB_QUEUED]))
      .on(PRIORITY, fail(409, "not_open", "the job is no longer queued or leased"));
    await userEvent.click(await screen.findByRole("button", { name: "Priority of job 22222222" }));
    const dialog = screen.getByRole("dialog", { name: "Priority of job 22222222" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Set priority" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Only jobs that are waiting or being worked on can be changed.",
    );
    expect(screen.getByRole("dialog", { name: "Priority of job 22222222" })).toBeInTheDocument();
  });

  it("disables actions above the person's role and names the role", async () => {
    renderApp("/leaders/eu-1/jobs", { fleet: [leader({ role: "viewer" })] }).on(
      JOBS,
      reply(200, [JOB_FAILED]),
    );
    const retry = await screen.findByRole("button", { name: "Retry job 11111111" });
    expect(retry).toBeDisabled();
    expect(retry).toHaveAccessibleDescription("needs operator");
  });

  it("says so when no jobs exist, and when a filter matches none", async () => {
    renderApp("/leaders/eu-1/jobs")
      .on(JOBS, reply(200, []))
      .on("GET /api/leaders/eu-1/jobs?state=failed&limit=100", reply(200, []));
    expect(await screen.findByText("This leader has no jobs.")).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "State" }), "failed");
    expect(await screen.findByText("No jobs match this filter.")).toBeInTheDocument();
  });

  it("shows a long recording path whole", async () => {
    const key = `incoming/${"very-long-folder-name/".repeat(8)}recording-with-a-very-long-name.wav`;
    renderApp("/leaders/eu-1/jobs").on(JOBS, reply(200, [{ ...JOB_FAILED, key }]));
    expect(await screen.findByText(key)).toBeInTheDocument();
  });

  it("shows an unreachable leader's error in the tab and keeps the page", async () => {
    renderApp("/leaders/eu-1/jobs", {
      fleet: [leader({ health: "unreachable", consecutive_failures: 3 })],
    }).on(JOBS, {
      status: 503,
      body: { code: "leader_unreachable", message: "leader eu-1 cannot be reached; try again" },
      headers: { "Retry-After": "15" },
    });
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The leader is not answering right now.",
    );
    expect(screen.getByText(/eu-1 is not answering, so nothing here can be read or changed/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Pools and followers" })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "State" })).toBeEnabled();
  });

  it("passes the leader's own refusal through", async () => {
    renderApp("/leaders/eu-1/jobs")
      .on(JOBS, reply(200, [JOB_FAILED]))
      .on(
        `POST /api/leaders/eu-1/jobs/${JOB_FAILED.id}/retry`,
        fail(409, "not_retryable", "only failed or cancelled jobs can be retried"),
      );
    await userEvent.click(await screen.findByRole("button", { name: "Retry job 11111111" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Only failed or cancelled jobs can be tried again.",
    );
  });

  it("keeps the cancel dialog open with the refusal when the leader refuses", async () => {
    renderApp("/leaders/eu-1/jobs")
      .on(JOBS, reply(200, [JOB_QUEUED]))
      .on(CANCEL, fail(409, "not_open", "the job is no longer queued or leased"));
    await userEvent.click(await screen.findByRole("button", { name: "Cancel job 22222222" }));
    const dialog = screen.getByRole("alertdialog", { name: "Cancel job 22222222?" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Cancel job" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Only jobs that are waiting or being worked on can be changed.",
    );
  });

  it("keeps a newer priority dialog open when an earlier one finishes late", async () => {
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const other = { ...JOB_QUEUED, id: "66666666-6666-4666-8666-666666666666" };
    const mock = renderApp("/leaders/eu-1/jobs")
      .on(JOBS, reply(200, [JOB_QUEUED, other]))
      .on(PRIORITY, async () => {
        await gate;
        return reply(200, { ...JOB_QUEUED, priority: 7 });
      });
    await userEvent.click(await screen.findByRole("button", { name: "Priority of job 22222222" }));
    const first = screen.getByRole("dialog", { name: "Priority of job 22222222" });
    await userEvent.click(within(first).getByRole("button", { name: "Set priority" }));
    await waitFor(() => expect(mock.callsTo(PRIORITY)).toHaveLength(1));
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Priority of job 66666666" }));
    expect(screen.getByRole("dialog", { name: "Priority of job 66666666" })).toBeInTheDocument();
    release();
    expect(await screen.findByText("Priority of job 22222222 is set.")).toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "Priority of job 66666666" })).toBeInTheDocument();
  });

  it("does not reload when the tab becomes visible", async () => {
    const mock = renderApp("/leaders/eu-1/jobs").on(JOBS, reply(200, [JOB_QUEUED]));
    await screen.findByRole("rowheader", { name: "22222222" });
    document.dispatchEvent(new Event("visibilitychange"));
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(mock.callsTo(JOBS)).toHaveLength(1);
  });
});
