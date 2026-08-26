import { getTranslations } from "next-intl/server";

import { FeedControls } from "@/components/feed-controls";
import { FeedEntry } from "@/components/feed-entry";
import { ReliabilityNote } from "@/components/reliability-note";
import { Link } from "@/i18n/navigation";
import { CATEGORIES, fetchFeed, type Category } from "@/lib/api";

const PAGE_SIZE = 20;

/** The whole view is derived from the query string, so a page is linkable and the browser's
 * back button works without any client state. Anything unparseable falls back to the
 * unfiltered feed rather than erroring: a hand-edited URL is not an exceptional condition. */
function parse(params: Record<string, string | string[] | undefined>) {
  const single = (key: string) => (Array.isArray(params[key]) ? params[key][0] : params[key]);
  const category = single("category");
  const hasFindings = single("has_findings");
  const offset = Number.parseInt(single("offset") ?? "", 10);

  return {
    category: CATEGORIES.includes(category as Category) ? (category as Category) : undefined,
    hasFindings: hasFindings === "true" ? true : hasFindings === "false" ? false : undefined,
    offset: Number.isFinite(offset) && offset > 0 ? offset : 0,
  };
}

export default async function FeedPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { category, hasFindings, offset } = parse(await searchParams);
  const t = await getTranslations("feed");
  const feed = await fetchFeed({ category, hasFindings, limit: PAGE_SIZE, offset });

  const page = (next: number) => {
    const query = new URLSearchParams({ offset: String(next) });
    if (category) query.set("category", category);
    if (hasFindings !== undefined) query.set("has_findings", String(hasFindings));
    return `/?${query}`;
  };

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold">{t("heading")}</h1>

      <FeedControls category={category} hasFindings={hasFindings} />
      <ReliabilityNote variant="short" />

      {feed.items.length === 0 ? (
        <p className="py-12 text-center text-neutral-500 dark:text-neutral-500">{t("empty")}</p>
      ) : (
        <>
          <ul>
            {feed.items.map((item) => (
              <FeedEntry key={item.id} item={item} />
            ))}
          </ul>

          <nav className="flex items-center justify-between text-sm">
            {offset > 0 ? (
              <Link
                href={page(Math.max(offset - PAGE_SIZE, 0))}
                className="underline underline-offset-4 hover:no-underline"
              >
                {t("previous")}
              </Link>
            ) : (
              <span />
            )}

            <span className="text-neutral-500 dark:text-neutral-500">
              {t("showing", {
                from: offset + 1,
                to: offset + feed.items.length,
                total: feed.total,
              })}
            </span>

            {offset + PAGE_SIZE < feed.total ? (
              <Link
                href={page(offset + PAGE_SIZE)}
                className="underline underline-offset-4 hover:no-underline"
              >
                {t("next")}
              </Link>
            ) : (
              <span />
            )}
          </nav>
        </>
      )}
    </div>
  );
}
