import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { LocationOut } from "../../api/types";
import { fail, reply } from "../../test/fetchMock";
import { leader } from "../../test/fixtures";
import { renderApp } from "../../test/renderApp";
import { EMPTY_FORM, validateLocation } from "./locationForm";
import { locationBody } from "./LocationsTab";

const LOCATION: LocationOut = {
  id: "l1",
  name: "intake",
  backend: "local",
  root: "/srv/intake",
  input_prefix: "",
  output_prefix: "transcripts/",
  pool: "default",
  required_device: "any",
  scan_interval_s: 900,
  enabled: true,
  last_scan_at: null,
  last_scan_error: "the root folder is not readable",
  scan_requested: false,
  channel_mode: "mono",
  channel_labels: ["Left", "Right"],
};
const DISABLED: LocationOut = { ...LOCATION, id: "l3", name: "archive", enabled: false };
const LOCATIONS = "GET /api/leaders/eu-1/locations";
const ADD = "POST /api/leaders/eu-1/locations";
const ROOT_LABEL = "Folder on the leader (absolute path)";

const valid = { ...EMPTY_FORM, name: "calls", root: "/srv/calls" };

describe("location form rules", () => {
  it("sends only the fields the person set, and labels only with a split mode", () => {
    expect(locationBody(valid)).toEqual({ name: "calls", root: "/srv/calls" });
    expect(locationBody({ ...valid, channel_mode: "mono", left: "Host", right: "Guest" })).toEqual({
      name: "calls",
      root: "/srv/calls",
    });
    expect(
      locationBody({
        ...valid,
        name: " calls ",
        pool: " lab ",
        output_prefix: "out/",
        required_device: "cuda",
        scan_interval_s: "60",
        channel_mode: "stereo_split",
        left: "Host",
        right: "Guest",
      }),
    ).toEqual({
      name: "calls",
      root: "/srv/calls",
      pool: "lab",
      output_prefix: "out/",
      required_device: "cuda",
      scan_interval_s: 60,
      channel_mode: "stereo_split",
      channel_labels: ["Host", "Guest"],
    });
  });

  it.each(["/srv/x", "C:\\srv\\x", "c:/srv/x", "\\\\host\\share\\x", "//host/share"])(
    "accepts the absolute root %j",
    (root) => {
      expect(validateLocation({ ...valid, root })).toEqual({});
    },
  );

  it.each(["", "srv/x", "./x", "C:srv", "\\srv", "C:", "/srv/\u0007x", `/${"a".repeat(1000)}`])(
    "refuses the root %j",
    (root) => {
      expect(Object.keys(validateLocation({ ...valid, root }))).toEqual(["root"]);
    },
  );

  it.each(["", "-a", ".a", "a b", "a/b", "a".repeat(101)])("refuses the name %j", (name) => {
    expect(Object.keys(validateLocation({ ...valid, name }))).toEqual(["name"]);
  });

  it("accepts the ends of the name pattern", () => {
    expect(validateLocation({ ...valid, name: "a".repeat(100), pool: "A.b_c-9" })).toEqual({});
  });

  it.each(["x", "/x/", "a//", "../", "a/./", "a b /", "a:/", "a\\b/", "a/\u0007/"])(
    "refuses the prefix %j",
    (prefix) => {
      expect(Object.keys(validateLocation({ ...valid, input_prefix: prefix }))).toEqual([
        "input_prefix",
      ]);
      expect(Object.keys(validateLocation({ ...valid, output_prefix: prefix }))).toEqual([
        "output_prefix",
      ]);
    },
  );

  it("accepts relative folder prefixes", () => {
    expect(validateLocation({ ...valid, input_prefix: "in/a b/", output_prefix: "out/" })).toEqual(
      {},
    );
  });

  it.each(["29", "604801", "1.5", "-30", "abc"])("refuses the scan interval %j", (value) => {
    expect(Object.keys(validateLocation({ ...valid, scan_interval_s: value }))).toEqual([
      "scan_interval_s",
    ]);
  });

  it.each(["30", "604800", ""])("accepts the scan interval %j", (value) => {
    expect(validateLocation({ ...valid, scan_interval_s: value })).toEqual({});
  });

  const split = { ...valid, channel_mode: "auto" as const };
  it.each([
    ["", "Right", "left"],
    ["Left", "", "right"],
    [" Left", "Right", "left"],
    ["Left", "Right ", "right"],
    ["a".repeat(41), "Right", "left"],
    ["Left", "Li\nne", "right"],
    ["Host", "HOST", "right"],
  ])("refuses the labels %j and %j", (left, right, field) => {
    expect(Object.keys(validateLocation({ ...split, left, right }))).toEqual([field]);
  });

  it("checks labels only for a split mode, and accepts the length limit", () => {
    expect(validateLocation({ ...valid, left: "", right: "" })).toEqual({});
    expect(validateLocation({ ...split, left: "a".repeat(40), right: "b" })).toEqual({});
  });
});

