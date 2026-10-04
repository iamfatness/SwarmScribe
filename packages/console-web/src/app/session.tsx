import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, setCsrfToken, setUnauthenticatedHandler } from "../api/client";
import type { SessionInfo } from "../api/types";
import { ErrorPanel } from "../components/ErrorPanel";
import { goToSignIn, goToSignedOut, sessionEnded } from "./navigation";

interface SessionValue {
  session: SessionInfo;
  signOut: () => Promise<void>;
}

const SessionContext = createContext<SessionValue | null>(null);

export function useSession(): SessionValue {
  const value = useContext(SessionContext);
  if (value === null) throw new Error("useSession outside SessionProvider");
  return value;
}

type Loaded =
  | { state: "loading" }
  | { state: "ready"; session: SessionInfo }
  | { state: "failed"; error: unknown };

/**
 * Loads GET /api/session once, keeps its CSRF token for unsafe calls, and sends any 401
 * (a session that ended or expired) to the sign-in page.
 */
export function SessionProvider({ children }: { children: ReactNode }) {
  const [loaded, setLoaded] = useState<Loaded>({ state: "loading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    setUnauthenticatedHandler(goToSignIn);
    const controller = new AbortController();
    api
      .get<SessionInfo>("/api/session", controller.signal)
      .then((session) => {
        setCsrfToken(session.csrf_token);
        setLoaded({ state: "ready", session });
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) setLoaded({ state: "failed", error });
      });
    return () => controller.abort();
  }, [attempt]);

  const signOut = useCallback(async () => {
    try {
      await api.post("/api/session/logout");
    } catch {
      // Leave anyway. If the session survived, the sign-in page's own probe says so and
      // offers the way back; rethrowing here would only be an unhandled rejection.
    } finally {
      setCsrfToken(null);
      // A 401 from the logout already sent the person to sign in; do not abort that.
      if (!sessionEnded()) goToSignedOut();
    }
  }, []);

  const value = useMemo(
    () => (loaded.state === "ready" ? { session: loaded.session, signOut } : null),
    [loaded, signOut],
  );

  if (loaded.state === "loading") {
    return (
      <p className="page-message" role="status">
        Loading the console…
      </p>
    );
  }
  if (loaded.state === "failed" || value === null) {
    return (
      <main className="page-message">
        <h1>SwarmScribe console</h1>
        <ErrorPanel
          error={loaded.state === "failed" ? loaded.error : null}
          onRetry={() => {
            setLoaded({ state: "loading" });
            setAttempt((n) => n + 1);
          }}
        />
      </main>
    );
  }
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}
