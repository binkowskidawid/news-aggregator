import { NextIntlClientProvider, hasLocale } from "next-intl";
import { getTranslations } from "next-intl/server";
import { Geist } from "next/font/google";
import { notFound } from "next/navigation";
import type { Metadata } from "next";
import type { ReactNode } from "react";

import { LanguageSwitch } from "@/components/language-switch";
import { SessionNav } from "@/components/session-nav";
import { Link } from "@/i18n/navigation";
import { routing } from "@/i18n/routing";
import { currentAccount } from "@/lib/session";

import "../globals.css";

// `latin-ext` is not optional here: ą, ę, ł, ń, ś, ź and ż live in that subset, and without
// it every Polish headline renders half in Geist and half in the fallback face.
const geist = Geist({ variable: "--font-geist-sans", subsets: ["latin", "latin-ext"] });

export async function generateMetadata({
  params,
}: {
  params: Promise<{ locale: string }>;
}): Promise<Metadata> {
  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: "app" });

  return { title: t("name"), description: t("tagline") };
}

export default async function LocaleLayout({
  children,
  params,
}: {
  children: ReactNode;
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  if (!hasLocale(routing.locales, locale)) notFound();

  const t = await getTranslations({ locale, namespace: "app" });
  const account = await currentAccount();

  return (
    <html lang={locale} className={`${geist.variable} h-full antialiased`}>
      <body className="mx-auto flex min-h-full max-w-3xl flex-col px-4 font-sans">
        <NextIntlClientProvider>
          <header className="flex items-start justify-between gap-4 border-b border-neutral-200 py-6 dark:border-neutral-800">
            <div className="space-y-1">
              <Link href="/" className="text-base font-semibold underline-offset-4 hover:underline">
                {t("name")}
              </Link>
              <p className="max-w-prose text-xs text-neutral-500 dark:text-neutral-500">
                {t("tagline")}
              </p>
            </div>
            <div className="flex shrink-0 flex-col items-end gap-2 text-xs">
              <LanguageSwitch />
              <SessionNav email={account?.email ?? null} role={account?.role ?? null} />
            </div>
          </header>

          <main className="flex-1 py-6">{children}</main>
        </NextIntlClientProvider>
      </body>
    </html>
  );
}
