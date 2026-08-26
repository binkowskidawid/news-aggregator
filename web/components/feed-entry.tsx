import { useFormatter, useTranslations } from "next-intl";

import { Link } from "@/i18n/navigation";
import type { FeedItem } from "@/lib/api";

/**
 * One row of the feed.
 *
 * The count of reported fragments is the only signal carried at list level. The type is not:
 * two thirds of all findings are `emotional_load`, so a label here would say the same thing
 * on most rows while looking like it distinguished them.
 */
export function FeedEntry({ item }: { item: FeedItem }) {
  const t = useTranslations("feed");
  const categories = useTranslations("category");
  const format = useFormatter();

  return (
    <li className="border-b border-neutral-200 py-4 last:border-0 dark:border-neutral-800">
      <article className="space-y-1.5">
        <p className="flex flex-wrap items-center gap-x-2 text-xs text-neutral-500 dark:text-neutral-500">
          <span>{item.source}</span>
          {item.published_at ? (
            <>
              <span aria-hidden>·</span>
              <time dateTime={item.published_at}>
                {format.dateTime(new Date(item.published_at), { dateStyle: "medium" })}
              </time>
            </>
          ) : null}
          {item.category ? (
            <>
              <span aria-hidden>·</span>
              <span>{categories(item.category)}</span>
            </>
          ) : null}
        </p>

        <h2 className="text-lg font-medium leading-snug">
          <Link href={`/articles/${item.id}`} className="underline-offset-4 hover:underline">
            <span lang="pl">{item.title}</span>
          </Link>
        </h2>

        <p className="text-sm text-neutral-600 dark:text-neutral-400">
          {t("counted", { count: item.finding_count })}
        </p>
      </article>
    </li>
  );
}
