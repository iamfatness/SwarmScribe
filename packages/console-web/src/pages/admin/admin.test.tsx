import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { ConsoleAdminOut, GrantOut, LeaderOut } from "../../api/types";
import { fail, reply } from "../../test/fetchMock";
import { SESSION } from "../../test/fixtures";
import { renderApp } from "../../test/renderApp";
import { parseLabels } from "./AdminFrame";
import { editBody } from "./AdminLeadersPage";

const ADMIN_SESSION = { ...SESSION, console_admin: true };
const CREDENTIAL = "c".repeat(20) + "_-" + "D".repeat(21);
const LEADER: LeaderOut = {
  name: "eu-1",
  base_url: "https://eu-1.leaders.example",
  labels: { region: "eu" },
  enabled: true,
  added_by: "admin@example.org",
  created_at: "2026-10-01T00:00:00Z",
  credential_updated_at: "2026-10-01T00:00:00Z",
  credential_updated_by: "admin@example.org",
  credential_revoked: false,
  credential_revoked_at: null,
};
const GRANT: GrantOut = {
  id: "77777777-7777-4777-8777-777777777777",
  role: "operator",
  scope: "label:region=eu",
  principal_kind: "domain",
  principal: "example.org",
  created_by: "admin@example.org",
  created_at: "2026-10-02T00:00:00Z",
};
const ADMIN: ConsoleAdminOut = {
  id: "88888888-8888-4888-8888-888888888888",
  principal_kind: "entra_group",
  principal: "a1a1a1a1-0000-4000-8000-0000000000c0",
  created_by: "swarmscribe-console cli",
  created_at: "2026-10-01T00:00:00Z",
};

describe("administration helpers", () => {
  it("parses labels one key=value per line", () => {
    expect(parseLabels("region=eu\n env = prod \n\n")).toEqual({ region: "eu", env: "prod" });
    expect(parseLabels("")).toEqual({});
    expect(parseLabels("region")).toBeNull();
    expect(parseLabels("=eu")).toBeNull();
    expect(parseLabels("region=")).toBeNull();
  });

  it("sends only what changed, and the credential only with a new address", () => {
    const same = { baseUrl: LEADER.base_url, labels: { region: "eu" }, enabled: true, credential: "" };
    expect(editBody(LEADER, same)).toEqual({});
    expect(editBody(LEADER, { ...same, enabled: false })).toEqual({ enabled: false });
    expect(editBody(LEADER, { ...same, labels: { region: "us" } })).toEqual({ labels: { region: "us" } });
    expect(
      editBody(LEADER, { ...same, baseUrl: "https://eu-2.leaders.example", credential: CREDENTIAL }),
    ).toEqual({
      base_url: "https://eu-2.leaders.example",
      credential: CREDENTIAL,
    });
  });
});

