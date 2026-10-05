import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { TokenCreated, TokenOut } from "../../api/types";
import { fail, reply } from "../../test/fetchMock";
import { leader } from "../../test/fixtures";
import { reactStateHolds } from "../../test/reactState";
import { renderApp } from "../../test/renderApp";
import { TokenReveal } from "./TokensTab";

const SECRET = "sst_plaintext-secret-value";
const TOKEN: TokenOut = {
  id: "44444444-4444-4444-8444-444444444444",
  pool: "default",
  expires_at: "2099-01-01T00:00:00Z",
  max_uses: 5,
  uses: 1,
  revoked: false,
  created_by: "admin@example.org",
  created_at: "2026-10-03T00:00:00Z",
};
const REVOKED: TokenOut = { ...TOKEN, id: "55550000-0000-4000-8000-000000000000", revoked: true };
const EXPIRED: TokenOut = {
  ...TOKEN,
  id: "66660000-0000-4000-8000-000000000000",
  expires_at: "2020-01-01T00:00:00Z",
};
const USED: TokenOut = { ...TOKEN, id: "77770000-0000-4000-8000-000000000000", uses: 5 };
const CREATED: TokenCreated = {
  id: "55555555-5555-4555-8555-555555555555",
  token: SECRET,
  pool: "gpu",
  expires_at: "2026-10-11T12:00:00Z",
  max_uses: 2,
};
const LIST = "GET /api/leaders/eu-1/tokens";
const CREATE = "POST /api/leaders/eu-1/tokens";
const REVOKE = `POST /api/leaders/eu-1/tokens/${TOKEN.id}/revoke`;
const ADMIN = { fleet: [leader({ role: "admin" })] };

afterEach(() => {
  vi.restoreAllMocks();
});

/** Everything that logs: nothing the app does may pass the token to any of these. */
function spyOnLogs() {
  return (["log", "info", "warn", "error", "debug"] as const).map((method) =>
    vi.spyOn(globalThis.console, method).mockImplementation(() => undefined),
  );
}

async function createToken(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole("button", { name: "Create join token" }));
  const form = screen.getByRole("dialog", { name: "Create a join token for eu-1" });
  const pool = within(form).getByRole("textbox", { name: "Pool" });
  await user.clear(pool);
  await user.type(pool, "gpu");
  const uses = within(form).getByRole("spinbutton", { name: "Uses (1 to 10000)" });
  await user.clear(uses);
  await user.type(uses, "2");
  await user.click(within(form).getByRole("button", { name: "Create token" }));
  return screen.findByRole("dialog", { name: "Here is the join token. It is shown once." });
}

describe("join tokens tab: role", () => {
  it.each(["viewer", "operator"] as const)(
    "tells a %s that tokens need admin, and sends no request for them",
    async (role) => {
      const mock = renderApp("/leaders/eu-1/tokens", { fleet: [leader({ role })] });
      expect(
        await screen.findByText(`Join tokens need the admin role on eu-1. Your role is ${role}.`),
      ).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Create join token" })).not.toBeInTheDocument();
      await new Promise((resolve) => setTimeout(resolve, 50));
      expect(mock.calls.filter((call) => call.url.includes("/tokens"))).toHaveLength(0);
    },
  );
});

