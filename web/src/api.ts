import createClient from "openapi-fetch";
import { useEffect, useState } from "react";
import type { components, paths } from "./schema";

export type Schemas = components["schemas"];
export type Problem = Schemas["Problem"];

export const api = createClient<paths>({ baseUrl: window.location.origin });

/**
 * A failed response, or a network failure (no response at all). `code` is the problem's code, or "unknown"
 * when the body is not a problem (an HTML 502 from a proxy, an empty 5xx).
 */
export type ApiError = { status: number; code: string } | { network: true };

type Call<T> = (signal: AbortSignal) => Promise<{ data?: T; error?: unknown; response: Response }>;

function asError(body: unknown, status: number): ApiError {
  const code = typeof body === "object" && body !== null ? (body as Partial<Problem>).code : undefined;
  return { status, code: typeof code === "string" ? code : "unknown" };
}

export interface ApiState<T> {
  data?: T;
  error?: ApiError;
  loading: boolean;
}

/**
 * Runs `call` whenever `deps` change (or not at all while `call` is null), aborting the previous request.
 * Keeps the last data while a new request is in flight, so tables don't flash empty.
 */
export function useApi<T>(call: Call<T> | null, deps: readonly unknown[]): ApiState<T> {
  const [state, setState] = useState<ApiState<T>>({ loading: call !== null });

  useEffect(() => {
    if (call === null) return;
    const ctl = new AbortController();
    setState((s) => ({ data: s.data, loading: true }));
    call(ctl.signal).then(
      ({ data, error, response }) => {
        if (ctl.signal.aborted) return;
        setState(response.ok ? { data, loading: false } : { error: asError(error, response.status), loading: false });
      },
      () => {
        if (!ctl.signal.aborted) setState({ error: { network: true }, loading: false });
      },
    );
    return () => ctl.abort();
  }, deps); // the caller's inputs to `call`, not `call` itself (a new closure every render)

  return state;
}
