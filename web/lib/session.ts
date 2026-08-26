/**
 * Reads made on behalf of the signed-in person.
 *
 * Server-only by construction: `next/headers` refuses to load in a client component, so no
 * `server-only` guard is needed on top of it.
 */

import { cookies } from "next/headers";
import { cache } from "react";

import {
  readAuthed,
  readOps,
  type Account,
  type Category,
  type Check,
  type FeedItem,
  type Subscriptions,
} from "./api";

/** Rows of counters read straight from SQL. The API declines to type them and so does this:
 * a shape per section would be a schema whose only consumer is a table that prints it. */
export type Overview = Record<string, unknown>;

/** The whole cookie header, forwarded verbatim.
 *
 * Only `session` is ever read by the API, but rebuilding the header from one cookie would
 * silently drop anything added later, and the API is the only thing on the other end. */
async function header(): Promise<string> {
  const store = await cookies();
  return store
    .getAll()
    .map(({ name, value }) => `${name}=${value}`)
    .join("; ");
}

/** Who is signed in, or null. Every account page starts here, and so does the layout, on
 * every request.
 *
 * `cache` deduplicates within one render pass: the layout and the page both ask, and without
 * it every page view would cost two identical round trips to `/auth/me`. It is per-request
 * memoisation, not a cache across requests — a signed-out reader is never served a signed-in
 * answer. */
export const currentAccount = cache(async (): Promise<Account | null> => {
  return readAuthed<Account>("/auth/me", await header());
});

export const savedArticles = cache(async (): Promise<FeedItem[] | null> => {
  return readAuthed<FeedItem[]>("/me/saved", await header());
});

export async function subscribedCategories(): Promise<Category[] | null> {
  const answer = await readAuthed<Subscriptions>("/me/subscriptions", await header());
  return answer?.categories ?? null;
}

/** The operator panel's two reads, together. Null from either means the page does not exist
 * for whoever asked — see `readOps`. */
export async function operatorPanel(): Promise<{ checks: Check[]; overview: Overview } | null> {
  const cookie = await header();
  const [checks, overview] = await Promise.all([
    readOps<Check[]>("/ops/checks", cookie),
    readOps<Overview>("/ops/overview", cookie),
  ]);

  return checks && overview ? { checks, overview } : null;
}
