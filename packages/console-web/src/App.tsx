import { useEffect } from "react";
import { startActivityTracking } from "./app/activity";
import { FleetProvider } from "./app/fleet";
import { RouterProvider, useLocation } from "./app/router";
import { SessionProvider } from "./app/session";
import { Layout, type NavItem } from "./components/Layout";
import { FleetPage } from "./pages/FleetPage";
import { NotFoundPage } from "./pages/NotFoundPage";
import { SignInPage } from "./pages/SignInPage";

const NAV: NavItem[] = [{ to: "/", label: "Fleet", match: (pathname) => pathname === "/" }];

function SignedInPage() {
  const { pathname } = useLocation();
  if (pathname === "/") return <FleetPage />;
  return <NotFoundPage />;
}

function Routes() {
  const { pathname } = useLocation();
  // Outside SessionProvider: nothing here may need a session or poll the fleet.
  if (pathname === "/sign-in") return <SignInPage />;
  return (
    <SessionProvider>
      <FleetProvider>
        <Layout nav={NAV}>
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
