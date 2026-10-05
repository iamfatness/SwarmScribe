import { useId } from "react";
import type { PrincipalKind } from "../../api/types";

/**
 * The four ways to name who gets a role or becomes a console administrator. `label` is the
 * choice as a person reads it, `field` the name of the box that follows it, `hint` what to
 * put there.
 */
export const PRINCIPAL_KINDS: { value: PrincipalKind; label: string; field: string; hint: string }[] = [
  {
    value: "entra_group",
    label: "An Entra ID group",
    field: "Group object ID",
    hint: "The group's object ID in Entra ID, a GUID.",
  },
  {
    value: "google_group",
    label: "A Google group",
    field: "Group address",
    hint: "The group's email address.",
  },
  {
    value: "email",
    label: "One person, by email",
    field: "Email address",
    hint: "The address of a Google account.",
  },
  {
    value: "domain",
    label: "Everyone at a domain",
    field: "Domain",
    hint: "A Google Workspace domain, such as example.org.",
  },
];

/** Who a role or a console administrator entry names: a kind, then the name itself. */
export function PrincipalFields({
  kind,
  principal,
  onKind,
  onPrincipal,
}: {
  kind: PrincipalKind;
  principal: string;
  onKind: (kind: PrincipalKind) => void;
  onPrincipal: (principal: string) => void;
}) {
  const hintId = useId();
  const chosen = PRINCIPAL_KINDS.find((k) => k.value === kind);
  return (
    <>
      <label className="field">
        Who
        <select value={kind} onChange={(e) => onKind(e.target.value as PrincipalKind)}>
          {PRINCIPAL_KINDS.map((k) => (
            <option key={k.value} value={k.value}>
              {k.label}
            </option>
          ))}
        </select>
      </label>
      <div className="field">
        <label className="field">
          {chosen?.field ?? "Name"}
          <input
            required
            autoComplete="off"
            spellCheck={false}
            value={principal}
            onChange={(e) => onPrincipal(e.target.value)}
            aria-describedby={hintId}
          />
        </label>
        <span id={hintId} className="field-help">
          {chosen?.hint ?? ""}
        </span>
      </div>
    </>
  );
}
