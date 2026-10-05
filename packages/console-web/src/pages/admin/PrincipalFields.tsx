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

/**
 * The words that go before the name itself, for each kind. Nobody types or reads a kind's
 * stored code ("entra_group:") here: the form asks for it in words, so the lists and the
 * sentences about them say it in words too. An email address needs none.
 */
const KIND_WORDS: Record<string, string> = {
  entra_group: "Entra ID group",
  google_group: "Google group",
  email: "",
  domain: "everyone at",
};

/**
 * Who an entry names, as a phrase: "Entra ID group a1a1…", "Google group ops@example.org",
 * "sam@example.org", "everyone at example.org". `start` is for the beginning of a sentence or
 * a cell ("Everyone at example.org is now viewer on all.").
 */
export function whoText(kind: string, principal: string, start = false): string {
  const words = KIND_WORDS[kind] ?? "";
  const phrase = words === "" ? principal : `${words} ${principal}`;
  return start && kind === "domain" ? `E${phrase.slice(1)}` : phrase;
}

/** The same in a table cell: the words in the page's type, the name itself in mono, whole. */
export function Who({ kind, principal }: { kind: string; principal: string }) {
  const words = whoText(kind, "", true).trim();
  return (
    <>
      {words !== "" && <span className="who-kind">{words} </span>}
      <span className="mono ident">{principal}</span>
    </>
  );
}

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
