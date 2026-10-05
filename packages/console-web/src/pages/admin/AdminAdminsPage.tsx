import { useState, type FormEvent } from "react";
import { api } from "../../api/client";
import type { ConsoleAdminIn, ConsoleAdminOut, PrincipalKind } from "../../api/types";
import { useDialogAction } from "../../app/useDialogAction";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { ErrorPanel } from "../../components/ErrorPanel";
import { formatTime } from "../../lib/format";
import { ActionNotice, ReadState } from "../leader/common";
import { useAdminList } from "./AdminFrame";
import { PrincipalFields, Who, whoText } from "./PrincipalFields";
import { useRowFocus } from "../leader/rowFocus";

const noop = () => undefined;

function AddAdminForm({ onDone }: { onDone: (admin: ConsoleAdminOut) => void }) {
  const [kind, setKind] = useState<PrincipalKind>("entra_group");
  const [principal, setPrincipal] = useState("");
  // Not a dialog, but the same guard against a second submit while one is in flight.
  const action = useDialogAction(noop);
  const submit = (event: FormEvent) => {
    event.preventDefault();
    const body: ConsoleAdminIn = { principal_kind: kind, principal: principal.trim() };
    void action.submit(async () => {
      const admin = await api.post<ConsoleAdminOut>("/api/admin/console-admins", body);
      setPrincipal("");
      onDone(admin);
    });
  };
  return (
    <form
      className="form-grid form-panel"
      aria-label="Add a console administrator"
      onSubmit={submit}
    >
      <h2>Add a console administrator</h2>
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
          Add administrator
        </button>
      </div>
    </form>
  );
}

/** The Console administrators section of Administration (pages/admin/AdminPage.tsx frames it). */
export function AdminAdminsSection() {
  const read = useAdminList<ConsoleAdminOut>("/api/admin/console-admins");
  const [removing, setRemoving] = useState<ConsoleAdminOut | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const rows = useRowFocus(read, setNotice);

  return (
    <div {...rows.props}>
      <div className="section-head">
        <h2>Console administrators</h2>
      </div>
      <ActionNotice message={notice} />
      <ReadState read={read} what="console administrators">
        {(admins) => (
          <div
            className="table-scroll"
            role="region"
            aria-label="Console administrators"
            tabIndex={0}
          >
            <table className="medium">
              <thead>
                <tr>
                  <th scope="col">Who</th>
                  <th scope="col">Added</th>
                  <th scope="col">Actions</th>
                </tr>
              </thead>
              <tbody>
                {admins.map((admin) => (
                  <tr key={admin.id} data-row={admin.id}>
                    <th scope="row" className="long">
                      <Who kind={admin.principal_kind} principal={admin.principal} />
                    </th>
                    <td>
                      <span className="nowrap">{formatTime(admin.created_at)}</span>{" "}
                      <span className="by-line">by {admin.created_by}</span>
                    </td>
                    <td className="actions">
                      <button
                        type="button"
                        className="button button-danger"
                        onClick={() => setRemoving(admin)}
                        aria-label={`Remove console administrator ${whoText(admin.principal_kind, admin.principal)}`}
                      >
                        Remove
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </ReadState>
      <AddAdminForm
        onDone={(admin) => {
          const who = whoText(admin.principal_kind, admin.principal, true);
          setNotice(`${who} is now a console administrator.`);
          read.refresh();
        }}
      />
      {removing !== null && (
        <ConfirmDialog
          title="Remove this console administrator?"
          message={`${whoText(removing.principal_kind, removing.principal, true)} can no longer add leaders or give roles. The last administrator cannot be removed.`}
          confirmLabel="Remove administrator"
          onClose={() => setRemoving(null)}
          onConfirm={async () => {
            await api.del(`/api/admin/console-admins/${encodeURIComponent(removing.id)}`);
            rows.done("The console administrator is removed.");
          }}
        />
      )}
    </div>
  );
}

