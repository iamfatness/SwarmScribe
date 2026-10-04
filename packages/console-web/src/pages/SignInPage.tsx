import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { ProvidersOut, SessionInfo } from "../api/types";
import { safeReturnTo } from "../app/navigation";
import { useSearchParam } from "../app/router";
import { usePageTitle } from "../app/usePageTitle";
import { ErrorPanel } from "../components/ErrorPanel";

const PROVIDER_NAMES: Record<string, string> = { entra: "Microsoft Entra ID", google: "Google" };

/**
 * Sign-in starts with a full-page visit to /auth/login (the console redirects to the
 * provider and back), so these are plain links, not in-app navigations. This page sits
 * outside SessionProvider: it probes GET /api/session itself, and a 401 there simply means
 * "signed out" (the navigation module does not reload on /sign-in).
 */
export function SignInPage() {
  usePageTitle("Sign in");
  const returnTo = safeReturnTo(useSearchParam("return_to"));
  const signedOut = useSearchParam("signed_out") === "1";
  const [providers, setProviders] = useState<string[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [signedInAs, setSignedInAs] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    api
      .get<ProvidersOut>("/auth/providers", controller.signal)
      .then((answer) => setProviders(answer.providers))
      .catch((caught: unknown) => {
        if (!controller.signal.aborted) setError(caught);
      });
    // Already signed in? Offer the way back. Any failure (401 included) means "not signed in".
    api
      .get<SessionInfo>("/api/session", controller.signal)
      .then((session) => setSignedInAs(session.email ?? session.subject))
      .catch(() => undefined);
    return () => controller.abort();
  }, []);

  return (
    <main id="main" className="sign-in">
      <h1>Sign in to the SwarmScribe console</h1>
      {signedOut && <p role="status">You have signed out.</p>}
      {signedInAs !== null && (
        <p role="status">
          You are already signed in as {signedInAs}. <a href={returnTo}>Continue to the console</a>.
        </p>
      )}
      {error !== null && <ErrorPanel error={error} />}
      {providers === null && error === null && <p>Loading sign-in options…</p>}
      {providers !== null && providers.length === 0 && <p>No sign-in provider is configured.</p>}
      {providers !== null && providers.length > 0 && (
        <ul className="provider-list">
          {providers.map((provider) => {
            const params = new URLSearchParams({ provider });
            if (returnTo !== "/") params.set("return_to", returnTo);
            return (
              <li key={provider}>
                <a className="button button-primary" href={`/auth/login?${params.toString()}`}>
                  Sign in with {PROVIDER_NAMES[provider] ?? provider}
                </a>
              </li>
            );
          })}
        </ul>
      )}
    </main>
  );
}
