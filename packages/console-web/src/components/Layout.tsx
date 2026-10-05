import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import type { FleetLeader, Role } from "../api/types";
import { isIdle, onResume } from "../app/activity";
import { useFleet } from "../app/fleet";
import { Link, useLocation } from "../app/router";
import { useSession } from "../app/session";
import { readTheme, saveTheme, type ThemeChoice } from "../app/theme";
import { leaderUrl } from "../pages/leader/tabs";
import { BrandMark, Wordmark } from "./Brand";

function ThemeSelect() {
  const [choice, setChoice] = useState<ThemeChoice>(readTheme);
  return (
    <label className="theme-select">
      Theme
      <select
        value={choice}
        onChange={(event) => {
          const next = event.target.value as ThemeChoice;
          setChoice(next);
          saveTheme(next);
        }}
      >
        <option value="system">System</option>
        <option value="light">Light</option>
        <option value="dark">Dark</option>
      </select>
    </label>
  );
}

/** Shown once background checks have stopped because the person has been away. */
function IdleNotice() {
  const [idle, setIdle] = useState(false);
  useEffect(() => {
    const timer = setInterval(() => setIdle(isIdle()), 30_000);
    const unsubscribe = onResume(() => setIdle(false));
    return () => {
      clearInterval(timer);
      unsubscribe();
    };
  }, []);
  if (!idle) return null;
  return (
    <p className="notice idle-notice" role="status">
      Checks are paused because you have been away. Press a key or click to start them again.
    </p>
  );
}

const ROLES: Role[] = ["admin", "operator", "viewer"];

