import { useEffect, useRef, useState, type ReactNode } from "react";
import { isIdle, onResume } from "../app/activity";
import { Link, useLocation } from "../app/router";
import { useSession } from "../app/session";
import { readTheme, saveTheme, type ThemeChoice } from "../app/theme";

export interface NavItem {
  to: string;
  label: string;
  /** Whether the item is the current section for this path. */
  match: (pathname: string) => boolean;
}

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

/** Shown once background refreshes have stopped because the person has been idle. */
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
    <p className="notice" role="status">
      Updates are paused because you have been inactive. Press any key or click to resume.
    </p>
  );
}

export function Layout({
  nav,
  pageOf = (pathname) => pathname,
  children,
}: {
  nav: NavItem[];
  /** Names the page a path belongs to; focus moves only when this changes (not on a tab switch). */
  pageOf?: (pathname: string) => string;
  children: ReactNode;
}) {
  const { session, signOut } = useSession();
  const { pathname } = useLocation();
  const page = pageOf(pathname);
  const main = useRef<HTMLElement>(null);
  const first = useRef(true);

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

  return (
    <>
      <a className="skip-link" href="#main">
        Skip to main content
      </a>
      <header className="app-header">
        <Link to="/" className="brand">
          SwarmScribe console
        </Link>
        <nav aria-label="Main">
          <ul className="nav-list">
            {nav.map((item) => {
              const current = item.match(pathname);
              return (
                <li key={item.to}>
                  <Link to={item.to} className="nav-link" aria-current={current ? "page" : undefined}>
                    {item.label}
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>
        <div className="header-tools">
          <span className="who">{session.email ?? session.subject}</span>
          <ThemeSelect />
          <button type="button" className="button" onClick={() => void signOut()}>
            Sign out
          </button>
        </div>
      </header>
      <IdleNotice />
      <main id="main" ref={main} tabIndex={-1}>
        {children}
      </main>
    </>
  );
}
