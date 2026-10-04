// Looks for a string in the React state the app holds right now: every mounted component's
// props and hook state. Used to prove a secret did not outlive its dialog. The DOM, and what
// a closure captured, are checked elsewhere; this reaches what React itself keeps.

interface FiberLike {
  child: FiberLike | null;
  sibling: FiberLike | null;
  memoizedProps: unknown;
  memoizedState: unknown;
}

function holds(value: unknown, needle: string, seen: Set<object>, depth: number): boolean {
  if (typeof value === "string") return value.includes(needle);
  if (value === null || typeof value !== "object" || depth > 12) return false;
  if (value instanceof Node || value instanceof Window || seen.has(value)) return false;
  seen.add(value);
  return Object.values(value).some((inner) => holds(inner, needle, seen, depth + 1));
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
