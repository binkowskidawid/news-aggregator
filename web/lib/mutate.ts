/**
 * The one way the browser writes to the API.
 *
 * Every mutation goes to a relative `/api/...` path so the request stays same-origin and the
 * browser handles the session cookie itself — the same reason `lib/api.ts` exists for reads.
 *
 * The point of the wrapper is the failure. `fetch` rejects only on a network fault and
 * resolves happily on a 500, so a call site that awaits it and moves on reports success for
 * both. Returning the status forces each caller to decide, and `0` names the case a status
 * cannot: the request never reached the server.
 */

export const UNREACHABLE = 0;

export async function mutate(path: string, init?: RequestInit): Promise<number> {
  try {
    return (await fetch(path, init)).status;
  } catch {
    return UNREACHABLE;
  }
}

export const succeeded = (status: number) => status >= 200 && status < 300;

/** JSON body plus the header, which the API requires and which is easy to forget. */
export const withJson = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { "content-type": "application/json" },
  body: JSON.stringify(body),
});
