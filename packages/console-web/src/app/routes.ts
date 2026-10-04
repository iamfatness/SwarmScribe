import prefixes from "./routePrefixes.json";

// The one list of the web app's top-level routes: "/" and the first segment of every other
// page. App.tsx sends a path to NotFound unless it is routed here, so a page cannot exist
// without being listed. The Helm chart's Ingress paths are checked against this same file
// (deploy/helm/swarmscribe-console/ci/check_render.py): a page whose prefix is missing here
// would 404 at the ingress controller.
const ROUTED: readonly string[] = prefixes;

/** Whether the app has a page for `pathname`'s first segment (the root counts). */
export function isRouted(pathname: string): boolean {
  if (pathname === "/") return ROUTED.includes("/");
  const first = pathname.split("/")[1] ?? "";
  return first !== "" && ROUTED.includes(`/${first}`);
}
