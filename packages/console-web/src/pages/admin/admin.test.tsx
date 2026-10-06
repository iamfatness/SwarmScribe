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

describe("administration frame", () => {
  it("is one page named Administration, with its three sections as links", async () => {
    renderApp("/admin/leaders", { session: ADMIN_SESSION }).on("GET /api/admin/leaders", reply(200, [LEADER]));
    expect(await screen.findByRole("heading", { level: 1, name: "Administration" })).toBeInTheDocument();
    expect(
      screen.getByText(/Console administrators decide which leaders this console talks to/),
    ).toHaveTextContent("Being one gives you no role on any leader by itself.");
    const sections = within(screen.getByRole("navigation", { name: "Administration" }));
    expect(sections.getAllByRole("link").map((link) => [link.textContent, link.getAttribute("href")])).toEqual([
      ["Leaders", "/admin/leaders"],
      ["Who can do what", "/admin/grants"],
      ["Console administrators", "/admin/admins"],
    ]);
    expect(sections.getByRole("link", { name: "Leaders" })).toHaveAttribute("aria-current", "page");
    await waitFor(() => expect(document.title).toBe("Administration: Leaders · SwarmScribe console"));
    expect(await screen.findByRole("heading", { level: 2, name: "1\u00a0leader" })).toBeInTheDocument();
  });

  it("keeps focus on the section link when only the section changes", async () => {
    renderApp("/admin/leaders", { session: ADMIN_SESSION })
      .on("GET /api/admin/leaders", reply(200, [LEADER]))
      .on("GET /api/admin/grants", reply(200, [GRANT]));
    const sections = within(await screen.findByRole("navigation", { name: "Administration" }));
    const who = sections.getByRole("link", { name: "Who can do what" });
    await userEvent.click(who);
    expect(await screen.findByRole("heading", { level: 2, name: "Who can do what" })).toBeInTheDocument();
    expect(who).toHaveAttribute("aria-current", "page");
    expect(who).toHaveFocus();
    expect(screen.getByRole("heading", { level: 1, name: "Administration" })).not.toHaveFocus();
  });

  it("moves focus to the Administration heading when arriving from the fleet", async () => {
    renderApp("/", { session: ADMIN_SESSION, fleet: [] }).on("GET /api/admin/leaders", reply(200, [LEADER]));
    await userEvent.click(await screen.findByRole("link", { name: "Administration" }));
    const heading = await screen.findByRole("heading", { level: 1, name: "Administration" });
    await waitFor(() => expect(heading).toHaveFocus());
  });

  it("explains the two steps of adding a leader, beside the list", async () => {
    renderApp("/admin/leaders", { session: ADMIN_SESSION }).on("GET /api/admin/leaders", reply(200, []));
    const steps = await screen.findByRole("region", { name: "Adding a leader takes two steps" });
    expect(within(steps).getAllByRole("listitem")).toHaveLength(2);
    expect(within(steps).getByText("swarmscribe-admin console create")).toBeInTheDocument();
    expect(await screen.findByText("This console talks to no leaders yet.")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: "0\u00a0leaders" })).toBeInTheDocument();
  });

  it("shows a leader's state and credential, and names each row's buttons", async () => {
    const revoked = {
      ...LEADER,
      name: "us-1",
      base_url: "https://us-1.leaders.example",
      enabled: false,
      credential_revoked: true,
    };
    renderApp("/admin/leaders", { session: ADMIN_SESSION }).on(
      "GET /api/admin/leaders",
      reply(200, [LEADER, revoked]),
    );
    const region = await screen.findByRole("region", { name: "Registered leaders" });
    expect(
      within(region)
        .getAllByRole("columnheader")
        .map((th) => th.textContent),
    ).toEqual(["Leader", "Address", "Labels", "State", "Credential", "Actions"]);
    const eu = within(region).getByRole("row", { name: /eu-1/ });
    expect(within(eu).getAllByRole("cell")[2]).toHaveTextContent(/^On$/);
    expect(
      within(eu)
        .getAllByRole("button")
        .map((button) => [button.textContent, button.getAttribute("aria-label")]),
    ).toEqual([
      ["Edit", "Edit eu-1"],
      ["Replace credential", "Replace credential for eu-1"],
      ["Remove", "Remove eu-1"],
    ]);
    const us = within(region).getByRole("row", { name: /us-1/ });
    expect(within(us).getAllByRole("cell")[2]).toHaveTextContent(/^Switched off$/);
    expect(us).toHaveTextContent("Revoked by the leader");
  });

  it("keeps a label of ordinary length whole, one label to a line, and lets a long one break", async () => {
    const long = `note=${"x".repeat(40)}`;
    const labelled = { ...LEADER, labels: { env: "prod", note: "x".repeat(40), region: "eu" } };
    const bare = { ...LEADER, name: "us-1", base_url: "https://us-1.leaders.example", labels: {} };
    renderApp("/admin/leaders", { session: ADMIN_SESSION }).on(
      "GET /api/admin/leaders",
      reply(200, [labelled, bare]),
    );
    const region = await screen.findByRole("region", { name: "Registered leaders" });
    const cell = within(within(region).getByRole("row", { name: /eu-1/ })).getAllByRole("cell")[1];
    // The text is the labels as they are stored, line breaks included.
    expect(cell?.textContent).toBe(`env=prod\n${long}\nregion=eu`);
    expect(Array.from(cell?.querySelectorAll(".nowrap") ?? [], (el) => el.textContent)).toEqual([
      "env=prod",
      "region=eu",
    ]);
    expect(within(within(region).getByRole("row", { name: /us-1/ })).getAllByRole("cell")[1]?.textContent).toBe("–");
    // The row's actions are one set, which wraps as a whole (styles/surfaces.css, .action-set).
    expect(region.querySelectorAll("td.actions > .action-set")).toHaveLength(2);
  });

  it("keeps the heading and the way to add a leader when the list cannot be loaded", async () => {
    let n = 0;
    renderApp("/admin/leaders", { session: ADMIN_SESSION }).on("GET /api/admin/leaders", () =>
      ++n === 1 ? fail(503, "unavailable", "down") : reply(200, [LEADER]),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The console is not answering just now. Try again shortly.",
    );
    // Not "0 leaders": the console does not know how many there are.
    expect(screen.getByRole("heading", { level: 2, name: "Leaders" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add a leader" })).toBeEnabled();
    expect(screen.getByRole("region", { name: "Adding a leader takes two steps" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("heading", { level: 2, name: "1\u00a0leader" })).toBeInTheDocument();
  });

  it("keeps what was typed when the kind of who is changed, and renames the box", async () => {
    renderApp("/admin/admins", { session: ADMIN_SESSION }).on(
      "GET /api/admin/console-admins",
      reply(200, [ADMIN]),
    );
    const form = await screen.findByRole("form", { name: "Add a console administrator" });
    await userEvent.type(within(form).getByRole("textbox", { name: "Group object ID" }), "example.org");
    await userEvent.selectOptions(within(form).getByRole("combobox", { name: "Who" }), "domain");
    expect(within(form).queryByRole("textbox", { name: "Group object ID" })).not.toBeInTheDocument();
    expect(within(form).getByRole("textbox", { name: "Domain" })).toHaveValue("example.org");
  });

  it("names the box for each kind of who, with its hint", async () => {
    renderApp("/admin/grants", { session: ADMIN_SESSION }).on("GET /api/admin/grants", reply(200, []));
    const form = await screen.findByRole("form", { name: "Give a role" });
    const who = within(form).getByRole("combobox", { name: "Who" });
    const expected: [string, string, RegExp][] = [
      ["entra_group", "Group object ID", /object ID in Entra ID/],
      ["google_group", "Group address", /group's email address/],
      ["email", "Email address", /Google account/],
      ["domain", "Domain", /example\.org/],
    ];
    for (const [kind, label, hint] of expected) {
      await userEvent.selectOptions(who, kind);
      expect(within(form).getByRole("textbox", { name: label })).toHaveAccessibleDescription(hint);
    }
    expect(within(form).getByRole("textbox", { name: "On which leaders" })).toHaveValue("all");
    expect(await screen.findByText("Nobody has been given a role yet.")).toBeInTheDocument();
  });
});

describe("administration pages", () => {
  it("refuses a person who is not a console administrator, without asking", async () => {
    const mock = renderApp("/admin/grants");
    expect(
      await screen.findByText(/Administration is for console administrators/),
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
        await screen.findByText(/Administration is for console administrators/),
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
    await userEvent.click(await screen.findByRole("button", { name: "Add a leader" }));
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
    await userEvent.click(within(dialog).getByRole("button", { name: "Add this leader" }));
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

  it("explains in plain words what a role is and how it is given", async () => {
    renderApp("/admin/grants", { session: ADMIN_SESSION }).on("GET /api/admin/grants", reply(200, [GRANT]));
    // One line before the table; the rest is a callout after it, "How roles work".
    const table = await screen.findByRole("region", { name: "Roles given" });
    const opening = screen.getByText("A person's role on a leader is the highest one given to them that covers it.");
    expect(opening.compareDocumentPosition(table) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    const callout = screen.getByRole("region", { name: "How roles work" });
    expect(table.compareDocumentPosition(callout) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    const roles = within(callout).getAllByRole("listitem").map((item) => item.textContent);
    expect(roles).toEqual([
      "A viewer can look.",
      "An operator can also try jobs again, cancel them, scan a location and wind followers down.",
      "An admin can also add and switch locations, revoke followers and make join tokens.",
    ]);
    expect(within(callout).getByText("leader:eu-1").tagName).toBe("CODE");
    expect(callout).toHaveTextContent("Giving or removing a role takes effect at once.");
    expect(callout).toHaveTextContent(
      "Group membership is read when a person signs in, so a change to a group shows the next time they do.",
    );
    // Nothing explanatory is left between the heading and the table but the one line.
    expect(screen.getByRole("main")).not.toHaveTextContent(/A viewer can look\. An operator/);
  });

  it("says who holds a role in words, never as a kind code", async () => {
    const grants: GrantOut[] = [
      GRANT,
      { ...GRANT, id: "g-entra", principal_kind: "entra_group", principal: "a1a1a1a1-0000-4000-8000-000000000001" },
      { ...GRANT, id: "g-google", principal_kind: "google_group", principal: "ops@example.org" },
      { ...GRANT, id: "g-email", principal_kind: "email", principal: "sam@example.org" },
    ];
    renderApp("/admin/grants", { session: ADMIN_SESSION }).on("GET /api/admin/grants", reply(200, grants));
    const table = await screen.findByRole("region", { name: "Roles given" });
    const who = within(table)
      .getAllByRole("rowheader")
      .map((cell) => cell.textContent);
    expect(who).toEqual([
      "Everyone at example.org",
      "Entra ID group a1a1a1a1-0000-4000-8000-000000000001",
      "Google group ops@example.org",
      "sam@example.org",
    ]);
    expect(table).not.toHaveTextContent(/entra_group|google_group|domain:|email:/);
    // The identifier itself is still there to copy, whole, in mono.
    expect(within(table).getByText("a1a1a1a1-0000-4000-8000-000000000001")).toHaveClass("mono");
    expect(
      within(table).getByRole("button", {
        name: "Remove operator on label:region=eu from Entra ID group a1a1a1a1-0000-4000-8000-000000000001",
      }),
    ).toBeInTheDocument();
    // The stored form of which leaders stays as it is typed.
    expect(within(table).getAllByText("label:region=eu")).toHaveLength(4);
  });

  it("shows the command in the credential help as code, without the backticks", async () => {
    renderApp("/admin/leaders", { session: ADMIN_SESSION }).on("GET /api/admin/leaders", reply(200, [LEADER]));
    await userEvent.click(await screen.findByRole("button", { name: "Add a leader" }));
    const dialog = screen.getByRole("dialog", { name: "Add a leader" });
    const command = within(dialog).getByText("swarmscribe-admin console create");
    expect(command.tagName).toBe("CODE");
    expect(dialog.textContent).not.toContain("`");
  });

  it("adds and removes a grant", async () => {
    const mock = renderApp("/admin/grants", { session: ADMIN_SESSION })
      .on("GET /api/admin/grants", reply(200, [GRANT]))
      .on("POST /api/admin/grants", reply(201, { ...GRANT, id: "g2", role: "viewer", scope: "all" }))
      .on(`DELETE /api/admin/grants/${GRANT.id}`, reply(204));
    const form = await screen.findByRole("form", { name: "Give a role" });
    await userEvent.selectOptions(within(form).getByRole("combobox", { name: "Who" }), "domain");
    await userEvent.type(within(form).getByRole("textbox", { name: "Domain" }), "example.org");
    await userEvent.click(within(form).getByRole("button", { name: "Give the role" }));
    await waitFor(() =>
      expect(mock.callsTo("POST /api/admin/grants")[0]?.body).toEqual({
        role: "viewer",
        scope: "all",
        principal_kind: "domain",
        principal: "example.org",
      }),
    );
    expect(await screen.findByText("Everyone at example.org is now viewer on all.")).toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("button", { name: "Remove operator on label:region=eu from everyone at example.org" }),
    );
    const asking = screen.getByRole("alertdialog");
    expect(asking).toHaveTextContent("Everyone at example.org stops being operator on label:region=eu at once.");
    await userEvent.click(within(asking).getByRole("button", { name: "Remove the role" }));
    await waitFor(() => expect(mock.callsTo(`DELETE /api/admin/grants/${GRANT.id}`)).toHaveLength(1));
  });

  it("shows the last-administrator refusal", async () => {
    renderApp("/admin/admins", { session: ADMIN_SESSION })
      .on("GET /api/admin/console-admins", reply(200, [ADMIN]))
      .on(
        `DELETE /api/admin/console-admins/${ADMIN.id}`,
        fail(409, "last_admin", "the last console administrator cannot be removed"),
      );
    await userEvent.click(
      await screen.findByRole("button", { name: `Remove console administrator Entra ID group ${ADMIN.principal}` }),
    );
    expect(screen.getByRole("alertdialog")).toHaveTextContent(
      `Entra ID group ${ADMIN.principal} can no longer add leaders or give roles.`,
    );
    expect(screen.getByRole("main")).not.toHaveTextContent("entra_group");
    await userEvent.click(
      within(screen.getByRole("alertdialog")).getByRole("button", { name: "Remove administrator" }),
    );
    expect(await within(screen.getByRole("alertdialog")).findByRole("alert")).toHaveTextContent(
      "The last console administrator cannot be removed.",
    );
  });
});