describe("join tokens tab: list and revoke", () => {
  it("lists tokens by state without any plaintext, and offers revoke only for a live one", async () => {
    renderApp("/leaders/eu-1/tokens", ADMIN).on(LIST, reply(200, [TOKEN, REVOKED, EXPIRED, USED]));
    const table = await screen.findByRole("region", { name: "Join token list" });
    expect(within(table).getByRole("row", { name: /44444444/ })).toHaveTextContent("Can be used");
    expect(within(table).getByRole("row", { name: /55550000/ })).toHaveTextContent("Revoked");
    expect(within(table).getByRole("row", { name: /66660000/ })).toHaveTextContent("Expired");
    expect(within(table).getByRole("row", { name: /77770000/ })).toHaveTextContent("Used up");
    expect(within(table).getByRole("row", { name: /44444444/ })).toHaveTextContent("1 of 5");
    expect(screen.getAllByRole("button", { name: /^Revoke token/ })).toHaveLength(3);
    expect(screen.queryByRole("button", { name: "Revoke token 55550000" })).not.toBeInTheDocument();
  });

  it("asks first, then revokes, announces it and reloads the list", async () => {
    const user = userEvent.setup();
    const mock = renderApp("/leaders/eu-1/tokens", ADMIN)
      .on(LIST, reply(200, [TOKEN]))
      .on(REVOKE, reply(200, { ...TOKEN, revoked: true }));
    await user.click(await screen.findByRole("button", { name: "Revoke token 44444444" }));
    const confirm = screen.getByRole("alertdialog", { name: "Revoke join token 44444444?" });
    expect(mock.callsTo(REVOKE)).toHaveLength(0);
    await user.click(within(confirm).getByRole("button", { name: "Revoke token" }));
    expect(await screen.findByText("Join token 44444444 is revoked.")).toBeInTheDocument();
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    await waitFor(() => expect(mock.callsTo(LIST).length).toBeGreaterThanOrEqual(2));
  });

  it("keeps the confirmation open with the error when the revoke fails", async () => {
    const user = userEvent.setup();
    renderApp("/leaders/eu-1/tokens", ADMIN)
      .on(LIST, reply(200, [TOKEN]))
      .on(REVOKE, fail(502, "bad_gateway"));
    await user.click(await screen.findByRole("button", { name: "Revoke token 44444444" }));
    const confirm = screen.getByRole("alertdialog");
    await user.click(within(confirm).getByRole("button", { name: "Revoke token" }));
    expect(await within(confirm).findByRole("alert")).toBeInTheDocument();
  });
});

describe("join tokens tab: create", () => {
  it("sends the leader's TokenIn and nothing else, with days turned into seconds", async () => {
    const user = userEvent.setup();
    const mock = renderApp("/leaders/eu-1/tokens", ADMIN)
      .on(LIST, reply(200, []))
      .on(CREATE, reply(201, CREATED));
    await createToken(user);
    expect(mock.callsTo(CREATE)).toHaveLength(1);
    expect(mock.callsTo(CREATE)[0]?.body).toEqual({
      pool: "gpu",
      expires_in_seconds: 7 * 86400,
      max_uses: 2,
    });
  });

  it.each([
    ["Pool", "-bad", /pool/i],
    ["Expires after (days, 1 to 90)", "0", /1 to 90/],
    ["Expires after (days, 1 to 90)", "91", /1 to 90/],
    ["Expires after (days, 1 to 90)", "1.5", /1 to 90/],
    ["Uses (1 to 10000)", "0", /1 to 10000/],
    ["Uses (1 to 10000)", "10001", /1 to 10000/],
  ])(
    "refuses %s = %s beside the field, without asking the leader",
    async (label, value, message) => {
      const user = userEvent.setup();
      const mock = renderApp("/leaders/eu-1/tokens", ADMIN).on(LIST, reply(200, []));
      await user.click(await screen.findByRole("button", { name: "Create join token" }));
      const form = screen.getByRole("dialog");
      const field = within(form).getByLabelText(label);
      await user.clear(field);
      await user.type(field, value);
      await user.click(within(form).getByRole("button", { name: "Create token" }));
      expect(await within(form).findByRole("alert")).toHaveTextContent(message);
      expect(field).toHaveFocus();
      expect(mock.callsTo(CREATE)).toHaveLength(0);
    },
  );

  it("announces a failed create in the form and shows no token", async () => {
    const user = userEvent.setup();
    renderApp("/leaders/eu-1/tokens", ADMIN)
      .on(LIST, reply(200, []))
      .on(CREATE, fail(502, "bad_gateway"));
    await user.click(await screen.findByRole("button", { name: "Create join token" }));
    await user.click(screen.getByRole("button", { name: "Create token" }));
    expect(await within(screen.getByRole("dialog")).findByRole("alert")).toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: "Here is the join token. It is shown once." })).not.toBeInTheDocument();
  });
});

