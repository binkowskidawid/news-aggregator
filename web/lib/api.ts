/**
 * The only place that talks to the API.
 *
 * Requests go to a relative `/api/...` path so the browser and the server both stay on one
 * origin — see the rewrite in next.config.ts. Server Components have no origin to be
 * relative to, so they get one from the environment.
 *
 * Every type here comes from lib/api-types.ts, generated from the schema FastAPI emits
 * (`make api-types`). Nothing about the contract is retyped by hand.
 */

import type { components } from "./api-types";

export type FeedItem = components["schemas"]["FeedItem"];
export type Feed = components["schemas"]["Feed"];
export type ArticleDetail = components["schemas"]["ArticleDetail"];
export type Finding = components["schemas"]["FindingOut"];
export type Category = components["schemas"]["Category"];
export type Assessment = components["schemas"]["Assessment"];
export type ManipulationType = components["schemas"]["ManipulationType"];
export type Account = components["schemas"]["Account"];
export type Subscriptions = components["schemas"]["Subscriptions"];
export type Check = components["schemas"]["Check"];

// The filter needs the values at runtime and a TypeScript union has none — it is erased.
// Typing the array as `Category[]` means a value renamed or dropped in domain/analysis.py
// fails the build after `make api-types`; a value *added* there still has to be added here,
// and the message catalogues need its label anyway.
export const CATEGORIES: readonly Category[] = [
  "polityka",
  "kultura",
  "technologia",
  "sport",
  "biznes",
  "geopolityka",
  "zdrowie",
  "inne",
];

const ORIGIN = process.env.API_URL ?? "http://127.0.0.1:8000";

class ApiError extends Error {
  constructor(readonly status: number, path: string) {
    super(`${path} answered ${status}`);
  }
}

async function read<T>(path: string): Promise<T> {
  // `no-store`: the corpus changes as the analyser works through the queue, and a reader
  // reloading to see whether an article was analysed should not be served a cached "no".
  const response = await fetch(`${ORIGIN}${path}`, { cache: "no-store" });
  if (!response.ok) throw new ApiError(response.status, path);
  return (await response.json()) as T;
}

export type FeedQuery = {
  category?: Category;
  hasFindings?: boolean;
  limit: number;
  offset: number;
};

export function fetchFeed({ category, hasFindings, limit, offset }: FeedQuery): Promise<Feed> {
  const query = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (category) query.set("category", category);
  if (hasFindings !== undefined) query.set("has_findings", String(hasFindings));

  return read<Feed>(`/feed?${query}`);
}

/** Null rather than a throw for 404: an article with no production analysis is a normal
 * answer the page renders as "not found", not a failure of the request. */
export async function fetchArticle(id: string): Promise<ArticleDetail | null> {
  try {
    return await read<ArticleDetail>(`/articles/${id}`);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null;
    throw error;
  }
}

/**
 * A read on behalf of a signed-in person.
 *
 * The browser reaches the API through the rewrite and sends the session cookie itself. A
 * Server Component has no such origin — it calls FastAPI directly — so the cookie has to be
 * handed over explicitly. The header is passed in rather than read here, because reading it
 * needs `next/headers` and this module is imported by client components for its types.
 *
 * 401 answers null: a signed-out reader is a state every account page renders, not a fault.
 */
export async function readAuthed<T>(path: string, cookie: string): Promise<T | null> {
  const response = await fetch(`${ORIGIN}${path}`, {
    cache: "no-store",
    headers: { cookie },
  });
  if (response.status === 401) return null;
  if (!response.ok) throw new ApiError(response.status, path);
  return (await response.json()) as T;
}

/**
 * A read on an operator endpoint.
 *
 * 404 answers null alongside 401, because that is the answer `require_admin` gives a signed-in
 * reader without the role — the panel does not confirm its own existence to someone who may
 * not use it. Collapsing the two here is what keeps that property from being undone by a
 * front end that renders "forbidden" where the API said "no such page".
 */
export async function readOps<T>(path: string, cookie: string): Promise<T | null> {
  const response = await fetch(`${ORIGIN}${path}`, {
    cache: "no-store",
    headers: { cookie },
  });
  if (response.status === 401 || response.status === 404) return null;
  if (!response.ok) throw new ApiError(response.status, path);
  return (await response.json()) as T;
}
