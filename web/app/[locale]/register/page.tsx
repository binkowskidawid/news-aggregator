import { getTranslations } from "next-intl/server";

import { AuthForm } from "@/components/auth-form";
import { Link, redirect } from "@/i18n/navigation";
import { currentAccount } from "@/lib/session";

export default async function RegisterPage({ params }: { params: Promise<{ locale: string }> }) {
  const { locale } = await params;
  if (await currentAccount()) return redirect({ href: "/account", locale });

  const t = await getTranslations("account");

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold">{t("createAccount")}</h1>
      <p className="max-w-prose text-sm text-neutral-600 dark:text-neutral-400">
        {t("registerExplainer")}
      </p>
      <AuthForm mode="register" />
      <p className="text-sm text-neutral-600 dark:text-neutral-400">
        {t.rich("haveAccount", {
          link: (chunks) => (
            <Link href="/signin" className="underline underline-offset-4 hover:no-underline">
              {chunks}
            </Link>
          ),
        })}
      </p>
    </div>
  );
}