describe("administration pages", () => {
  it("refuses a person who is not a console administrator, without asking", async () => {
    const mock = renderApp("/admin/grants");
    expect(
      await screen.findByText(/Console administration needs a console administrator/),
    ).toBeInTheDocument();
    expect(mock.callsTo("GET /api/admin/grants")).toHaveLength(0);
    expect(screen.queryByRole("link", { name: "Administration" })).not.toBeInTheDocument();
    expect(mock.calls.filter((call) => call.url.startsWith("/api/admin"))).toHaveLength(0);
  });

  it.each(["/admin", "/admin/leaders", "/admin/admins"])(
    "asks nothing of the administration API for a non-administrator at %s",
    async (path) => {
      const mock = renderApp(path);
      expect(
        await screen.findByText(/Console administration needs a console administrator/),
      ).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /Add / })).not.toBeInTheDocument();
      expect(mock.calls.filter((call) => call.url.startsWith("/api/admin"))).toHaveLength(0);
    },
  );

  it("shows Administration in the main navigation for a console administrator", async () => {
    renderApp("/", { session: ADMIN_SESSION, fleet: [] });
    expect(await screen.findByRole("link", { name: "Administration" })).toHaveAttribute(
      "href",
      "/admin/leaders",
    );
  });

  it("adds a leader with a sealed credential field and parsed labels", async () => {
    const mock = renderApp("/admin/leaders", { session: ADMIN_SESSION })
      .on("GET /api/admin/leaders", reply(200, [LEADER]))
      .on("POST /api/admin/leaders", reply(201, { ...LEADER, name: "us-1" }));
    await userEvent.click(await screen.findByRole("button", { name: "Add leader" }));
    const dialog = screen.getByRole("dialog", { name: "Add a leader" });
    await userEvent.type(within(dialog).getByRole("textbox", { name: "Name" }), "us-1");
    const url = within(dialog).getByRole("textbox", { name: "Address (https://)" });
    await userEvent.clear(url);
    await userEvent.type(url, "https://us-1.leaders.example");
    await userEvent.type(within(dialog).getByRole("textbox", { name: "Labels" }), "region=us");
    const credential = within(dialog).getByLabelText("Console credential");
    expect(credential).toHaveAttribute("type", "password");
    expect(credential).toHaveAttribute("autocomplete", "off");
    await userEvent.type(credential, CREDENTIAL);
    await userEvent.click(within(dialog).getByRole("button", { name: "Add leader" }));
    await waitFor(() =>
      expect(mock.callsTo("POST /api/admin/leaders")[0]?.body).toEqual({
        name: "us-1",
        base_url: "https://us-1.leaders.example",
        labels: { region: "us" },
        credential: CREDENTIAL,
        enabled: true,
      }),
    );
    expect(await screen.findByText("Leader us-1 is added.")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(document.body.innerHTML).not.toContain(CREDENTIAL);
  });

  it("refuses malformed labels before sending anything", async () => {
    const mock = renderApp("/admin/leaders", { session: ADMIN_SESSION }).on(
      "GET /api/admin/leaders",
      reply(200, [LEADER]),
    );
    await userEvent.click(await screen.findByRole("button", { name: "Edit eu-1" }));
    const dialog = screen.getByRole("dialog", { name: "Edit eu-1" });
    const labels = within(dialog).getByRole("textbox", { name: "Labels" });
    await userEvent.clear(labels);
    await userEvent.type(labels, "no equals sign");
    await userEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Those labels are not valid.");
    expect(mock.calls.filter((call) => call.method === "PATCH")).toHaveLength(0);
  });

  it("asks for the credential when the address changes", async () => {
    renderApp("/admin/leaders", { session: ADMIN_SESSION }).on(
      "GET /api/admin/leaders",
      reply(200, [LEADER]),
    );
    await userEvent.click(await screen.findByRole("button", { name: "Edit eu-1" }));
    const dialog = screen.getByRole("dialog", { name: "Edit eu-1" });
    expect(
      within(dialog).queryByLabelText("Console credential for the new address"),
    ).not.toBeInTheDocument();
    await userEvent.type(within(dialog).getByRole("textbox", { name: "Address (https://)" }), "x");
    expect(within(dialog).getByLabelText("Console credential for the new address")).toBeRequired();
  });

  it("adds and removes a grant", async () => {
    const mock = renderApp("/admin/grants", { session: ADMIN_SESSION })
      .on("GET /api/admin/grants", reply(200, [GRANT]))
      .on("POST /api/admin/grants", reply(201, { ...GRANT, id: "g2", role: "viewer", scope: "all" }))
      .on(`DELETE /api/admin/grants/${GRANT.id}`, reply(204));
    const form = await screen.findByRole("form", { name: "Add a grant" });
    await userEvent.selectOptions(within(form).getByRole("combobox", { name: "Principal kind" }), "domain");
    await userEvent.type(within(form).getByRole("textbox", { name: "Principal" }), "example.org");
    await userEvent.click(within(form).getByRole("button", { name: "Add grant" }));
    await waitFor(() =>
      expect(mock.callsTo("POST /api/admin/grants")[0]?.body).toEqual({
        role: "viewer",
        scope: "all",
        principal_kind: "domain",
        principal: "example.org",
      }),
    );
    await userEvent.click(screen.getByRole("button", { name: "Remove grant for example.org" }));
    await userEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Remove grant" }));
    await waitFor(() => expect(mock.callsTo(`DELETE /api/admin/grants/${GRANT.id}`)).toHaveLength(1));
  });

  it("shows the last-administrator refusal", async () => {
    renderApp("/admin/admins", { session: ADMIN_SESSION })
      .on("GET /api/admin/console-admins", reply(200, [ADMIN]))
      .on(
        `DELETE /api/admin/console-admins/${ADMIN.id}`,
        fail(409, "last_admin", "the last console administrator cannot be removed"),
      );
    await userEvent.click(await screen.findByRole("button", { name: `Remove ${ADMIN.principal}` }));
    await userEvent.click(
      within(screen.getByRole("alertdialog")).getByRole("button", { name: "Remove administrator" }),
    );
    expect(await within(screen.getByRole("alertdialog")).findByRole("alert")).toHaveTextContent(
      "The last console administrator cannot be removed.",
    );
  });
});
