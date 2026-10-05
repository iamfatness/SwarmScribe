import { useId, useState, type FormEvent } from "react";
import { api } from "../../api/client";
import type { GrantIn, GrantOut, PrincipalKind, Role } from "../../api/types";
import { useDialogAction } from "../../app/useDialogAction";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { ErrorPanel } from "../../components/ErrorPanel";
import { formatTime } from "../../lib/format";
import { ActionNotice, ReadState } from "../leader/common";
import { useAdminList } from "./AdminFrame";
import { PrincipalFields } from "./PrincipalFields";
import { useRowFocus } from "../leader/rowFocus";

const noop = () => undefined;

function AddGrantForm({ onDone }: { onDone: (grant: GrantOut) => void }) {
  const [role, setRole] = useState<Role>("viewer");
  const [scope, setScope] = useState("all");
  const [kind, setKind] = useState<PrincipalKind>("entra_group");
  const [principal, setPrincipal] = useState("");
  const scopeHelp = useId();
  // Not a dialog, but the same guard against a second submit while one is in flight.
  const action = useDialogAction(noop);
  const submit = (event: FormEvent) => {
    event.preventDefault();
    const body: GrantIn = {
      role,
      scope: scope.trim(),
      principal_kind: kind,
      principal: principal.trim(),
    };
    void action.submit(async () => {
      const grant = await api.post<GrantOut>("/api/admin/grants", body);
      setPrincipal("");
      onDone(grant);
    });
  };
  return (
    <form className="form-grid form-panel" aria-label="Give a role" onSubmit={submit}>
      <h2>Give a role</h2>
      <label className="field">
        Role
        <select value={role} onChange={(e) => setRole(e.target.value as Role)}>
          <option value="viewer">viewer</option>
          <option value="operator">operator</option>
          <option value="admin">admin</option>
        </select>
      </label>
      <div className="field">
        <label className="field">
          On which leaders
          <input
            required
            autoComplete="off"
            spellCheck={false}
            value={scope}
            onChange={(e) => setScope(e.target.value)}
            aria-describedby={scopeHelp}
          />
        </label>
        <span id={scopeHelp} className="field-help">
          Write all, leader:&lt;name&gt; or label:&lt;key&gt;=&lt;value&gt;.
        </span>
      </div>
      <PrincipalFields
        kind={kind}
        principal={principal}
        onKind={setKind}
        onPrincipal={setPrincipal}
      />
      {action.error !== null && <ErrorPanel error={action.error} />}
      <div className="dialog-buttons">
        <button
          type="submit"
          className="button button-primary"
          aria-disabled={action.busy || undefined}
        >
          Give the role
        </button>
      </div>
    </form>
  );
}

/** The "Who can do what" section of Administration (pages/admin/AdminPage.tsx frames it). */
export function AdminGrantsSection() {
  const read = useAdminList<GrantOut>("/api/admin/grants");
  const [removing, setRemoving] = useState<GrantOut | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const rows = useRowFocus(read, setNotice);

  return (
    <div {...rows.props}>
      <div className="section-head">
        <h2>Who can do what</h2>
      </div>
      <p className="page-sub">
        A person's role on a leader is the highest one given to them that covers it. Giving or
        removing a role takes effect at once. Group membership is read when a person signs in, so
        a change to a group shows the next time they do.
      </p>
      <div className="explain">
        <p>
          A viewer can look. An operator can also try jobs again, cancel them, scan a location and
          wind followers down. An admin can also add and switch locations, revoke followers and
          make join tokens.
        </p>
        <p>
          A role covers leaders in one of three ways: <code>all</code> for every leader,{" "}
          <code>leader:eu-1</code> for one leader by name, or <code>label:region=eu</code> for every
          leader with that label. It is given to an Entra ID group, a Google group, one person by
          email, or everyone at a domain.
        </p>
      </div>
      <ActionNotice message={notice} />
      <ReadState read={read} what="the roles">
        {(grants) =>
          grants.length === 0 ? (
            <p>Nobody has been given a role yet.</p>
          ) : (
            <div className="table-scroll" role="region" aria-label="Grants" tabIndex={0}>
              <table className="medium">
                <thead>
                  <tr>
                    <th scope="col">Who</th>
                    <th scope="col">Role</th>
                    <th scope="col">On which leaders</th>
                    <th scope="col">Given</th>
                    <th scope="col">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {grants.map((grant) => (
                    <tr key={grant.id} data-row={grant.id}>
                      <th scope="row" className="mono long ident">
                        {grant.principal_kind}:{grant.principal}
                      </th>
                      <td className="nowrap">{grant.role}</td>
                      <td className="mono long ident">{grant.scope}</td>
                      <td>
                        {formatTime(grant.created_at)} by {grant.created_by}
                      </td>
                      <td className="actions">
                        <button
                          type="button"
                          className="button button-danger"
                          onClick={() => setRemoving(grant)}
                          aria-label={`Remove ${grant.role} on ${grant.scope} from ${grant.principal_kind}:${grant.principal}`}
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
      <AddGrantForm
        onDone={(grant) => {
          setNotice(
            `${grant.principal_kind}:${grant.principal} is now ${grant.role} on ${grant.scope}.`,
          );
          read.refresh();
        }}
      />
      {removing !== null && (
        <ConfirmDialog
          title="Remove this role?"
          message={`${removing.principal_kind}:${removing.principal} stops being ${removing.role} on ${removing.scope} at once.`}
          confirmLabel="Remove the role"
          onClose={() => setRemoving(null)}
          onConfirm={async () => {
            await api.del(`/api/admin/grants/${encodeURIComponent(removing.id)}`);
            rows.done("The role is removed.");
          }}
        />
      )}
    </div>
  );
}

