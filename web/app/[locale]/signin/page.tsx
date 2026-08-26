import { getTranslations } from "next-intl/server";

import { AuthForm } from "@/components/auth-form";
import { Link, redirect } from "@/i18n/navigation";
import { currentAccount } from "@/lib/session";

export default async function SignInPage({ params }: { params: Promise<{ locale: string }> }) {
  const { locale } = await params;
  if (await currentAccount()) return redirect({ href: "/account", locale });

  const t = await getTranslations("account");

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold">{t("signIn")}</h1>
      <AuthForm mode="login" />
      <p className="text-sm text-neutral-600 dark:text-neutral-400">
        {t.rich("noAccount", {
          link: (chunks) => (
            <Link href="/register" className="underline underline-offset-4 hover:no-underline">
              {chunks}
            </Link>
          ),
        })}
      </p>
    </div>
  );
}
