import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { ProvidersOut, SessionInfo } from "../api/types";
import { safeReturnTo } from "../app/navigation";
import { useSearchParam } from "../app/router";
import { usePageTitle } from "../app/usePageTitle";
import { BrandMark, Wordmark } from "../components/Brand";
import { ErrorPanel } from "../components/ErrorPanel";

const PROVIDER_NAMES: Record<string, string> = { entra: "Microsoft", google: "Google" };

/**
 * Sign-in starts with a full-page visit to /auth/login (the console redirects to the
 * provider and back), so these are plain links, not in-app navigations. This page sits
 * outside SessionProvider: it probes GET /api/session itself, and a 401 there simply means
 * "signed out" (the navigation module does not reload on /sign-in).
 *
 * Two panels: the brand on ink (in both themes), and the sign-in card, a lifted sheet.
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
    <div className="signin">
      <header className="signin-brand on-ink">
        <span className="brand signin-logo">
          <BrandMark size={40} />
          <Wordmark />
        </span>
        <div>
          <p className="signin-pitch">Every leader you look after, in one place.</p>
          <p className="signin-lede">
            See what is waiting, what finished and what needs a look. Everything you change here is
            recorded under your name, on the leader as well as here.
          </p>
        </div>
        <p className="signin-host">
          <span className="mark mark-hex" aria-hidden="true" />
          {window.location.host}
        </p>
      </header>
      <main id="main" className="signin-side">
        <div className="sheet sheet-lifted signin-card">
          <h1 className="sheet-title signin-title">Sign in</h1>
          <p className="muted">Use the account your organisation gave you.</p>
          {signedOut && <p role="status">You are signed out.</p>}
          {signedInAs !== null && (
            <p role="status">
              You are already signed in as {signedInAs}. <a href={returnTo}>Go to the console</a>.
            </p>
          )}
          {error !== null && <ErrorPanel error={error} />}
          {providers === null && error === null && <p>Loading the ways to sign in…</p>}
          {providers !== null && providers.length === 0 && (
            <p>No way to sign in is set up on this console. Tell whoever runs it.</p>
          )}
          {providers !== null && providers.length > 0 && (
            <ul className="provider-list">
              {providers.map((provider, i) => {
                const params = new URLSearchParams({ provider });
                if (returnTo !== "/") params.set("return_to", returnTo);
                return (
                  <li key={provider}>
                    <a
                      className={i === 0 ? "button button-primary button-wide" : "button button-wide"}
                      href={`/auth/login?${params.toString()}`}
                    >
                      Continue with {PROVIDER_NAMES[provider] ?? provider}
                    </a>
                  </li>
                );
              })}
            </ul>
          )}
          <p className="signin-note">
            The console signs you out after a spell with no activity, and after a longer fixed time
            whatever you are doing.
          </p>
        </div>
      </main>
    </div>
  );
}
