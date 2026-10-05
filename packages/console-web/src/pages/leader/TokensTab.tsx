import { useEffect, useId, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { api, leaderPath } from "../../api/client";
import { can } from "../../api/roles";
import type { TokenCreated, TokenIn, TokenOut } from "../../api/types";
import { useDialogAction } from "../../app/useDialogAction";
import { ActionButton } from "../../components/ActionButton";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { Dialog } from "../../components/Dialog";
import { ErrorPanel } from "../../components/ErrorPanel";
import { formatCount, formatTime, withUnit } from "../../lib/format";
import {
  ActionNotice,
  ReadState,
  RefreshButton,
  shortId,
  useLeaderRead,
  type TabProps,
} from "./common";
import { useRowFocus } from "./rowFocus";

const DAY_S = 86_400;
/** The leader's NAME_PATTERN (admin_models.py), for the browser's own check. */
const NAME_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$/;

type TokenField = "pool" | "days" | "uses";
type TokenErrors = Partial<Record<TokenField, string>>;

const FIELD_ORDER: TokenField[] = ["pool", "days", "uses"];

function wholeNumber(text: string, min: number, max: number): number | null {
  if (!/^\d+$/.test(text.trim())) return null;
  const value = Number(text);
  return value >= min && value <= max ? value : null;
}

/** The leader's TokenIn rules (pool pattern, 60 s to 90 days, 1 to 10000 uses), beside the field. */
function validateToken(pool: string, days: string, uses: string): TokenErrors {
  const errors: TokenErrors = {};
  if (!NAME_PATTERN.test(pool.trim())) {
    errors.pool =
      "Pool: start with a letter or digit, then letters, digits, dots, underscores or hyphens, " +
      "up to 100 characters.";
  }
  if (wholeNumber(days, 1, 90) === null) errors.days = "Enter a whole number of days from 1 to 90.";
  if (wholeNumber(uses, 1, 10_000) === null)
    errors.uses = "Enter a whole number of uses from 1 to 10000.";
  return errors;
}

/** A held Enter repeats: only its first press may submit, or it would mint tokens. */
function ignoreRepeatedEnter(event: KeyboardEvent<HTMLInputElement>) {
  if (event.key === "Enter" && event.repeat) event.preventDefault();
}

function NumberField({
  label,
  field,
  value,
  min,
  max,
  error,
  onChange,
}: {
  label: string;
  field: TokenField;
  value: string;
  min: number;
  max: number;
  error: string | undefined;
  onChange: (value: string) => void;
}) {
  const errorId = useId();
  return (
    <div>
      <label className="field">
        {label}
        <input
          name={field}
          type="number"
          min={min}
          max={max}
          step={1}
          value={value}
          aria-required
          aria-invalid={error === undefined ? undefined : true}
          aria-describedby={error === undefined ? undefined : errorId}
          onKeyDown={ignoreRepeatedEnter}
          onChange={(event) => onChange(event.target.value)}
        />
      </label>
      {error !== undefined && (
        <p id={errorId} className="error-text" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

function CreateTokenDialog({
  leaderName,
  onClose,
  onCreated,
}: {
  leaderName: string;
  onClose: () => void;
  onCreated: (created: TokenCreated) => void;
}) {
  const [pool, setPool] = useState("default");
  const [days, setDays] = useState("7");
  const [uses, setUses] = useState("1");
  const [errors, setErrors] = useState<TokenErrors>({});
  const action = useDialogAction(onClose);
  const formRef = useRef<HTMLFormElement>(null);
  const poolId = useId();
  const poolErrorId = useId();

  // While the request is in flight the dialog cannot be dismissed: a create that was
  // "cancelled" would still happen, and its token must be shown, never lost.
  const dismiss = () => {
    if (!action.busy) action.close();
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (action.busy) return;
    const found = validateToken(pool, days, uses);
    setErrors(found);
    const first = FIELD_ORDER.find((field) => found[field] !== undefined);
    if (first !== undefined) {
      const control = formRef.current?.elements.namedItem(first);
      if (control instanceof HTMLElement) control.focus();
      return;
    }
    const body: TokenIn = {
      pool: pool.trim(),
      expires_in_seconds: Number(days) * DAY_S,
      max_uses: Number(uses),
    };
    // The answer goes straight from the request to the one-time dialog: it is held in no
    // variable of this component. onCreated also closes this form, in the same render.
    void action.submit(async () => {
      onCreated(await api.post<TokenCreated>(leaderPath(leaderName, "tokens"), body));
    });
  };

  return (
    <Dialog
      title={`Create a join token for ${leaderName}`}
      onClose={dismiss}
      dismissable={!action.busy}
    >
      <form ref={formRef} className="form-grid" noValidate onSubmit={submit}>
        <div>
          <div className="field">
            <label htmlFor={poolId}>Pool</label>
            <input
              id={poolId}
              name="pool"
              value={pool}
              aria-required
              aria-invalid={errors.pool === undefined ? undefined : true}
              aria-describedby={errors.pool === undefined ? undefined : poolErrorId}
              onKeyDown={ignoreRepeatedEnter}
              onChange={(event) => setPool(event.target.value)}
            />
          </div>
          {errors.pool !== undefined && (
            <p id={poolErrorId} className="error-text" role="alert">
              {errors.pool}
            </p>
          )}
        </div>
        <NumberField
          label="Expires after (days, 1 to 90)"
          field="days"
          min={1}
          max={90}
          value={days}
          error={errors.days}
          onChange={setDays}
        />
        <NumberField
          label="Uses (1 to 10000)"
          field="uses"
          min={1}
          max={10000}
          value={uses}
          error={errors.uses}
          onChange={setUses}
        />
        {action.error !== null && <ErrorPanel error={action.error} />}
        <div className="dialog-buttons">
          <button
            type="button"
            className="button"
            aria-disabled={action.busy || undefined}
            onClick={dismiss}
          >
            Cancel
          </button>
          <button
            type="submit"
            className="button button-primary"
            aria-disabled={action.busy || undefined}
          >
            Create token
          </button>
        </div>
      </form>
    </Dialog>
  );
}

/**
 * Selects the whole token with the caret at its start, so the field keeps showing it from its
 * first character (a selection made forwards scrolls to its end, and cut the start off).
 */
function selectWhole(field: HTMLTextAreaElement | null): void {
  if (field === null) return;
  field.setSelectionRange(0, field.value.length, "backward");
  field.scrollTop = 0;
  field.scrollLeft = 0;
}

/**
 * The join token's plaintext, shown once. It lives only in this dialog's props: the parent
 * drops it when the dialog closes, and nothing writes it to the URL, storage, the title or a
 * log. It cannot be dismissed by accident: until the token is copied, Escape and "I have
 * stored it" both ask first and close on the second go; focus starts on the token itself.
 * The token is 47 characters: its field takes the dialog's whole width, where it fits on one
 * line, and is a read-only box that wraps where it does not, so every character is in view at
 * every width with nothing to scroll. Copy sits beneath it, beside the line that says whether
 * it has been copied.
 * Layout: docs/superpowers/design/TokenDialog.dc.html.
 */
export function TokenCreatedDialog({
  created,
  onClose,
}: {
  created: TokenCreated;
  onClose: () => void;
}) {
  const [copied, setCopied] = useState<"no" | "yes" | "failed">("no");
  const [asked, setAsked] = useState(false);
  const tokenRef = useRef<HTMLTextAreaElement>(null);
  const warningId = useId();
  const tokenId = useId();

  // Focus starts on the token, selected: a stray Enter then does nothing, and a copy by hand
  // is one keystroke. (Dialog focuses its first control; this runs after it.)
  useEffect(() => {
    tokenRef.current?.focus();
    selectWhole(tokenRef.current);
  }, []);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(created.token);
      setCopied("yes");
    } catch {
      // Unavailable or denied: leave the text selected so the person can copy it by hand.
      setCopied("failed");
      tokenRef.current?.focus();
      selectWhole(tokenRef.current);
    }
  };

  const requestClose = () => {
    if (copied === "yes" || asked) onClose();
    else setAsked(true);
  };

  let status = "Not copied yet.";
  if (copied === "yes") status = "Copied.";
  else if (copied === "failed") status = "Copying did not work. Select the token and copy it yourself.";
  // The question is its own announcement, shown beside a failure text, never replaced by it.
  const question = asked
    ? "The token is not shown again. To close without copying it, press Escape again or " +
      "choose I have stored it again."
    : "";

  return (
    <Dialog
      title="Here is the join token. It is shown once."
      onClose={requestClose}
      describedBy={warningId}
    >
      <p id={warningId} className="muted">
        Copy it now and give it to the machine that will join the <strong>{created.pool}</strong>{" "}
        pool. After you close this, nobody can read it again, including you.
      </p>
      <label className="field" htmlFor={tokenId}>
        Join token
      </label>
      <textarea
        id={tokenId}
        ref={tokenRef}
        className="mono token-field"
        readOnly
        rows={1}
        autoComplete="off"
        spellCheck={false}
        value={created.token}
        onFocus={(event) => selectWhole(event.target)}
      />
      <div className="token-row">
        <button type="button" className="button button-primary" onClick={() => void copy()}>
          Copy
        </button>
        <p className="token-status" role="status">
          {status}
        </p>
      </div>
      <p className="token-status" role="status">
        {question}
      </p>
      <dl className="fact-row">
        <div>
          <dt>Pool</dt>
          <dd>{created.pool}</dd>
        </div>
        <div>
          <dt>Can be used</dt>
          <dd>{created.max_uses === 1 ? "once" : withUnit(formatCount(created.max_uses), "times")}</dd>
        </div>
        <div>
          <dt>Expires</dt>
          <dd>{formatTime(created.expires_at)}</dd>
        </div>
      </dl>
      <div className="dialog-buttons">
        <button type="button" className="button" onClick={requestClose}>
          I have stored it
        </button>
      </div>
    </Dialog>
  );
}

/**
 * Shows created tokens one at a time, oldest first. Each gets its own dialog (keyed by the
 * token id, so its "copied" state starts fresh), and a token that arrives while another is
 * showing waits its turn: none is ever dropped or overwritten.
 */
export function TokenReveal({
  queue,
  onDismiss,
}: {
  queue: TokenCreated[];
  onDismiss: (token: TokenCreated) => void;
}) {
  const head = queue[0];
  if (head === undefined) return null;
  return <TokenCreatedDialog key={head.id} created={head} onClose={() => onDismiss(head)} />;
}

function tokenState(token: TokenOut, now: number): string {
  if (token.revoked) return "Revoked";
  if (Date.parse(token.expires_at) <= now) return "Expired";
  if (token.uses >= token.max_uses) return "Used up";
  return "Can be used";
}

function TokenList({ leader }: TabProps) {
  const read = useLeaderRead<TokenOut[]>(leader.name, "tokens");
  const [creating, setCreating] = useState(false);
  // Tokens just made, waiting to be shown once each. The plaintext lives here and in the
  // dialog's props only, and leaves with the dismissal.
  const [created, setCreated] = useState<TokenCreated[]>([]);
  const [revoking, setRevoking] = useState<TokenOut | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const rows = useRowFocus(read, setNotice);

  return (
    <div {...rows.props}>
      <div className="section-head">
        <ActionButton held={leader.role} action="tokens.create" primary onClick={() => setCreating(true)}>
          Create join token
        </ActionButton>
        <RefreshButton read={read} />
      </div>
      <ActionNotice message={notice} />
      <ReadState read={read} what="join tokens">
        {(tokens) => {
          // "Now" is when the list was read: render stays pure, and the state column
          // describes the list as the leader returned it.
          const now = read.updatedAt ?? 0;
          return tokens.length === 0 ? (
            <p>No join tokens yet.</p>
          ) : (
            <div className="table-scroll" role="region" aria-label="Join token list" tabIndex={0}>
              <table className="medium">
                <thead>
                  <tr>
                    <th scope="col">Token</th>
                    <th scope="col">Pool</th>
                    <th scope="col">State</th>
                    <th scope="col" className="num">Used</th>
                    <th scope="col">Expires</th>
                    <th scope="col">Made by</th>
                    <th scope="col">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {tokens.map((token) => (
                    <tr key={token.id} data-row={token.id}>
                      <th scope="row">
                        <code>{shortId(token.id)}</code>
                      </th>
                      <td className="nowrap">{token.pool}</td>
                      <td className="nowrap">{tokenState(token, now)}</td>
                      <td className="num">
                        {token.uses} of {token.max_uses}
                      </td>
                      <td>{formatTime(token.expires_at)}</td>
                      <td className="long">{token.created_by}</td>
                      <td className="actions">
                        {!token.revoked && (
                          <ActionButton
                            held={leader.role}
                            action="tokens.revoke"
                            danger
                            onClick={() => setRevoking(token)}
                            name={`Revoke token ${shortId(token.id)}`}
                          >
                            Revoke
                          </ActionButton>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          );
        }}
      </ReadState>
      {creating && (
        <CreateTokenDialog
          leaderName={leader.name}
          onClose={() => setCreating(false)}
          onCreated={(token) => {
            // One render: the form goes and the one-time dialog comes, focus returning to
            // the Create button first so the one-time dialog's own return target is right.
            setCreating(false);
            setCreated((queue) =>
              queue.some((t) => t.id === token.id) ? queue : [...queue, token],
            );
          }}
        />
      )}
      <TokenReveal
        queue={created}
        onDismiss={(token) => {
          setNotice(`Join token ${shortId(token.id)} is created.`);
          setCreated((queue) => queue.filter((t) => t.id !== token.id));
          read.refresh();
        }}
      />
      {revoking !== null && (
        <ConfirmDialog
          title={`Revoke join token ${shortId(revoking.id)}?`}
          message="No new follower can join with it. Followers that already joined carry on."
          confirmLabel="Revoke token"
          onClose={() => setRevoking(null)}
          onConfirm={async () => {
            setNotice(null);
            await api.post(leaderPath(leader.name, `tokens/${revoking.id}/revoke`));
            rows.done(`Join token ${shortId(revoking.id)} is revoked.`);
          }}
        />
      )}
    </div>
  );
}

export function TokensTab({ leader }: TabProps) {
  // Listing join tokens needs admin on the leader; below it the console would refuse, so
  // the tab says so instead of asking.
  if (!can(leader.role, "tokens.view")) {
    return (
      <p>
        Join tokens need the admin role on {leader.name}. Your role is {leader.role}.
      </p>
    );
  }
  return <TokenList leader={leader} />;
}
