import { useId, useRef, useState, type FormEvent } from "react";
import { ApiError, api, leaderPath } from "../../api/client";
import type { ChannelMode, LocationOut, RequiredDevice } from "../../api/types";
import { useAction } from "../../app/useAction";
import { useDialogAction } from "../../app/useDialogAction";
import { ActionButton } from "../../components/ActionButton";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { Dialog } from "../../components/Dialog";
import { ErrorPanel } from "../../components/ErrorPanel";
import { formatDuration, formatTime } from "../../lib/format";
import { ActionNotice, ReadState, RefreshButton, useLeaderRead, type TabProps } from "./common";
import {
  EMPTY_FORM,
  FIELD_ORDER,
  locationBody,
  validateLocation,
  type LocationErrors,
  type LocationField,
  type LocationForm,
} from "./locationForm";

export { locationBody };

/** Shown for any 422: the server's own text can carry field paths, so it is never shown. */
const REFUSED_TEXT = "The values were not accepted. Check each field and try again.";

function isRefusal(error: unknown): boolean {
  return error instanceof ApiError && error.status === 422;
}

function TextField({
  label,
  field,
  value,
  onChange,
  error,
  help,
  required = false,
}: {
  label: string;
  field: LocationField;
  value: string;
  onChange: (value: string) => void;
  error: string | undefined;
  help?: string;
  required?: boolean;
}) {
  const helpId = useId();
  const errorId = useId();
  const described = [help === undefined ? null : helpId, error === undefined ? null : errorId]
    .filter((id) => id !== null)
    .join(" ");
  return (
    <div>
      <label className="field">
        {label}
        <input
          name={field}
          value={value}
          aria-required={required || undefined}
          aria-invalid={error === undefined ? undefined : true}
          aria-describedby={described === "" ? undefined : described}
          onChange={(event) => onChange(event.target.value)}
        />
      </label>
      {help !== undefined && (
        <p id={helpId} className="field-help">
          {help}
        </p>
      )}
      {error !== undefined && (
        <p id={errorId} className="error-text" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

function AddLocationDialog({
  leaderName,
  onClose,
  onDone,
}: {
  leaderName: string;
  onClose: () => void;
  onDone: (name: string) => void;
}) {
  const [form, setForm] = useState<LocationForm>(EMPTY_FORM);
  const [errors, setErrors] = useState<LocationErrors>({});
  const action = useDialogAction(onClose);
  const formRef = useRef<HTMLFormElement>(null);

  const set = (patch: Partial<LocationForm>) => {
    setForm((prev) => ({ ...prev, ...patch }));
    // A field the person is correcting stops showing its old mistake.
    setErrors((prev) => {
      const cleared = Object.keys(patch).flatMap((key) =>
        key === "channel_mode" ? ["left", "right"] : [key],
      );
      return Object.fromEntries(Object.entries(prev).filter(([key]) => !cleared.includes(key)));
    });
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const found = validateLocation(form);
    setErrors(found);
    const first = FIELD_ORDER.find((field) => found[field] !== undefined);
    if (first !== undefined) {
      const control = formRef.current?.elements.namedItem(first);
      if (control instanceof HTMLElement) control.focus();
      return;
    }
    const body = locationBody(form);
    void action.submit(async () => {
      await api.post(leaderPath(leaderName, "locations"), body);
      onDone(body.name);
    });
  };

  const split = form.channel_mode !== "mono";
  return (
    <Dialog title={`Add a location to ${leaderName}`} onClose={action.close}>
      <form ref={formRef} className="form-grid" noValidate onSubmit={submit}>
        <TextField
          label="Name"
          field="name"
          required
          value={form.name}
          error={errors.name}
          onChange={(name) => set({ name })}
        />
        <TextField
          label="Folder on the leader (absolute path)"
          field="root"
          required
          value={form.root}
          error={errors.root}
          onChange={(root) => set({ root })}
        />
        <TextField
          label="Input prefix"
          field="input_prefix"
          value={form.input_prefix}
          error={errors.input_prefix}
          help="A relative folder ending in /, such as incoming/. Empty scans the whole folder."
          onChange={(input_prefix) => set({ input_prefix })}
        />
        <TextField
          label="Output prefix"
          field="output_prefix"
          value={form.output_prefix}
          error={errors.output_prefix}
          help="Where transcripts are written. Empty uses the leader's default, transcripts/."
          onChange={(output_prefix) => set({ output_prefix })}
        />
        <TextField
          label="Pool"
          field="pool"
          value={form.pool}
          error={errors.pool}
          help="Empty uses the leader's default pool, default."
          onChange={(pool) => set({ pool })}
        />
        <label className="field">
          Required device
          <select
            value={form.required_device}
            onChange={(event) =>
              set({ required_device: event.target.value as RequiredDevice | "" })
            }
          >
            <option value="">Any (default)</option>
            <option value="cuda">CUDA GPU</option>
            <option value="cpu">CPU</option>
          </select>
        </label>
        <TextField
          label="Scan interval in seconds"
          field="scan_interval_s"
          value={form.scan_interval_s}
          error={errors.scan_interval_s}
          help="From 30 to 604800. Empty uses the leader's default, 900."
          onChange={(scan_interval_s) => set({ scan_interval_s })}
        />
        <label className="field">
          Channels
          <select
            value={form.channel_mode}
            onChange={(event) => set({ channel_mode: event.target.value as ChannelMode })}
          >
            <option value="mono">Mono</option>
            <option value="stereo_split">Stereo, one speaker per channel</option>
            <option value="auto">Automatic</option>
          </select>
        </label>
        {split && (
          <>
            <TextField
              label="Left channel label"
              field="left"
              required
              value={form.left}
              error={errors.left}
              help="1 to 40 characters, no space at either end."
              onChange={(left) => set({ left })}
            />
            <TextField
              label="Right channel label"
              field="right"
              required
              value={form.right}
              error={errors.right}
              help="Different from the left label, ignoring upper and lower case."
              onChange={(right) => set({ right })}
            />
          </>
        )}
        {action.error !== null &&
          (isRefusal(action.error) ? (
            <div className="error-panel" role="alert">
              <p className="error-title">{REFUSED_TEXT}</p>
            </div>
          ) : (
            <ErrorPanel error={action.error} />
          ))}
        <div className="dialog-buttons">
          <button type="button" className="button" onClick={action.close}>
            Cancel
          </button>
          <button
            type="submit"
            className="button button-primary"
            aria-disabled={action.busy || undefined}
          >
            Add location
          </button>
        </div>
      </form>
    </Dialog>
  );
}

function channels(location: LocationOut): string {
  if (location.channel_mode === "mono") return "mono";
  return `${location.channel_mode} (${location.channel_labels.join(", ")})`;
}

export function LocationsTab({ leader }: TabProps) {
  const read = useLeaderRead<LocationOut[]>(leader.name, "locations");
  const action = useAction();
  const [adding, setAdding] = useState(false);
  const [disabling, setDisabling] = useState<LocationOut | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const path = (location: LocationOut, verb: "enable" | "disable" | "ingest") =>
    leaderPath(leader.name, `locations/${encodeURIComponent(location.name)}/${verb}`);

  const post = async (location: LocationOut, verb: "enable" | "ingest", done: string) => {
    setNotice(null);
    if (await action.run(() => api.post(path(location, verb)))) {
      setNotice(done);
      read.refresh();
    }
  };

  return (
    <>
      <div className="section-head">
        <ActionButton held={leader.role} action="locations.add" onClick={() => setAdding(true)}>
          Add location
        </ActionButton>
        <RefreshButton read={read} />
      </div>
      <ActionNotice message={notice} />
      {action.error !== null && <ErrorPanel error={action.error} />}
      <ReadState read={read} what="locations">
        {(locations) =>
          locations.length === 0 ? (
            <p>This leader has no locations.</p>
          ) : (
            <div className="table-scroll" role="region" aria-label="Locations" tabIndex={0}>
              <table>
                <thead>
                  <tr>
                    <th scope="col">Location</th>
                    <th scope="col">Folder</th>
                    <th scope="col">Pool</th>
                    <th scope="col">Device</th>
                    <th scope="col">Channels</th>
                    <th scope="col">Scan every</th>
                    <th scope="col">Enabled</th>
                    <th scope="col">Last scan</th>
                    <th scope="col">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {locations.map((location) => (
                    <tr key={location.id}>
                      <th scope="row">{location.name}</th>
                      <td>
                        <span className="mono">{location.root ?? "–"}</span>
                        {location.input_prefix !== "" && (
                          <span className="cell-note">Input prefix {location.input_prefix}</span>
                        )}
                      </td>
                      <td>{location.pool}</td>
                      <td>{location.required_device}</td>
                      <td>{channels(location)}</td>
                      <td>{formatDuration(location.scan_interval_s)}</td>
                      <td>{location.enabled ? "Yes" : "No"}</td>
                      <td>
                        {location.last_scan_at ? formatTime(location.last_scan_at) : "Never"}
                        {location.scan_requested && (
                          <span className="cell-note">Scan requested</span>
                        )}
                        {location.last_scan_error && (
                          <span className="cell-note error-text">{location.last_scan_error}</span>
                        )}
                      </td>
                      <td className="actions">
                        {location.enabled && (
                          <ActionButton
                            held={leader.role}
                            action="locations.ingest"
                            busy={action.busy}
                            onClick={() =>
                              void post(
                                location,
                                "ingest",
                                `A scan of ${location.name} is requested.`,
                              )
                            }
                            name={`Scan now ${location.name}`}
                          >
                            Scan now
                          </ActionButton>
                        )}
                        {location.enabled ? (
                          <ActionButton
                            held={leader.role}
                            action="locations.disable"
                            danger
                            onClick={() => setDisabling(location)}
                            name={`Disable ${location.name}`}
                          >
                            Disable
                          </ActionButton>
                        ) : (
                          <ActionButton
                            held={leader.role}
                            action="locations.enable"
                            busy={action.busy}
                            onClick={() =>
                              void post(location, "enable", `${location.name} is enabled.`)
                            }
                            name={`Enable ${location.name}`}
                          >
                            Enable
                          </ActionButton>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        }
      </ReadState>
      {adding && (
        <AddLocationDialog
          leaderName={leader.name}
          onClose={() => setAdding(false)}
          onDone={(name) => {
            setNotice(`Location ${name} is added.`);
            read.refresh();
          }}
        />
      )}
      {disabling !== null && (
        <ConfirmDialog
          title={`Disable ${disabling.name}?`}
          message={
            "The leader stops scanning this location for new recordings until it is enabled " +
            "again. Jobs already made are not affected."
          }
          confirmLabel="Disable location"
          onClose={() => setDisabling(null)}
          onConfirm={async () => {
            setNotice(null);
            await api.post(path(disabling, "disable"));
            setNotice(`${disabling.name} is disabled.`);
            read.refresh();
          }}
        />
      )}
    </>
  );
}
