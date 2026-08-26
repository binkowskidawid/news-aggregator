import { useTranslations } from "next-intl";

import { Link } from "@/i18n/navigation";
import { CATEGORIES, type Category } from "@/lib/api";

type Props = {
  category?: Category;
  hasFindings?: boolean;
};

/**
 * Category and findings filters, as links rather than client state.
 *
 * Everything the feed shows is derived from the query string, so the browser's own history,
 * the back button and a copied URL all work without a line of state management.
 */
export function FeedControls({ category, hasFindings }: Props) {
  const t = useTranslations("feed");
  const names = useTranslations("category");

  const chip = (active: boolean) =>
    [
      "rounded-full border px-3 py-1 text-sm transition-colors",
      active
        ? "border-neutral-900 bg-neutral-900 text-white dark:border-neutral-100 dark:bg-neutral-100 dark:text-neutral-900"
        : "border-neutral-300 hover:border-neutral-500 dark:border-neutral-700 dark:hover:border-neutral-500",
    ].join(" ");

  const href = (next: { category?: Category; hasFindings?: boolean }) => {
    const query: Record<string, string> = {};
    if (next.category) query.category = next.category;
    if (next.hasFindings !== undefined) query.has_findings = String(next.hasFindings);
    return { pathname: "/" as const, query };
  };

  return (
    <div className="space-y-3">
      <nav aria-label={names("all")} className="flex flex-wrap gap-2">
        <Link href={href({ hasFindings })} className={chip(category === undefined)}>
          {names("all")}
        </Link>
        {CATEGORIES.map((value) => (
          <Link
            key={value}
            href={href({ category: value, hasFindings })}
            className={chip(category === value)}
          >
            {names(value)}
          </Link>
        ))}
      </nav>

      <nav aria-label={t("onlyReported")} className="flex flex-wrap gap-2">
        <Link href={href({ category })} className={chip(hasFindings === undefined)}>
          {t("everything")}
        </Link>
        <Link
          href={href({ category, hasFindings: true })}
          className={chip(hasFindings === true)}
        >
          {t("onlyReported")}
        </Link>
        <Link
          href={href({ category, hasFindings: false })}
          className={chip(hasFindings === false)}
        >
          {t("onlyClean")}
        </Link>
      </nav>
    </div>
  );
}
