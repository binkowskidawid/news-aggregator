import { getTranslations } from "next-intl/server";

import { FeedEntry } from "@/components/feed-entry";
import { redirect } from "@/i18n/navigation";
import { savedArticles } from "@/lib/session";

export default async function SavedPage({ params }: { params: Promise<{ locale: string }> }) {
  const { locale } = await params;
  const saved = await savedArticles();
  if (saved === null) return redirect({ href: "/signin", locale });

  const t = await getTranslations("account");

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold">{t("saved")}</h1>

      {saved.length === 0 ? (
        <p className="py-12 text-center text-neutral-500 dark:text-neutral-500">
          {t("savedEmpty")}
        </p>
      ) : (
        <ul>
          {saved.map((item) => (
            <FeedEntry key={item.id} item={item} />
          ))}
        </ul>
      )}
    </div>
  );
}
