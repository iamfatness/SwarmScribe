import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { ConsoleAdminOut, LeaderOut } from "../../api/types";
import { fail, reply } from "../../test/fetchMock";
import { SESSION } from "../../test/fixtures";
import { reactStateHolds } from "../../test/reactState";
import { renderApp } from "../../test/renderApp";

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
const ADMIN: ConsoleAdminOut = {
  id: "88888888-8888-4888-8888-888888888888",
  principal_kind: "entra_group",
  principal: "a1a1a1a1-0000-4000-8000-0000000000c0",
  created_by: "swarmscribe-console cli",
  created_at: "2026-10-01T00:00:00Z",
};
const LIST = "GET /api/admin/leaders";
const ROTATE = "PUT /api/admin/leaders/eu-1/credential";
const PATCH = "PATCH /api/admin/leaders/eu-1";

async function openRotate() {
  await userEvent.click(await screen.findByRole("button", { name: "Rotate credential for eu-1" }));
  return screen.getByRole("dialog", { name: "Rotate the credential for eu-1" });
}

describe("leader administration requests", () => {
  it("refuses a malformed name, address, credential or label set without sending", async () => {
    const mock = renderApp("/admin/leaders", { session: ADMIN_SESSION }).on(
      LIST,
      reply(200, [LEADER]),
    );
    await userEvent.click(await screen.findByRole("button", { name: "Add leader" }));
    const dialog = screen.getByRole("dialog", { name: "Add a leader" });
    const submit = within(dialog).getByRole("button", { name: "Add leader" });
    const name = within(dialog).getByRole("textbox", { name: "Name" });
    const url = within(dialog).getByRole("textbox", { name: "Address (https://)" });
    const labels = within(dialog).getByRole("textbox", { name: "Labels" });
    const credential = within(dialog).getByLabelText("Console credential");

    await userEvent.type(name, "-bad name");
    await userEvent.click(submit);
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("That name is not valid.");

    await userEvent.clear(name);
    await userEvent.type(name, "us-1");
    await userEvent.clear(url);
    await userEvent.type(url, "http://us-1.leaders.example");
    await userEvent.click(submit);
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "The console may not call that address.",
    );

    await userEvent.clear(url);
    await userEvent.type(url, "https://us-1.leaders.example");
    await userEvent.type(credential, "too-short");
    await userEvent.click(submit);
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "That is not a console credential.",
    );

    await userEvent.clear(credential);
    await userEvent.type(credential, CREDENTIAL);
    await userEvent.type(labels, "Region=eu");
    await userEvent.click(submit);
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Those labels are not valid.",
    );
    expect(mock.calls.filter((call) => call.method === "POST")).toHaveLength(0);
  });

  it("refuses more than 32 labels", async () => {
    const mock = renderApp("/admin/leaders", { session: ADMIN_SESSION }).on(
      LIST,
      reply(200, [LEADER]),
    );
    await userEvent.click(await screen.findByRole("button", { name: "Edit eu-1" }));
    const dialog = screen.getByRole("dialog", { name: "Edit eu-1" });
    const labels = within(dialog).getByRole("textbox", { name: "Labels" });
    await userEvent.clear(labels);
    await userEvent.click(labels);
    await userEvent.paste(Array.from({ length: 33 }, (_, i) => `k${i}=v`).join("\n"));
    await userEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Those labels are not valid.",
    );
    expect(mock.calls.filter((call) => call.method === "PATCH")).toHaveLength(0);
  });

  it("edits with only the changed fields", async () => {
    const mock = renderApp("/admin/leaders", { session: ADMIN_SESSION })
      .on(LIST, reply(200, [LEADER]))
      .on(PATCH, reply(200, { ...LEADER, enabled: false }));
    await userEvent.click(await screen.findByRole("button", { name: "Edit eu-1" }));
    const dialog = screen.getByRole("dialog", { name: "Edit eu-1" });
    await userEvent.click(within(dialog).getByRole("checkbox", { name: "Enabled" }));
    await userEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(mock.callsTo(PATCH)[0]?.body).toEqual({ enabled: false }));
    expect(await screen.findByText("Leader eu-1 is saved.")).toBeInTheDocument();
  });

  it("sends the credential with a changed address", async () => {
    const mock = renderApp("/admin/leaders", { session: ADMIN_SESSION })
      .on(LIST, reply(200, [LEADER]))
      .on(PATCH, reply(200, LEADER));
    await userEvent.click(await screen.findByRole("button", { name: "Edit eu-1" }));
    const dialog = screen.getByRole("dialog", { name: "Edit eu-1" });
    const url = within(dialog).getByRole("textbox", { name: "Address (https://)" });
    await userEvent.clear(url);
    await userEvent.type(url, "https://eu-2.leaders.example");
    const credential = within(dialog).getByLabelText("Console credential for the new address");
    expect(credential).toHaveAttribute("type", "password");
    expect(credential).toHaveAttribute("autocomplete", "off");
    await userEvent.type(credential, CREDENTIAL);
    await userEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(mock.callsTo(PATCH)[0]?.body).toEqual({
        base_url: "https://eu-2.leaders.example",
        credential: CREDENTIAL,
      }),
    );
  });

  it("does not ask for a credential for an address the server reads as the same", async () => {
    const mock = renderApp("/admin/leaders", { session: ADMIN_SESSION }).on(
      LIST,
      reply(200, [LEADER]),
    );
    await userEvent.click(await screen.findByRole("button", { name: "Edit eu-1" }));
    const dialog = screen.getByRole("dialog", { name: "Edit eu-1" });
    await userEvent.type(within(dialog).getByRole("textbox", { name: "Address (https://)" }), "/");
    expect(
      within(dialog).queryByLabelText("Console credential for the new address"),
    ).not.toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(mock.calls.filter((call) => call.method === "PATCH")).toHaveLength(0);
  });

  it("removes a leader only after the confirmation", async () => {
    const mock = renderApp("/admin/leaders", { session: ADMIN_SESSION })
      .on(LIST, reply(200, [LEADER]))
      .on("DELETE /api/admin/leaders/eu-1", reply(204));
    await userEvent.click(await screen.findByRole("button", { name: "Remove eu-1" }));
    const dialog = screen.getByRole("alertdialog", { name: "Remove eu-1?" });
    expect(mock.callsTo("DELETE /api/admin/leaders/eu-1")).toHaveLength(0);
    await userEvent.click(within(dialog).getByRole("button", { name: "Remove leader" }));
    expect(await screen.findByText("Leader eu-1 is removed.")).toBeInTheDocument();
  });

  it("shows a refusal as fixed text, never as markup from the server's words", async () => {
    renderApp("/admin/leaders", { session: ADMIN_SESSION })
      .on(LIST, reply(200, [LEADER]))
      .on("POST /api/admin/leaders", fail(409, "exists", "a leader named '<b>x</b>' exists"));
    await userEvent.click(await screen.findByRole("button", { name: "Add leader" }));
    const dialog = screen.getByRole("dialog", { name: "Add a leader" });
    await userEvent.type(within(dialog).getByRole("textbox", { name: "Name" }), "eu-1");
    await userEvent.type(
      within(dialog).getByRole("textbox", { name: "Address (https://)" }),
      "eu-1.leaders.example",
    );
    await userEvent.type(within(dialog).getByLabelText("Console credential"), CREDENTIAL);
    await userEvent.click(within(dialog).getByRole("button", { name: "Add leader" }));
    const alert = await within(dialog).findByRole("alert");
    expect(alert).toHaveTextContent("That already exists.");
    expect(alert.querySelector("b")).toBeNull();
  });
});