describe("locations tab", () => {
  it("requests a scan, and asks before disabling", async () => {
    const mock = renderApp("/leaders/eu-1/locations", { fleet: [leader({ role: "admin" })] })
      .on(LOCATIONS, reply(200, [LOCATION]))
      .on(
        "POST /api/leaders/eu-1/locations/intake/ingest",
        reply(202, { name: "intake", requested_at: "2026-10-04T12:00:00Z" }),
      )
      .on(
        "POST /api/leaders/eu-1/locations/intake/disable",
        reply(200, { ...LOCATION, enabled: false }),
      );
    expect(await screen.findByText("the root folder is not readable")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Scan now intake" }));
    expect(await screen.findByText("A scan of intake is requested.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Disable intake" }));
    const dialog = screen.getByRole("alertdialog", { name: "Disable intake?" });
    expect(mock.callsTo("POST /api/leaders/eu-1/locations/intake/disable")).toHaveLength(0);
    await userEvent.click(within(dialog).getByRole("button", { name: "Disable location" }));
    await waitFor(() =>
      expect(mock.callsTo("POST /api/leaders/eu-1/locations/intake/disable")).toHaveLength(1),
    );
    expect(await screen.findByText("intake is disabled.")).toBeInTheDocument();
  });

  it("enables a disabled location and offers no scan for it", async () => {
    const mock = renderApp("/leaders/eu-1/locations", { fleet: [leader({ role: "admin" })] })
      .on(LOCATIONS, reply(200, [DISABLED]))
      .on(
        "POST /api/leaders/eu-1/locations/archive/enable",
        reply(200, { ...DISABLED, enabled: true }),
      );
    await userEvent.click(await screen.findByRole("button", { name: "Enable archive" }));
    expect(await screen.findByText("archive is enabled.")).toBeInTheDocument();
    expect(mock.callsTo("POST /api/leaders/eu-1/locations/archive/enable")).toHaveLength(1);
    expect(screen.queryByRole("button", { name: "Scan now archive" })).not.toBeInTheDocument();
  });

  it("shows each action disabled with the role it needs", async () => {
    renderApp("/leaders/eu-1/locations", { fleet: [leader({ role: "viewer" })] }).on(
      LOCATIONS,
      reply(200, [LOCATION]),
    );
    expect(await screen.findByRole("button", { name: "Disable intake" })).toBeDisabled();
    const add = screen.getByRole("button", { name: "Add location" });
    expect(add).toBeDisabled();
    expect(add).toHaveAccessibleDescription("needs admin");
    const scan = screen.getByRole("button", { name: "Scan now intake" });
    expect(scan).toBeDisabled();
    expect(scan).toHaveAccessibleDescription("needs operator");
  });

  it("lets an operator scan but not add or disable", async () => {
    renderApp("/leaders/eu-1/locations", { fleet: [leader({ role: "operator" })] }).on(
      LOCATIONS,
      reply(200, [LOCATION]),
    );
    expect(await screen.findByRole("button", { name: "Scan now intake" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Add location" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Disable intake" })).toBeDisabled();
  });

  it("announces a refused scan where the person is", async () => {
    renderApp("/leaders/eu-1/locations", { fleet: [leader({ role: "admin" })] })
      .on(LOCATIONS, reply(200, [LOCATION]))
      .on(
        "POST /api/leaders/eu-1/locations/intake/ingest",
        fail(409, "disabled", "location is disabled"),
      );
    await userEvent.click(await screen.findByRole("button", { name: "Scan now intake" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The location is disabled. Enable it first.",
    );
  });

  it("keeps the disable dialog open and announces the failure", async () => {
    renderApp("/leaders/eu-1/locations", { fleet: [leader({ role: "admin" })] })
      .on(LOCATIONS, reply(200, [LOCATION]))
      .on("POST /api/leaders/eu-1/locations/intake/disable", fail(503, "leader_unreachable"));
    await userEvent.click(await screen.findByRole("button", { name: "Disable intake" }));
    const dialog = screen.getByRole("alertdialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Disable location" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "The leader cannot be reached right now.",
    );
  });

  async function openAdd() {
    const mock = renderApp("/leaders/eu-1/locations", { fleet: [leader({ role: "admin" })] })
      .on(LOCATIONS, reply(200, [LOCATION]))
      .on(ADD, reply(201, { ...LOCATION, id: "l2", name: "calls" }));
    await userEvent.click(await screen.findByRole("button", { name: "Add location" }));
    const dialog = screen.getByRole("dialog", { name: "Add a location to eu-1" });
    return { mock, dialog };
  }

  async function fillRequired(dialog: HTMLElement, root = "/srv/calls") {
    await userEvent.type(within(dialog).getByRole("textbox", { name: "Name" }), "calls");
    await userEvent.type(within(dialog).getByRole("textbox", { name: ROOT_LABEL }), root);
  }

  it("adds a location from the form, sending only what was set", async () => {
    const { mock, dialog } = await openAdd();
    await fillRequired(dialog);
    await userEvent.click(within(dialog).getByRole("button", { name: "Add location" }));
    await waitFor(() => expect(mock.callsTo(ADD)).toHaveLength(1));
    expect(mock.callsTo(ADD)[0]?.body).toEqual({ name: "calls", root: "/srv/calls" });
    expect(await screen.findByText("Location calls is added.")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("sends the two labels when the mode splits channels", async () => {
    const { mock, dialog } = await openAdd();
    await fillRequired(dialog, "C:\\srv\\calls");
    expect(
      within(dialog).queryByRole("textbox", { name: "Left channel label" }),
    ).not.toBeInTheDocument();
    await userEvent.selectOptions(
      within(dialog).getByRole("combobox", { name: "Channels" }),
      "stereo_split",
    );
    const left = within(dialog).getByRole("textbox", { name: "Left channel label" });
    await userEvent.clear(left);
    await userEvent.type(left, "Host");
    await userEvent.click(within(dialog).getByRole("button", { name: "Add location" }));
    await waitFor(() => expect(mock.callsTo(ADD)).toHaveLength(1));
    expect(mock.callsTo(ADD)[0]?.body).toEqual({
      name: "calls",
      root: "C:\\srv\\calls",
      channel_mode: "stereo_split",
      channel_labels: ["Host", "Right"],
    });
  });

  it("ties each error to its field, announces it and calls nothing", async () => {
    const { mock, dialog } = await openAdd();
    await userEvent.type(within(dialog).getByRole("textbox", { name: "Name" }), "-bad");
    await userEvent.type(
      within(dialog).getByRole("textbox", { name: ROOT_LABEL }),
      "relative/path",
    );
    await userEvent.click(within(dialog).getByRole("button", { name: "Add location" }));
    const name = within(dialog).getByRole("textbox", { name: "Name" });
    const root = within(dialog).getByRole("textbox", { name: ROOT_LABEL });
    expect(name).toHaveAttribute("aria-invalid", "true");
    expect(root).toHaveAttribute("aria-invalid", "true");
    expect(name).toHaveAccessibleDescription(/Start with a letter or digit/);
    expect(root).toHaveAccessibleDescription(/absolute folder path.*C:\\recordings/);
    expect(within(dialog).getAllByRole("alert")).toHaveLength(2);
    expect(name).toHaveFocus();
    expect(within(dialog).getByRole("textbox", { name: "Pool" })).not.toHaveAttribute(
      "aria-invalid",
    );
    expect(mock.callsTo(ADD)).toHaveLength(0);
  });

  it("clears an error when the field is corrected", async () => {
    const { mock, dialog } = await openAdd();
    const name = within(dialog).getByRole("textbox", { name: "Name" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Add location" }));
    expect(name).toHaveAttribute("aria-invalid", "true");
    await fillRequired(dialog);
    expect(name).not.toHaveAttribute("aria-invalid");
    await userEvent.click(within(dialog).getByRole("button", { name: "Add location" }));
    await waitFor(() => expect(mock.callsTo(ADD)).toHaveLength(1));
  });

  it("shows fixed text for a server 422, never the server's field paths", async () => {
    const { mock, dialog } = await openAdd();
    mock.on(ADD, fail(422, "invalid_request", "body.root: <img src=x onerror=alert(1)> bad"));
    await fillRequired(dialog);
    await userEvent.click(within(dialog).getByRole("button", { name: "Add location" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "The values were not accepted. Check each field and try again.",
    );
    expect(dialog).not.toHaveTextContent("body.root");
    expect(dialog.querySelector("img")).toBeNull();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("shows the leader's own refusal in the dialog and keeps it open", async () => {
    const { mock, dialog } = await openAdd();
    mock.on(ADD, fail(409, "overlaps", "overlaps another location"));
    await fillRequired(dialog);
    await userEvent.click(within(dialog).getByRole("button", { name: "Add location" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "That location overlaps another location.",
    );
  });

  it("labels every field and returns focus to the opener on Cancel", async () => {
    const { dialog } = await openAdd();
    for (const control of within(dialog).getAllByRole("textbox")) {
      expect(control).toHaveAccessibleName();
    }
    for (const control of within(dialog).getAllByRole("combobox")) {
      expect(control).toHaveAccessibleName();
    }
    await userEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(screen.getByRole("button", { name: "Add location" })).toHaveFocus();
  });
});
