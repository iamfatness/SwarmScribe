import { useId } from "react";
import type { PrincipalKind } from "../../api/types";

export const PRINCIPAL_KINDS: { value: PrincipalKind; label: string; hint: string }[] = [
  { value: "entra_group", label: "Entra ID group", hint: "the group's object ID (a GUID)" },
  { value: "google_group", label: "Google group", hint: "the group's email address" },
  { value: "email", label: "Email address", hint: "a Google account's address" },
  { value: "domain", label: "Domain", hint: "a Google Workspace domain, such as example.org" },
];

/** Who a grant or a console administrator entry names: a kind and the principal itself. */
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
  const hint = PRINCIPAL_KINDS.find((k) => k.value === kind)?.hint ?? "";
  return (
    <>
      <label className="field">
        Principal kind
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
          Principal
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
          {hint}
        </span>
      </div>
    </>
  );
}
