// The drill-down's tabs. Each is its own URL, /leaders/<name>/<tab>, always ending with the
// tab so a leader name with a dot is never the last segment (the console answers 404, not
// index.html, for a last segment with a dot).

export const TABS = [
  { id: "pools", label: "Pools and followers" },
] as const;

export type TabId = (typeof TABS)[number]["id"];

export function leaderUrl(name: string, tab: TabId = "pools"): string {
  return `/leaders/${encodeURIComponent(name)}/${tab}`;
}
