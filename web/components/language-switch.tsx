"use client";

import { useLocale, useTranslations } from "next-intl";

import { Link, usePathname } from "@/i18n/navigation";
import { routing } from "@/i18n/routing";

/**
 * Switching language keeps the reader where they are.
 *
 * The one client component in this front end, and only because the current path is needed:
 * sending someone back to the feed because they wanted to read the same page in English is
 * the kind of small rudeness that makes a language switch go unused.
 */
export function LanguageSwitch() {
  const active = useLocale();
  const pathname = usePathname();
  const t = useTranslations("app");

  return (
    <nav aria-label={t("languageLabel")} className="flex gap-2 text-xs">
      {routing.locales.map((locale) => (
        <Link
          key={locale}
          href={pathname}
          locale={locale}
          aria-current={locale === active ? "true" : undefined}
          className={
            locale === active
              ? "font-semibold underline underline-offset-4"
              : "text-neutral-500 underline-offset-4 hover:underline dark:text-neutral-400"
          }
        >
          {locale.toUpperCase()}
        </Link>
      ))}
    </nav>
  );
}