describe("a credential in the administration pages", () => {
  it("is sent by PUT, never PATCH, and is nowhere once the dialog is done", async () => {
    const logs = (["log", "info", "warn", "error", "debug"] as const).map((method) =>
      vi.spyOn(globalThis.console, method).mockImplementation(() => undefined),
    );
    const writes = vi.spyOn(Storage.prototype, "setItem");
    const reads = vi.spyOn(Storage.prototype, "getItem");
    const mock = renderApp("/admin/leaders", { session: ADMIN_SESSION })
      .on(LIST, reply(200, [LEADER]))
      .on(ROTATE, reply(200, LEADER));
    const dialog = await openRotate();
    const credential = within(dialog).getByLabelText("New console credential");
    expect(credential).toHaveAttribute("type", "password");
    expect(credential).toHaveAttribute("autocomplete", "off");
    await userEvent.type(credential, CREDENTIAL);
    expect(reactStateHolds(CREDENTIAL)).toBe(true);
    expect(window.location.href).not.toContain(CREDENTIAL);
    await userEvent.click(within(dialog).getByRole("button", { name: "Replace credential" }));
    expect(await screen.findByText("The credential for eu-1 is replaced.")).toBeInTheDocument();
    expect(mock.callsTo(ROTATE)[0]?.body).toEqual({ credential: CREDENTIAL });
    expect(mock.calls.filter((call) => call.method === "PATCH")).toHaveLength(0);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(document.documentElement.outerHTML).not.toContain(CREDENTIAL);
    expect(reactStateHolds(CREDENTIAL)).toBe(false);
    expect(window.location.href).not.toContain(CREDENTIAL);
    expect(document.title).not.toContain(CREDENTIAL);
    expect(JSON.stringify(window.history.state)).not.toContain(CREDENTIAL);
    expect(JSON.stringify(writes.mock.calls)).not.toContain(CREDENTIAL);
    expect(JSON.stringify(reads.mock.calls)).not.toContain(CREDENTIAL);
    for (const log of logs) expect(JSON.stringify(log.mock.calls)).not.toContain(CREDENTIAL);
    expect(mock.calls.filter((call) => call.url.includes(CREDENTIAL))).toHaveLength(0);
  });

  it("stays out of the error text when refused, and is cleared when the dialog closes", async () => {
    renderApp("/admin/leaders", { session: ADMIN_SESSION })
      .on(LIST, reply(200, [LEADER]))
      .on(ROTATE, fail(422, "invalid_credential", `a console credential is ${CREDENTIAL}`));
    const dialog = await openRotate();
    await userEvent.type(within(dialog).getByLabelText("New console credential"), CREDENTIAL);
    await userEvent.click(within(dialog).getByRole("button", { name: "Replace credential" }));
    const alert = await within(dialog).findByRole("alert");
    expect(alert).toHaveTextContent("That is not a console credential.");
    expect(alert).not.toHaveTextContent(CREDENTIAL);
    await userEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(reactStateHolds(CREDENTIAL)).toBe(false);
    expect(document.documentElement.outerHTML).not.toContain(CREDENTIAL);
  });

  it("is cleared from the add dialog's state after success", async () => {
    const mock = renderApp("/admin/leaders", { session: ADMIN_SESSION })
      .on(LIST, reply(200, [LEADER]))
      .on("POST /api/admin/leaders", reply(201, LEADER));
    await userEvent.click(await screen.findByRole("button", { name: "Add leader" }));
    const dialog = screen.getByRole("dialog", { name: "Add a leader" });
    await userEvent.type(within(dialog).getByRole("textbox", { name: "Name" }), "us-1");
    await userEvent.type(
      within(dialog).getByRole("textbox", { name: "Address (https://)" }),
      "us-1.leaders.example",
    );
    await userEvent.type(within(dialog).getByLabelText("Console credential"), CREDENTIAL);
    await userEvent.click(within(dialog).getByRole("button", { name: "Add leader" }));
    await waitFor(() => expect(mock.callsTo("POST /api/admin/leaders")).toHaveLength(1));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(reactStateHolds(CREDENTIAL)).toBe(false);
  });
});

