import { useId, useState, type FormEvent } from "react";
import { api } from "../../api/client";
import { OwnRefusal } from "../../api/errors";
import type { CredentialIn, LeaderEdit, LeaderIn, LeaderOut } from "../../api/types";
import { useDialogAction } from "../../app/useDialogAction";
import { BreakPath } from "../../components/BreakPath";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { Dialog } from "../../components/Dialog";
import { ErrorPanel } from "../../components/ErrorPanel";
import { countOf, formatTime } from "../../lib/format";
import { ActionNotice, ReadState } from "../leader/common";
import {
  labelsAreValid,
  labelsText,
  parseLabels,
  sameLabels,
  useAdminList,
  withSecret,
} from "./AdminFrame";
import { useRowFocus } from "../leader/rowFocus";

const LABELS_HELP =
  "One key=value per line, such as region=eu. Keys are lowercase letters, digits and . _ - ; " +
  "values are printable characters without spaces; at most 32 labels.";
const CREDENTIAL_HELP =
  "The 43-character value that `swarmscribe-admin console create` printed on the leader. " +
  "It is never shown again.";
// The help is written with `backticks` around the command; the field shows them as code, and
// the message of a refusal (plain text) shows the same words without them.
const CREDENTIAL_PLAIN = CREDENTIAL_HELP.replace(/`/g, "");
const NAME_HELP = "Letters, digits, . _ - ; starts with a letter or digit; at most 100.";

const NAME = /^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$/;
const CREDENTIAL = /^[A-Za-z0-9_-]{43}$/;
const MAX_URL = 2000;

const leaderApi = (name: string) => `/api/admin/leaders/${encodeURIComponent(name)}`;

/**
 * The registry's own rules, checked before anything is sent so a mistake is heard at once and
 * the credential is not sent to be refused. They use the console's codes, so the person reads
 * the same title either way, and say the rule in our own words beneath it (an OwnRefusal: its
 * text is shown, where a server's never is). The server stays the authority (it also refuses
 * loopback and metadata addresses). None of these messages contains what was typed.
 */
function refuse(code: string, message: string): never {
  throw new OwnRefusal(code, message);
}

function checkName(name: string): void {
  if (!NAME.test(name)) refuse("invalid_name", NAME_HELP);
}

function checkAddress(address: string): void {
  if (!/^https:\/\/[^/\s]/i.test(address) || address.length > MAX_URL) {
    refuse("invalid_url", "A leader's address starts with https://.");
  }
}

function checkCredential(credential: string): void {
  if (!CREDENTIAL.test(credential)) refuse("invalid_credential", CREDENTIAL_PLAIN);
}

function checkLabels(text: string): Record<string, string> {
  const parsed = parseLabels(text);
  if (parsed === null || !labelsAreValid(parsed)) refuse("invalid_labels", LABELS_HELP);
  return parsed;
}

/** The address as the registry stores it: lowercase host, no default port, no trailing slash. */
function normalizedAddress(address: string): string {
  const trimmed = address.trim();
  try {
    const url = new URL(trimmed);
    if (url.protocol === "https:") return `https://${url.host}${url.pathname.replace(/\/+$/, "")}`;
  } catch {
    // Not a URL yet (still being typed): compare it as typed.
  }
  return trimmed.replace(/\/+$/, "");
}

function CredentialField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
}) {
  const helpId = useId();
  return (
    <div className="field">
      <label className="field">
        {label}
        <input
          type="password"
          autoComplete="off"
          spellCheck={false}
          required
          value={value}
          onChange={(e) => onChange(e.target.value)}
          aria-describedby={helpId}
        />
      </label>
      <span id={helpId} className="field-help">
        {CREDENTIAL_HELP.split("`").map((part, i) =>
          i % 2 === 1 ? <code key={i}>{part}</code> : part,
        )}
      </span>
    </div>
  );
}