/** "Admin on 2 leaders", "Admin on 2, viewer on 1 leader": the roles the person holds. */
export function roleSummary(leaders: FleetLeader[]): string {
  const parts = ROLES.map((role) => ({
    role,
    n: leaders.filter((leader) => leader.role === role).length,
  })).filter((part) => part.n > 0);
  if (parts.length === 0) return "No role on any leader yet";
  const text = parts
    .map((part, i) =>
      i === parts.length - 1
        ? `${part.role} on ${part.n} ${part.n === 1 ? "leader" : "leaders"}`
        : `${part.role} on ${part.n}`,
    )
    .join(", ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** What the rail says beside a leader that needs attention; nothing while it answers. */
function railFlag(leader: FleetLeader): { text: string; quiet: boolean } | null {
  switch (leader.health) {
    case "unreachable":
      return { text: "no answer", quiet: false };
    case "credential_revoked":
      return { text: "revoked", quiet: false };
    case "disabled":
      return { text: "off", quiet: true };
    default:
      return null;
  }
}

/** The leader a drill-down address names, lower-cased; null anywhere else. */
function leaderOf(pathname: string): string | null {
  const segments = pathname.split("/");
  if (segments[1] !== "leaders" || !segments[2]) return null;
  try {
    return decodeURIComponent(segments[2]).toLowerCase();
  } catch {
    return null;
  }
}

/**
 * The frame of every signed-in page: the rail (brand, Fleet with each visible leader beneath
 * it, Administration for a console administrator, then the person, the theme and sign-out)
 * and the main region. Below 900px the rail is a top bar and everything under the brand
 * sits behind a Menu button: a disclosure that takes focus when it opens, stops the page
 * behind it scrolling, and closes on Escape, on a press outside it, or on any navigation.
 */
export function Layout({
  pageOf = (pathname) => pathname,
  children,
}: {
  /** Names the page a path belongs to; focus moves only when this changes (not on a tab switch). */
  pageOf?: (pathname: string) => string;
  children: ReactNode;
}) {
  const { session, signOut } = useSession();
  const { pathname } = useLocation();
  const { data: leaders } = useFleet();
  const page = pageOf(pathname);
  const main = useRef<HTMLElement>(null);
  const first = useRef(true);
  const menuButton = useRef<HTMLButtonElement>(null);
  const rail = useRef<HTMLElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const panelId = useId();
  // The menu is open for one address: following a link closes it, with no effect to run.
  const [openAt, setOpenAt] = useState<string | null>(null);
  const open = openAt === pathname;

  // After an in-app navigation, move focus to the new page's main heading (or the main
  // region when a page has none), so keyboard and screen-reader users start at its content
  // and hear its title, not the link they left behind. The first render keeps the browser's
  // own start of page. A heading is not focusable by default, hence tabindex -1.
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    const heading = main.current?.querySelector("h1");
    if (heading) {
      heading.tabIndex = -1;
      heading.focus();
    } else {
      main.current?.focus();
    }
  }, [page]);

  // An open menu takes focus into itself (its first link) and the page behind it does not
  // scroll; both are undone by closing, whichever way that happens.
  useEffect(() => {
    if (!open) return;
    panel.current?.querySelector<HTMLElement>("a[href], button, select")?.focus();
    document.documentElement.classList.add("menu-open");
    return () => document.documentElement.classList.remove("menu-open");
  }, [open]);

  // Escape closes the open menu and hands focus back to its button. A dialog's own Escape
  // comes first: with one open, this does nothing. A press outside the rail also closes it;
  // focus goes to whatever was pressed, or back to the Menu button when that is not focusable.
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || document.querySelector("dialog[open]") !== null) return;
      setOpenAt(null);
      menuButton.current?.focus();
    };
    const onPress = (event: PointerEvent) => {
      if (event.target instanceof Node && rail.current?.contains(event.target)) return;
      setOpenAt(null);
      // The press moves focus to whatever was pressed once this handler returns. When that is
      // a control, focus stays with it; when it is only the page (the body or the main region,
      // which takes focus for the skip link), the Menu button takes it, so focus is not lost
      // with the menu that held it.
      setTimeout(() => {
        const active = document.activeElement;
        if (active === null || active === document.body || active === main.current) {
          menuButton.current?.focus();
        }
      }, 0);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onPress);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onPress);
    };
  }, [open]);

  const currentLeader = leaderOf(pathname);
  const onFleet = pathname === "/";
  const onAdmin = pathname === "/admin" || pathname.startsWith("/admin/");
  return (
    <div className="app">
      <a className="skip-link" href="#main">
        Skip to main content
      </a>
      <header ref={rail} className="rail on-ink">
        <div className="rail-top">
          <Link to="/" className="brand" aria-label="SwarmScribe console">
            <BrandMark />
            <Wordmark />
          </Link>
          <button
            ref={menuButton}
            type="button"
            className="button menu-button"
            aria-expanded={open}
            aria-controls={panelId}
            onClick={() => setOpenAt(open ? null : pathname)}
          >
            Menu
          </button>
        </div>
        <div ref={panel} id={panelId} className="rail-panel" data-open={open ? "true" : "false"}>
          <nav aria-label="Console">
            <ul className="nav-list">
              <li className="nav-fleet">
                <Link to="/" className="nav-link" aria-current={onFleet ? "page" : undefined}>
                  <span className={onFleet ? "mark mark-hex" : "mark mark-hex mark-quiet"} aria-hidden="true" />
                  Fleet
                </Link>
                {leaders !== undefined && leaders.length > 0 && (
                  <ul className="nav-sublist" aria-label="Leaders">
                    {leaders.map((leader) => {
                      const flag = railFlag(leader);
                      const current = currentLeader === leader.name.toLowerCase();
                      return (
                        <li key={leader.name}>
                          <Link
                            to={leaderUrl(leader.name)}
                            className="nav-link"
                            aria-current={current ? "page" : undefined}
                          >
                            {leader.name}
                            {flag !== null && " "}
                            {flag !== null && (
                              <span className={flag.quiet ? "nav-flag nav-flag-quiet" : "nav-flag"}>
                                {flag.text}
                              </span>
                            )}
                          </Link>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </li>
              {session.console_admin && (
                <li className="nav-gap">
                  <Link
                    to="/admin/leaders"
                    className="nav-link"
                    aria-current={onAdmin ? "page" : undefined}
                  >
                    <span
                      className={onAdmin ? "mark mark-hex" : "mark mark-hex mark-quiet"}
                      aria-hidden="true"
                    />
                    Administration
                  </Link>
                </li>
              )}
            </ul>
          </nav>
          <div className="rail-foot">
            <p className="who">
              <span className="who-name">{session.email ?? session.subject}</span>
              {session.console_admin && <span className="who-line">Console administrator</span>}
              {leaders !== undefined && <span className="who-line">{roleSummary(leaders)}</span>}
            </p>
            <ThemeSelect />
            <button type="button" className="button button-wide" onClick={() => void signOut()}>
              Sign out
            </button>
          </div>
        </div>
      </header>
      <div className="app-main">
        <IdleNotice />
        <main id="main" ref={main} tabIndex={-1}>
          {children}
        </main>
      </div>
    </div>
  );
}
