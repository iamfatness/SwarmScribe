import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type AnchorHTMLAttributes,
  type MouseEvent,
  type ReactNode,
} from "react";

// A small history-API router: the app has a handful of fixed routes and no nested data
// loading, so it does not need a router library. Routes are extensionless and outside /api
// and /auth (the console serves index.html only for those; see static.py). A leader
// drill-down URL always ends with its tab, so a leader name with a dot is never the last
// segment.

export interface AppLocation {
  pathname: string;
  search: string;
}

interface RouterValue {
  location: AppLocation;
  navigate: (to: string, options?: { replace?: boolean }) => void;
}

const RouterContext = createContext<RouterValue | null>(null);

function current(): AppLocation {
  return { pathname: window.location.pathname, search: window.location.search };
}

export function RouterProvider({ children }: { children: ReactNode }) {
  const [location, setLocation] = useState<AppLocation>(current);
  useEffect(() => {
    const onPop = () => setLocation(current());
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);
  const navigate = useCallback((to: string, options?: { replace?: boolean }) => {
    if (options?.replace) window.history.replaceState(null, "", to);
    else window.history.pushState(null, "", to);
    setLocation(current());
  }, []);
  const value = useMemo(() => ({ location, navigate }), [location, navigate]);
  return <RouterContext.Provider value={value}>{children}</RouterContext.Provider>;
}

function useRouter(): RouterValue {
  const value = useContext(RouterContext);
  if (value === null) throw new Error("useRouter outside RouterProvider");
  return value;
}

export function useLocation(): AppLocation {
  return useRouter().location;
}

export function useNavigate(): RouterValue["navigate"] {
  return useRouter().navigate;
}

export function useSearchParam(name: string): string | null {
  return new URLSearchParams(useLocation().search).get(name);
}

/**
 * The parameters of `pattern` ("/leaders/:name/:tab") in `pathname`, decoded, or null.
 * A segment that does not decode does not match.
 */
export function matchPath(pattern: string, pathname: string): Record<string, string> | null {
  const want = pattern.split("/");
  const got = pathname.split("/");
  if (want.length !== got.length) return null;
  const params: Record<string, string> = {};
  for (let i = 0; i < want.length; i += 1) {
    const w = want[i] as string;
    const g = got[i] as string;
    if (w.startsWith(":")) {
      if (g === "") return null;
      try {
        params[w.slice(1)] = decodeURIComponent(g);
      } catch {
        return null;
      }
    } else if (w !== g) {
      return null;
    }
  }
  return params;
}

type LinkProps = Omit<AnchorHTMLAttributes<HTMLAnchorElement>, "href"> & { to: string; children: ReactNode };

export function Link({ to, onClick, children, ...rest }: LinkProps) {
  const navigate = useNavigate();
  const handle = (event: MouseEvent<HTMLAnchorElement>) => {
    onClick?.(event);
    if (
      (rest.target !== undefined && rest.target !== "_self") ||
      rest.download !== undefined ||
      event.defaultPrevented ||
      event.button !== 0 ||
      event.metaKey ||
      event.ctrlKey ||
      event.shiftKey ||
      event.altKey
    ) {
      return;
    }
    event.preventDefault();
    navigate(to);
  };
  return (
    <a href={to} onClick={handle} {...rest}>
      {children}
    </a>
  );
}
