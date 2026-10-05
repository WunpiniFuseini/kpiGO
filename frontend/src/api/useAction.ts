import { useCallback, useEffect, useRef, useState } from "react";

import type { ActionName, Input, Output } from "./actions";
import { ApiError, invoke } from "./client";

export type QueryState<T> =
  | { status: "loading" }
  | { status: "error"; error: ApiError }
  | { status: "ready"; data: T; refreshing: boolean };

function asApiError(error: unknown): ApiError {
  if (error instanceof ApiError) return error;
  return new ApiError(0, "client_error", "Something went wrong in this screen.", String(error), "local");
}

/** Run a read-only action and keep its result; `reload` runs it again, keeping the last good data. */
export function useQuery<N extends ActionName>(
  name: N,
  input: Input<N>,
): [QueryState<Output<N>>, () => void] {
  const [state, setState] = useState<QueryState<Output<N>>>({ status: "loading" });
  const [tick, setTick] = useState(0);
  const key = JSON.stringify(input);
  const inputRef = useRef(input);
  inputRef.current = input;

  useEffect(() => {
    let live = true;
    setState((prev) => (prev.status === "ready" ? { ...prev, refreshing: true } : { status: "loading" }));
    invoke(name, inputRef.current).then(
      (data) => live && setState({ status: "ready", data, refreshing: false }),
      (error: unknown) => live && setState({ status: "error", error: asApiError(error) }),
    );
    return () => {
      live = false;
    };
  }, [name, key, tick]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  return [state, reload];
}

export type MutationState<T> =
  | { status: "idle" }
  | { status: "running" }
  | { status: "error"; error: ApiError }
  | { status: "done"; data: T };

/** Run a mutating action on demand. */
export function useMutation<N extends ActionName>(
  name: N,
): [MutationState<Output<N>>, (input: Input<N>) => Promise<Output<N> | undefined>, () => void] {
  const [state, setState] = useState<MutationState<Output<N>>>({ status: "idle" });
  const run = useCallback(
    async (input: Input<N>) => {
      setState({ status: "running" });
      try {
        const data = await invoke(name, input);
        setState({ status: "done", data });
        return data;
      } catch (error) {
        setState({ status: "error", error: asApiError(error) });
        return undefined;
      }
    },
    [name],
  );
  const reset = useCallback(() => setState({ status: "idle" }), []);
  return [state, run, reset];
}