describe("administrator and grant refusals", () => {
  it("adds an administrator, and shows an exists refusal", async () => {
    const mock = renderApp("/admin/admins", { session: ADMIN_SESSION })
      .on("GET /api/admin/console-admins", reply(200, [ADMIN]))
      .on("POST /api/admin/console-admins", fail(409, "exists", "already"));
    const form = await screen.findByRole("form", { name: "Add a console administrator" });
    await userEvent.selectOptions(
      within(form).getByRole("combobox", { name: "Principal kind" }),
      "email",
    );
    await userEvent.type(within(form).getByRole("textbox", { name: "Principal" }), "a@example.org");
    await userEvent.click(within(form).getByRole("button", { name: "Add administrator" }));
    await waitFor(() =>
      expect(mock.callsTo("POST /api/admin/console-admins")[0]?.body).toEqual({
        principal_kind: "email",
        principal: "a@example.org",
      }),
    );
    expect(await within(form).findByRole("alert")).toHaveTextContent("That already exists.");
  });

  it("shows the server's scope refusal as fixed text", async () => {
    renderApp("/admin/grants", { session: ADMIN_SESSION })
      .on("GET /api/admin/grants", reply(200, []))
      .on("POST /api/admin/grants", fail(422, "invalid_scope", "scope <i>x</i>"));
    const form = await screen.findByRole("form", { name: "Add a grant" });
    await userEvent.type(within(form).getByRole("textbox", { name: "Principal" }), "a1");
    await userEvent.click(within(form).getByRole("button", { name: "Add grant" }));
    const alert = await within(form).findByRole("alert");
    expect(alert).toHaveTextContent("That is not a way to say which leaders.");
    expect(alert.querySelector("i")).toBeNull();
  });
});
