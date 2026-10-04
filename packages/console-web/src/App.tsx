import { useEffect } from "react";
import { startActivityTracking } from "./app/activity";
import { FleetProvider } from "./app/fleet";
import { RouterProvider, matchPath, useLocation, useNavigate } from "./app/router";
import { SessionProvider } from "./app/session";
import { Layout, type NavItem } from "./components/Layout";
import { FleetPage } from "./pages/FleetPage";
import { LeaderPage } from "./pages/leader/LeaderPage";
import { leaderUrl } from "./pages/leader/tabs";
import { NotFoundPage } from "./pages/NotFoundPage";
import { SignInPage } from "./pages/SignInPage";

const FLEET: NavItem = {
  to: "/",
  label: "Fleet",
  match: (pathname) => pathname === "/" || pathname.startsWith("/leaders/"),
};

/**
 * The page a path belongs to, for moving focus: switching tabs inside one leader's
 * drill-down stays on the page (focus stays on the tab link that was activated); going
 * from the fleet to a leader, or from one leader to another, changes the page.
 */
function pageOf(pathname: string): string {
  const leader = matchPath("/leaders/:name/:tab", pathname) ?? matchPath("/leaders/:name", pathname);
  return leader === null ? pathname : `/leaders/${leader.name as string}`;
}

function Redirect({ to }: { to: string }) {
  const navigate = useNavigate();
  useEffect(() => navigate(to, { replace: true }), [navigate, to]);
  return null;
}

function SignedInPage() {
  const { pathname } = useLocation();
  if (pathname === "/") return <FleetPage />;
  const drill = matchPath("/leaders/:name/:tab", pathname);
  if (drill !== null) return <LeaderPage name={drill.name as string} tab={drill.tab as string} />;
  const bare = matchPath("/leaders/:name", pathname);
  if (bare !== null) return <Redirect to={leaderUrl(bare.name as string)} />;
  return <NotFoundPage />;
}

function Routes() {
  const { pathname } = useLocation();
  // Outside SessionProvider: nothing here may need a session or poll the fleet.
  if (pathname === "/sign-in") return <SignInPage />;
  return (
    <SessionProvider>
      <FleetProvider>
        <Layout nav={[FLEET]} pageOf={pageOf}>
          <SignedInPage />
        </Layout>
      </FleetProvider>
    </SessionProvider>
  );
}

export function App() {
  // Without this, idle never ends on input and background refreshes stay paused.
  useEffect(() => startActivityTracking(), []);
  return (
    <RouterProvider>
      <Routes />
    </RouterProvider>
  );
}
