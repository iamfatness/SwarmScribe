import { useId, useState, type FormEvent } from "react";
import { api } from "../../api/client";
import type { GrantIn, GrantOut, PrincipalKind, Role } from "../../api/types";
import { useDialogAction } from "../../app/useDialogAction";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { ErrorPanel } from "../../components/ErrorPanel";
import { formatTime } from "../../lib/format";
import { ActionNotice, ReadState } from "../leader/common";
import { AdminFrame, useAdminList } from "./AdminFrame";
import { PrincipalFields } from "./PrincipalFields";

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
    <form className="form-grid form-panel" aria-label="Add a grant" onSubmit={submit}>
      <h2>Add a grant</h2>
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
          Scope
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
          all, leader:&lt;name&gt; or label:&lt;key&gt;=&lt;value&gt;
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
          Add grant
        </button>
      </div>
    </form>
  );
}

function GrantsContent() {
  const read = useAdminList<GrantOut>("/api/admin/grants");
  const [removing, setRemoving] = useState<GrantOut | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  return (
    <>
      <p className="muted">
        A person's role on a leader is the highest grant whose scope matches it. Changes apply at
        their next sign-in.
      </p>
      <ActionNotice message={notice} />
      <ReadState read={read} what="grants">
        {(grants) =>
          grants.length === 0 ? (
            <p>No grants.</p>
          ) : (
            <div className="table-scroll" role="region" aria-label="Grants" tabIndex={0}>
              <table>
                <thead>
                  <tr>
                    <th scope="col">Principal</th>
                    <th scope="col">Role</th>
                    <th scope="col">Scope</th>
                    <th scope="col">Added</th>
                    <th scope="col">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {grants.map((grant) => (
                    <tr key={grant.id}>
                      <th scope="row" className="mono">
                        {grant.principal_kind}:{grant.principal}
                      </th>
                      <td>{grant.role}</td>
                      <td className="mono">{grant.scope}</td>
                      <td>
                        {formatTime(grant.created_at)} by {grant.created_by}
                      </td>
                      <td className="actions">
                        <button
                          type="button"
                          className="button button-danger"
                          onClick={() => setRemoving(grant)}
                          aria-label={`Remove grant: ${grant.role} on ${grant.scope} for ${grant.principal_kind}:${grant.principal}`}
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
            `Grant added: ${grant.role} on ${grant.scope} for ${grant.principal_kind}:${grant.principal}.`,
          );
          read.refresh();
        }}
      />
      {removing !== null && (
        <ConfirmDialog
          title="Remove this grant?"
          message={`${removing.principal_kind}:${removing.principal} loses ${removing.role} on ${removing.scope} at their next sign-in.`}
          confirmLabel="Remove grant"
          onClose={() => setRemoving(null)}
          onConfirm={async () => {
            await api.del(`/api/admin/grants/${encodeURIComponent(removing.id)}`);
            setNotice("Grant removed.");
            read.refresh();
          }}
        />
      )}
    </>
  );
}

export function AdminGrantsPage() {
  return (
    <AdminFrame title="Grants">
      <GrantsContent />
    </AdminFrame>
  );
}
