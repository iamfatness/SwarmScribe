import { useId, type ReactNode } from "react";
import { can, neededRole, type LeaderAction } from "../api/roles";
import type { Role } from "../api/types";

/**
 * A leader action's button. When the person's console role for the leader is below the
 * action's (the console's allow-list), the button is disabled and says which role it needs;
 * the console and the leader would refuse it anyway. The note is visible text beside the
 * button (in reading order for screen readers, not colour-only) and also its description.
 */
export function ActionButton({
  held,
  action,
  onClick,
  danger = false,
  primary = false,
  busy = false,
  name,
  children,
}: {
  /** The person's console role on this leader (FleetLeader.role). */
  held: Role;
  action: LeaderAction;
  onClick: () => void;
  danger?: boolean;
  /** The one action a row is there for (Try again on a failed job). */
  primary?: boolean;
  busy?: boolean;
  /**
   * The full accessible name when the visible text needs its row for context ("Cancel job
   * 1a2b3c4d"). It must contain the visible text, word for word, so speech input finds it.
   */
  name?: string;
  children: ReactNode;
}) {
  const noteId = useId();
  const allowed = can(held, action);
  return (
    <span className="action">
      <button
        type="button"
        className={danger ? "button button-danger" : primary ? "button button-primary" : "button"}
        disabled={!allowed}
        aria-disabled={busy || undefined}
        aria-label={name}
        aria-describedby={allowed ? undefined : noteId}
        onClick={() => {
          if (!busy) onClick();
        }}
      >
        {children}
      </button>
      {!allowed && (
        <span id={noteId} className="needs-role">
          needs {neededRole(action)}
        </span>
      )}
    </span>
  );
}
