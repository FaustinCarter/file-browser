import { useCallback, useRef } from "react";

/**
 * Returns a stable function identity that always invokes the latest `fn`.
 * Lets effects call it without listing it as a dependency.
 */
export function useCallbackRef<T extends (...args: any[]) => any>(fn: T): T {
  const ref = useRef(fn);
  ref.current = fn;
  return useCallback(((...args: any[]) => ref.current(...args)) as T, []);
}