describe("join tokens tab: the plaintext is shown once", () => {
  it("says what the token is for, that it is not copied yet, and its pool, uses and expiry", async () => {
    const user = userEvent.setup();
    renderApp("/leaders/eu-1/tokens", ADMIN)
      .on(LIST, reply(200, [TOKEN]))
      .on(CREATE, reply(201, CREATED));
    const shown = await createToken(user);
    expect(within(shown).getByRole("heading", { level: 2 })).toHaveTextContent(
      "Here is the join token. It is shown once.",
    );
    expect(shown).toHaveAccessibleDescription(
      "Copy it now and give it to the machine that will join the gpu pool. After you close this, nobody can read it again, including you.",
    );
    expect(within(shown).getByText("Not copied yet.")).toHaveAttribute("role", "status");
    const facts: Record<string, string> = {};
    for (const term of within(shown).getAllByRole("term")) {
      facts[term.textContent ?? ""] = term.nextElementSibling?.textContent ?? "";
    }
    expect(Object.keys(facts)).toEqual(["Pool", "Can be used", "Expires"]);
    expect(facts.Pool).toBe("gpu");
    expect(facts["Can be used"]).toBe("2 times");
    expect(facts.Expires).toMatch(/2026/);
    // The token, then Copy, then the way out: Copy is the dialog's one primary button.
    expect(within(shown).getAllByRole("button").map((button) => button.textContent)).toEqual([
      "Copy",
      "I have stored it",
    ]);
    expect(within(shown).getByRole("button", { name: "Copy" })).toHaveClass("button-primary");
    expect(shown.querySelector("[style]")).toBeNull();
  });

  it("says once, not 1 times, for a token that can be used a single time", async () => {
    const user = userEvent.setup();
    renderApp("/leaders/eu-1/tokens", ADMIN)
      .on(LIST, reply(200, [TOKEN]))
      .on(CREATE, reply(201, { ...CREATED, max_uses: 1 }));
    const shown = await createToken(user);
    expect(within(shown).getByText("Can be used").nextElementSibling).toHaveTextContent(/^once$/);
  });

  it("shows it in a dialog with the copy button, focus on the selected token", async () => {
    const user = userEvent.setup();
    const writeText = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue(undefined);
    renderApp("/leaders/eu-1/tokens", ADMIN)
      .on(LIST, reply(200, [TOKEN]))
      .on(CREATE, reply(201, CREATED));
    const shown = await createToken(user);
    const field = within(shown).getByRole("textbox", { name: "Join token" });
    expect(field).toHaveValue(SECRET);
    expect(field).toHaveAttribute("readonly");
    expect(field).toHaveFocus();
    const input = field as HTMLInputElement;
    expect([input.selectionStart, input.selectionEnd]).toEqual([0, SECRET.length]);
    expect(shown).toHaveAccessibleDescription(/nobody can read it again, including you/);
    await user.click(within(shown).getByRole("button", { name: "Copy" }));
    expect(writeText).toHaveBeenCalledExactlyOnceWith(SECRET);
    expect(within(shown).getByText("Copied.")).toBeInTheDocument();
  });

  it("is gone from the page, React state, URL, storage, title and logs once closed", async () => {
    const user = userEvent.setup();
    const logs = spyOnLogs();
    // C3a's lint bans window.localStorage and sessionStorage in src/, tests included, so
    // watch every storage call through spies on the prototype, set before the flow starts.
    const writes = vi.spyOn(Storage.prototype, "setItem");
    const reads = vi.spyOn(Storage.prototype, "getItem");
    vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue(undefined);
    const mock = renderApp("/leaders/eu-1/tokens", ADMIN)
      .on(LIST, reply(200, [TOKEN]))
      .on(CREATE, reply(201, CREATED));
    const shown = await createToken(user);

    // While it is open the plaintext is the dialog's alone, and in none of the other places.
    expect(reactStateHolds(SECRET)).toBe(true);
    expect(window.location.href).not.toContain("sst_plaintext");
    expect(document.title).not.toContain("sst_plaintext");

    await user.click(within(shown).getByRole("button", { name: "Copy" }));
    await user.click(within(shown).getByRole("button", { name: "I have stored it" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(await screen.findByText("Join token 55555555 is created.")).toBeInTheDocument();

    // A reload of the list (the leader never returns plaintext) cannot bring it back.
    await waitFor(() => expect(mock.callsTo(LIST).length).toBeGreaterThanOrEqual(2));
    await user.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() => expect(mock.callsTo(LIST).length).toBeGreaterThanOrEqual(3));

    expect(document.documentElement.outerHTML).not.toContain(SECRET);
    expect(reactStateHolds(SECRET)).toBe(false);
    expect(window.location.href).not.toContain("sst_plaintext");
    expect(JSON.stringify(window.history.state)).not.toContain("sst_plaintext");
    expect(document.title).not.toContain("sst_plaintext");
    expect(JSON.stringify(writes.mock.calls)).not.toContain("sst_plaintext");
    expect(JSON.stringify(reads.mock.calls)).not.toContain("sst_plaintext");
    for (const log of logs) expect(JSON.stringify(log.mock.calls)).not.toContain("sst_plaintext");
    // The token went nowhere: no later request carries it.
    expect(JSON.stringify(mock.calls)).not.toContain(SECRET);
  });

  it("does not close on a first Escape: it asks, and a second Escape closes", async () => {
    const user = userEvent.setup();
    renderApp("/leaders/eu-1/tokens", ADMIN)
      .on(LIST, reply(200, []))
      .on(CREATE, reply(201, CREATED));
    const shown = await createToken(user);
    await user.keyboard("{Escape}");
    expect(screen.getByRole("dialog", { name: "Here is the join token. It is shown once." })).toBeInTheDocument();
    expect(
      within(shown).getByText(
        /The token is not shown again\. To close without copying it, press Escape again/,
      ),
    ).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(document.body.innerHTML).not.toContain(SECRET);
    expect(reactStateHolds(SECRET)).toBe(false);
  });

  it("closes on the first Escape once the person has copied it", async () => {
    const user = userEvent.setup();
    vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue(undefined);
    renderApp("/leaders/eu-1/tokens", ADMIN)
      .on(LIST, reply(200, []))
      .on(CREATE, reply(201, CREATED));
    const shown = await createToken(user);
    await user.click(within(shown).getByRole("button", { name: "Copy" }));
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("returns focus to the Create button when it closes", async () => {
    const user = userEvent.setup();
    renderApp("/leaders/eu-1/tokens", ADMIN)
      .on(LIST, reply(200, []))
      .on(CREATE, reply(201, CREATED));
    const shown = await createToken(user);
    const stored = within(shown).getByRole("button", { name: "I have stored it" });
    await user.click(stored);
    await user.click(stored);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Create join token" })).toHaveFocus(),
    );
  });

  it.each([
    [
      "is denied",
      () => vi.spyOn(navigator.clipboard, "writeText").mockRejectedValue(new Error("no")),
    ],
    [
      "is unavailable",
      () => Object.defineProperty(navigator, "clipboard", { value: undefined, configurable: true }),
    ],
  ])("says so, and leaves the text selected, when the clipboard %s", async (_name, arrange) => {
    const user = userEvent.setup();
    const logs = spyOnLogs();
    renderApp("/leaders/eu-1/tokens", ADMIN)
      .on(LIST, reply(200, []))
      .on(CREATE, reply(201, CREATED));
    const shown = await createToken(user);
    arrange();
    await user.click(within(shown).getByRole("button", { name: "Copy" }));
    const status = await within(shown).findByText("Copying did not work. Select the token and copy it yourself.");
    expect(status.closest("[role=status]")).not.toBeNull();
    const field = within(shown).getByRole("textbox", { name: "Join token" }) as HTMLInputElement;
    expect(field).toHaveValue(SECRET);
    expect(field).toHaveFocus();
    expect([field.selectionStart, field.selectionEnd]).toEqual([0, SECRET.length]);
    for (const log of logs) expect(JSON.stringify(log.mock.calls)).not.toContain(SECRET);
  });

  it("asks before 'I have stored it' closes an uncopied token, and a stray Enter does not close", async () => {
    const user = userEvent.setup();
    const mock = renderApp("/leaders/eu-1/tokens", ADMIN)
      .on(LIST, reply(200, []))
      .on(CREATE, reply(201, CREATED));
    // Submit with Enter in the form, then press Enter again as the token dialog appears.
    await user.click(await screen.findByRole("button", { name: "Create join token" }));
    const form = screen.getByRole("dialog");
    await user.click(within(form).getByRole("textbox", { name: "Pool" }));
    await user.keyboard("{Enter}");
    const shown = await screen.findByRole("dialog", { name: "Here is the join token. It is shown once." });
    await user.keyboard("{Enter}");
    expect(screen.getByRole("dialog", { name: "Here is the join token. It is shown once." })).toBeInTheDocument();
    expect(mock.callsTo(CREATE)).toHaveLength(1);
    await user.click(within(shown).getByRole("button", { name: "I have stored it" }));
    expect(screen.getByRole("dialog", { name: "Here is the join token. It is shown once." })).toBeInTheDocument();
    expect(within(shown).getByText(/not shown again/)).toBeInTheDocument();
    await user.click(within(shown).getByRole("button", { name: "I have stored it" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("sends one request for a held Enter", async () => {
    const user = userEvent.setup();
    const mock = renderApp("/leaders/eu-1/tokens", ADMIN)
      .on(LIST, reply(200, []))
      .on(CREATE, async () => {
        await new Promise((resolve) => setTimeout(resolve, 100));
        return reply(201, CREATED);
      });
    await user.click(await screen.findByRole("button", { name: "Create join token" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("textbox", { name: "Pool" }));
    await user.keyboard("{Enter>5}");
    await screen.findByRole("dialog", { name: "Here is the join token. It is shown once." });
    await user.keyboard("{/Enter}");
    expect(mock.callsTo(CREATE)).toHaveLength(1);
  });

  it("still asks on Escape after a failed copy, and shows both messages", async () => {
    const user = userEvent.setup();
    renderApp("/leaders/eu-1/tokens", ADMIN)
      .on(LIST, reply(200, []))
      .on(CREATE, reply(201, CREATED));
    const shown = await createToken(user);
    vi.spyOn(navigator.clipboard, "writeText").mockRejectedValue(new Error("no"));
    await user.click(within(shown).getByRole("button", { name: "Copy" }));
    await within(shown).findByText("Copying did not work. Select the token and copy it yourself.");
    await user.keyboard("{Escape}");
    expect(screen.getByRole("dialog", { name: "Here is the join token. It is shown once." })).toBeInTheDocument();
    expect(within(shown).getByText("Copying did not work. Select the token and copy it yourself.")).toBeVisible();
    const question = within(shown).getByText(/not shown again/);
    expect(question.closest("[role=status]")).not.toBeNull();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("cannot be dismissed while the create is in flight, then shows the token", async () => {
    const user = userEvent.setup();
    let release: () => void = () => undefined;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    renderApp("/leaders/eu-1/tokens", ADMIN)
      .on(LIST, reply(200, []))
      .on(CREATE, async () => {
        await gate;
        return reply(201, CREATED);
      });
    await user.click(await screen.findByRole("button", { name: "Create join token" }));
    const form = screen.getByRole("dialog");
    await user.click(within(form).getByRole("button", { name: "Create token" }));
    const cancel = within(form).getByRole("button", { name: "Cancel" });
    await waitFor(() => expect(cancel).toHaveAttribute("aria-disabled", "true"));
    await user.keyboard("{Escape}");
    await user.click(cancel);
    expect(
      screen.getByRole("dialog", { name: "Create a join token for eu-1" }),
    ).toBeInTheDocument();
    release();
    const shown = await screen.findByRole("dialog", { name: "Here is the join token. It is shown once." });
    expect(within(shown).getByRole("textbox", { name: "Join token" })).toHaveValue(SECRET);
  });

  it("shows two tokens in turn, each starting as not copied, and drops neither", async () => {
    const user = userEvent.setup();
    vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue(undefined);
    const second: TokenCreated = {
      ...CREATED,
      id: "88888888-8888-4888-8888-888888888888",
      token: "sst_second",
    };
    let setQueue: (update: (queue: TokenCreated[]) => TokenCreated[]) => void = () => undefined;
    function Harness() {
      const [queue, set] = useState<TokenCreated[]>([CREATED]);
      setQueue = set;
      return (
        <TokenReveal queue={queue} onDismiss={(t) => set((q) => q.filter((x) => x.id !== t.id))} />
      );
    }
    render(<Harness />);
    const first = await screen.findByRole("dialog", { name: "Here is the join token. It is shown once." });
    await user.click(within(first).getByRole("button", { name: "Copy" }));
    expect(within(first).getByText("Copied.")).toBeInTheDocument();
    // A second token arrives while the first is showing: the first stays.
    act(() => setQueue((q) => [...q, second]));
    expect(within(first).getByRole("textbox", { name: "Join token" })).toHaveValue(SECRET);
    expect(reactStateHolds("sst_second")).toBe(true);
    await user.click(within(first).getByRole("button", { name: "I have stored it" }));
    const next = await screen.findByRole("dialog", { name: "Here is the join token. It is shown once." });
    expect(within(next).getByRole("textbox", { name: "Join token" })).toHaveValue("sst_second");
    expect(within(next).queryByText("Copied.")).not.toBeInTheDocument();
    expect(document.body.innerHTML).not.toContain(SECRET);
    await user.click(within(next).getByRole("button", { name: "I have stored it" }));
    expect(screen.getByText(/not shown again/)).toBeInTheDocument();
  });
});
