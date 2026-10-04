// Looks for a string in the props and hook state of every component React has mounted right
// now, to show a secret did not outlive its dialog.
//
// What it can prove: the string is in no mounted component's props or hook state (nested
// plain objects, arrays and linked hook state included).
// What it cannot see: closures (a function's captured variables), DOM nodes and refs (the
// page's HTML is checked separately), Map and Set contents, module-level variables, and
// React's alternate (previous-render) fibers. A pass is evidence, not a proof of absence.
// It throws, rather than quietly answering "not found", if a structure is deeper than it will
// search.

const MAX_DEPTH = 500;
/** Fiber and element links that lead to the rest of the tree or the DOM, not to state. */
const SKIPPED = new Set(["return", "alternate", "stateNode", "_owner", "_store"]);

interface FiberLike {
  child: FiberLike | null;
  sibling: FiberLike | null;
  memoizedProps: unknown;
  memoizedState: unknown;
}

function holds(value: unknown, needle: string, seen: Set<object>, depth: number): boolean {
  if (typeof value === "string") return value.includes(needle);
  if (value === null || typeof value !== "object") return false;
  if (value instanceof Node || value instanceof Window || seen.has(value)) return false;
  if (depth > MAX_DEPTH) throw new Error("reactStateHolds: structure too deep to search");
  seen.add(value);
  return Object.entries(value).some(
    ([key, inner]) => !SKIPPED.has(key) && holds(inner, needle, seen, depth + 1),
  );
}

function walk(fiber: FiberLike | null, needle: string, seen: Set<object>): boolean {
  for (let at = fiber; at !== null; at = at.sibling) {
    // Hook state is a linked list: { memoizedState, next, ... }; the generic search follows it.
    if (holds(at.memoizedProps, needle, seen, 0) || holds(at.memoizedState, needle, seen, 0)) {
      return true;
    }
    if (walk(at.child, needle, seen)) return true;
  }
  return false;
}

export function reactStateHolds(needle: string): boolean {
  const seen = new Set<object>();
  return Array.from(document.body.children).some((element) => {
    const key = Object.keys(element).find((name) => name.startsWith("__reactContainer$"));
    if (key === undefined) return false;
    const root = (element as unknown as Record<string, FiberLike>)[key];
    return root !== undefined && walk(root, needle, seen);
  });
}
