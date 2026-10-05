import { AdminAdminsSection } from "./AdminAdminsPage";
import { AdminFrame } from "./AdminFrame";
import { AdminGrantsSection } from "./AdminGrantsPage";
import { AdminLeadersSection } from "./AdminLeadersPage";

export type AdminSection = "leaders" | "grants" | "admins";

const TITLES: Record<AdminSection, string> = {
  leaders: "Leaders",
  grants: "Who can do what",
  admins: "Console administrators",
};

/**
 * Administration is one page with three sections. The frame (heading, sections' links) is
 * the same element for all three, so switching section keeps focus on the link that was
 * activated; only the section beneath it is replaced.
 */
export function AdminPage({ section }: { section: AdminSection }) {
  return (
    <AdminFrame title={TITLES[section]}>
      {section === "leaders" && <AdminLeadersSection />}
      {section === "grants" && <AdminGrantsSection />}
      {section === "admins" && <AdminAdminsSection />}
    </AdminFrame>
  );
}
