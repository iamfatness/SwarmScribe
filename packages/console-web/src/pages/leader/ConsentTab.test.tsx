import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { ConsentReport } from "../../api/types";
import { fail, reply } from "../../test/fetchMock";
import { renderApp } from "../../test/renderApp";

const REPORT = "GET /api/leaders/eu-1/consent/report";

describe("consent tab", () => {
  it("shows counts by location and transcripts to review", async () => {
    const report: ConsentReport = {
      locations: [{ name: "intake", consented: 10, not_consented: 1, withdrawn: 2, missing: 0 }],
      flagged: [
        {
          job_id: "66666666-6666-4666-8666-666666666666",
          location: "intake",
          key: "incoming/b.wav",
          completed_at: "2026-10-04T10:00:00Z",
          output_location: "intake",
          outputs: ["transcripts/b.json"],
        },
      ],
      truncated: true,
    };
    const mock = renderApp("/leaders/eu-1/consent").on(REPORT, reply(200, report));
    const byLocation = await screen.findByRole("region", { name: "Consent by location" });
    expect(within(byLocation).getByRole("row", { name: /intake/ })).toHaveTextContent(
      "intake10120",
    );
    expect(screen.getByText("intake: transcripts/b.json")).toBeInTheDocument();
    // Above the tables, where it is seen first, and below them.
    expect(screen.getAllByText(/The report is cut short/)).toHaveLength(2);
    // Only the report was read, once, with no query the console does not need.
    expect(mock.callsTo(REPORT)).toHaveLength(1);
  });

  it("says so when nothing needs review, and when there are no locations", async () => {
    renderApp("/leaders/eu-1/consent").on(
      REPORT,
      reply(200, { locations: [], flagged: [], truncated: false }),
    );
    expect(
      await screen.findByText(
        "No transcript was made from a recording that is no longer consented.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("This leader has no locations.")).toBeInTheDocument();
    expect(screen.queryByText(/cut short/)).not.toBeInTheDocument();
  });

  it("shows a missing completed time as a dash", async () => {
    renderApp("/leaders/eu-1/consent").on(
      REPORT,
      reply(200, {
        locations: [],
        flagged: [
          {
            job_id: "77777777-7777-4777-8777-777777777777",
            location: "intake",
            key: "a.wav",
            completed_at: null,
            output_location: "out",
            outputs: [],
          },
        ],
        truncated: false,
      }),
    );
    expect(await screen.findByRole("row", { name: /77777777/ })).toHaveTextContent("–");
  });

  it("announces a failed read with a way to retry", async () => {
    renderApp("/leaders/eu-1/consent").on(REPORT, fail(503, "leader_unreachable"));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });
});