function LabelsField({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const helpId = useId();
  return (
    <div className="field">
      <label className="field">
        Labels
        <textarea
          rows={3}
          spellCheck={false}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          aria-describedby={helpId}
        />
      </label>
      <span id={helpId} className="field-help">
        {LABELS_HELP}
      </span>
    </div>
  );
}

function DialogButtons({
  busy,
  submitLabel,
  onCancel,
}: {
  busy: boolean;
  submitLabel: string;
  onCancel: () => void;
}) {
  return (
    <div className="dialog-buttons">
      <button type="submit" className="button button-primary" aria-disabled={busy || undefined}>
        {submitLabel}
      </button>
      <button type="button" className="button" onClick={onCancel}>
        Cancel
      </button>
    </div>
  );
}

function AddLeaderDialog({
  onClose,
  onDone,
}: {
  onClose: () => void;
  onDone: (name: string) => void;
}) {
  const [name, setName] = useState("");
  const [baseUrl, setBaseUrl] = useState("https://");
  const [labels, setLabels] = useState("");
  const [credential, setCredential] = useState("");
  const [enabled, setEnabled] = useState(true);
  const action = useDialogAction(onClose);
  const submit = (event: FormEvent) => {
    event.preventDefault();
    void action.submit(async () => {
      const secret = credential.trim();
      checkName(name.trim());
      checkAddress(baseUrl.trim());
      const parsed = checkLabels(labels);
      checkCredential(secret);
      const body: LeaderIn = {
        name: name.trim(),
        base_url: baseUrl.trim(),
        labels: parsed,
        credential: secret,
        enabled,
      };
      await withSecret(secret, () => api.post<LeaderOut>("/api/admin/leaders", body));
      setCredential("");
      onDone(body.name);
    });
  };
  return (
    <Dialog title="Add a leader" onClose={action.close}>
      <form className="form-grid" noValidate onSubmit={submit}>
        <label className="field">
          Name
          <input required value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <label className="field">
          Address (https://)
          <input
            type="url"
            required
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
          />
        </label>
        <LabelsField value={labels} onChange={setLabels} />
        <CredentialField label="Console credential" value={credential} onChange={setCredential} />
        <label className="field-check">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(e) => setEnabled(e.target.checked)}
          />
          Switched on
        </label>
        {action.error !== null && <ErrorPanel error={action.error} />}
        <DialogButtons busy={action.busy} submitLabel="Add this leader" onCancel={action.close} />
      </form>
    </Dialog>
  );
}

/**
 * Only changed fields are sent. A new address carries the credential for it (the registry
 * refuses one without, and refuses a credential without a new address: that is rotation).
 */
export function editBody(
  leader: LeaderOut,
  form: { baseUrl: string; labels: Record<string, string>; enabled: boolean; credential: string },
): LeaderEdit {
  const body: LeaderEdit = {};
  if (normalizedAddress(form.baseUrl) !== normalizedAddress(leader.base_url)) {
    body.base_url = form.baseUrl.trim();
    body.credential = form.credential;
  }
  if (!sameLabels(form.labels, leader.labels)) body.labels = form.labels;
  if (form.enabled !== leader.enabled) body.enabled = form.enabled;
  return body;
}

function EditLeaderDialog({
  leader,
  onClose,
  onDone,
}: {
  leader: LeaderOut;
  onClose: () => void;
  onDone: () => void;
}) {
  const [baseUrl, setBaseUrl] = useState(leader.base_url);
  const [labels, setLabels] = useState(labelsText(leader.labels));
  const [enabled, setEnabled] = useState(leader.enabled);
  const [credential, setCredential] = useState("");
  const action = useDialogAction(onClose);
  const urlChanged = normalizedAddress(baseUrl) !== normalizedAddress(leader.base_url);

  const changeUrl = (value: string) => {
    setBaseUrl(value);
    // Back to the address it had: the credential typed for another address goes too.
    if (normalizedAddress(value) === normalizedAddress(leader.base_url)) setCredential("");
  };
  const submit = (event: FormEvent) => {
    event.preventDefault();
    void action.submit(async () => {
      const parsed = checkLabels(labels);
      const secret = credential.trim();
      if (urlChanged) {
        checkAddress(baseUrl.trim());
        checkCredential(secret);
      }
      const body = editBody(leader, { baseUrl, labels: parsed, enabled, credential: secret });
      if (Object.keys(body).length > 0) {
        await withSecret(secret, () => api.patch<LeaderOut>(leaderApi(leader.name), body));
      }
      setCredential("");
      onDone();
    });
  };
  return (
    <Dialog title={`Edit ${leader.name}`} onClose={action.close}>
      <form className="form-grid" noValidate onSubmit={submit}>
        <label className="field">
          Address (https://)
          <input type="url" required value={baseUrl} onChange={(e) => changeUrl(e.target.value)} />
        </label>
        {urlChanged && (
          <CredentialField
            label="Console credential for the new address"
            value={credential}
            onChange={setCredential}
          />
        )}
        <LabelsField value={labels} onChange={setLabels} />
        <label className="field-check">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(e) => setEnabled(e.target.checked)}
          />
          Switched on
        </label>
        {action.error !== null && <ErrorPanel error={action.error} />}
        <DialogButtons busy={action.busy} submitLabel="Save" onCancel={action.close} />
      </form>
    </Dialog>
  );
}

/** Replacing a credential alone is its own call (PUT), never an edit (the registry says use_rotate). */
function RotateDialog({
  leader,
  onClose,
  onDone,
}: {
  leader: LeaderOut;
  onClose: () => void;
  onDone: () => void;
}) {
  const [credential, setCredential] = useState("");
  const action = useDialogAction(onClose);
  const submit = (event: FormEvent) => {
    event.preventDefault();
    void action.submit(async () => {
      const body: CredentialIn = { credential: credential.trim() };
      checkCredential(body.credential);
      await withSecret(body.credential, () =>
        api.put<LeaderOut>(`${leaderApi(leader.name)}/credential`, body),
      );
      setCredential("");
      onDone();
    });
  };
  return (
    <Dialog title={`Replace the credential for ${leader.name}`} onClose={action.close}>
      <form className="form-grid" noValidate onSubmit={submit}>
        <p>
          Make a new console credential on the leader first, paste it here, then revoke the old
          one on the leader.
        </p>
        <CredentialField
          label="New console credential"
          value={credential}
          onChange={setCredential}
        />
        {action.error !== null && <ErrorPanel error={action.error} />}
        <DialogButtons
          busy={action.busy}
          submitLabel="Replace credential"
          onCancel={action.close}
        />
      </form>
    </Dialog>
  );
}

type Open = { kind: "add" } | { kind: "edit" | "rotate" | "remove"; leader: LeaderOut } | null;

/** The Leaders section of Administration (pages/admin/AdminPage.tsx frames it). */
export function AdminLeadersSection() {
  const read = useAdminList<LeaderOut>("/api/admin/leaders");
  const [open, setOpen] = useState<Open>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const rows = useRowFocus(read, setNotice);
  const calloutId = useId();
  const close = () => setOpen(null);
  const done = (message: string) => {
    setNotice(message);
    read.refresh();
  };

  return (
    <div {...rows.props}>
      <div className="section-head">
        <h2>{read.data === undefined ? "Leaders" : countOf(read.data.length, "leader")}</h2>
        <button
          type="button"
          className="button button-primary"
          onClick={() => setOpen({ kind: "add" })}
        >
          Add a leader
        </button>
      </div>
      <ActionNotice message={notice} />
      <ReadState read={read} what="leaders">
        {(leaders) =>
          leaders.length === 0 ? (
            <p>This console talks to no leaders yet.</p>
          ) : (
            <div className="table-scroll" role="region" aria-label="Registered leaders" tabIndex={0}>
              <table className="wide">
                <thead>
                  <tr>
                    <th scope="col">Leader</th>
                    <th scope="col">Address</th>
                    <th scope="col">Labels</th>
                    <th scope="col">State</th>
                    <th scope="col">Credential</th>
                    <th scope="col">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {leaders.map((leader) => (
                    <tr key={leader.name} data-row={leader.name}>
                      <th scope="row">{leader.name}</th>
                      <td className="mono long">
                        <BreakPath text={leader.base_url} />
                      </td>
                      <td className="mono long label-lines">{labelsText(leader.labels) || "–"}</td>
                      <td className="nowrap">{leader.enabled ? "On" : "Switched off"}</td>
                      <td>
                        {leader.credential_revoked ? (
                          <span className="badge badge-bad">Revoked by the leader</span>
                        ) : (
                          <span>
                            <span className="nowrap">Set {formatTime(leader.credential_updated_at)}</span>{" "}
                            <span className="by-line">by {leader.credential_updated_by}</span>
                          </span>
                        )}
                      </td>
                      <td className="actions">
                        <button
                          type="button"
                          className="button"
                          onClick={() => setOpen({ kind: "edit", leader })}
                          aria-label={`Edit ${leader.name}`}
                        >
                          Edit
                        </button>
                        <button
                          type="button"
                          className="button"
                          onClick={() => setOpen({ kind: "rotate", leader })}
                          aria-label={`Replace credential for ${leader.name}`}
                        >
                          Replace credential
                        </button>
                        <button
                          type="button"
                          className="button button-danger"
                          onClick={() => setOpen({ kind: "remove", leader })}
                          aria-label={`Remove ${leader.name}`}
                        >
                          Remove
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        }
      </ReadState>
      {open?.kind === "add" && (
        <AddLeaderDialog onClose={close} onDone={(name) => done(`Leader ${name} is added.`)} />
      )}
      {open?.kind === "edit" && (
        <EditLeaderDialog
          leader={open.leader}
          onClose={close}
          onDone={() => done(`Leader ${open.leader.name} is saved.`)}
        />
      )}
      {open?.kind === "rotate" && (
        <RotateDialog
          leader={open.leader}
          onClose={close}
          onDone={() => done(`The credential for ${open.leader.name} is replaced.`)}
        />
      )}
      {open?.kind === "remove" && (
        <ConfirmDialog
          title={`Remove ${open.leader.name}?`}
          message="The console forgets this leader, its history and the roles given on it by name. The leader itself is not changed: revoke the console's credential there too."
          confirmLabel="Remove leader"
          onClose={close}
          onConfirm={async () => {
            await api.del(leaderApi(open.leader.name));
            rows.done(`Leader ${open.leader.name} is removed.`);
          }}
        />
      )}
      <section className="sheet callout" aria-labelledby={calloutId}>
        <h2 id={calloutId}>Adding a leader takes two steps</h2>
        <ol>
          <li>
            On the leader, an admin runs <code>swarmscribe-admin console create</code> and copies the
            credential it prints.
          </li>
          <li>
            Here, choose <strong>Add a leader</strong> and paste the address and that credential. The
            credential is never shown again.
          </li>
        </ol>
      </section>
    </div>
  );
}

