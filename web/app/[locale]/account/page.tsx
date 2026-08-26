import { getTranslations } from "next-intl/server";

import { AccountPanel } from "@/components/account-panel";
import { redirect } from "@/i18n/navigation";
import { currentAccount, subscribedCategories } from "@/lib/session";

export default async function AccountPage({ params }: { params: Promise<{ locale: string }> }) {
  const { locale } = await params;
  const account = await currentAccount();
  // `return`: next-intl's `redirect` is typed `never`, but TypeScript narrows on such a
  // call only when the callee carries an explicit annotation, and this one is destructured
  // from `createNavigation`. Returning it narrows through plain control flow instead.
  if (!account) return redirect({ href: "/signin", locale });

  const subscribed = (await subscribedCategories()) ?? [];
  const t = await getTranslations("account");

  return (
    <div className="space-y-8">
      <div className="space-y-1">
        <h1 className="text-xl font-semibold">{t("account")}</h1>
        <p className="text-sm text-neutral-500 dark:text-neutral-500">{account.email}</p>
      </div>

      <AccountPanel subscribed={subscribed} />

      <section className="space-y-3 border-t border-neutral-200 pt-6 dark:border-neutral-800">
        <h2 className="text-base font-semibold">{t("exportHeading")}</h2>
        <p className="max-w-prose text-sm text-neutral-600 dark:text-neutral-400">
          {t("exportExplainer")}
        </p>
        {/* A plain link, not a fetch: the browser already sends the session cookie to a
            same-origin request and saves the answer without any of it passing through React. */}
        <a
          href="/api/me/export"
          download="account.json"
          className="inline-block text-sm underline underline-offset-4 hover:no-underline"
        >
          {t("exportDownload")}
        </a>
      </section>
    </div>
  );
}
