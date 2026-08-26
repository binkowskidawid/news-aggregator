import { useTranslations } from "next-intl";

import type { Finding } from "@/lib/api";

type Props = {
  findings: readonly Finding[];
  sourceUrl: string;
};

/**
 * What the model said, framed as an observation rather than a verdict.
 *
 * `confidence` is deliberately absent. The model returns 0.85-0.92 on almost every finding
 * while measured precision under prompt v1.1.3 is 42%; printing that number next to a claim
 * that is more often wrong than right would be the most misleading element on the page.
 *
 * `explanation` and `neutral_alternative` are the model's own Polish text — the prompt is
 * written in Polish and measured only on Polish press. They are marked `lang="pl"` so that
 * an English reader is told what they are looking at, and so a screen reader pronounces
 * them correctly instead of reading Polish with English phonetics.
 */
export function FindingList({ findings, sourceUrl }: Props) {
  const t = useTranslations("detail");
  const types = useTranslations("finding");

  return (
    <ol className="mt-8 space-y-6 border-t border-neutral-200 pt-6 dark:border-neutral-800">
      {findings.map((finding, index) => (
        <li key={`${finding.field}-${finding.start}`} id={`finding-${index + 1}`} className="flex gap-3">
          <span
            aria-hidden
            className="mt-0.5 shrink-0 text-sm font-semibold text-amber-800 dark:text-amber-300"
          >
            {index + 1}
          </span>
          <div className="space-y-2">
            <p className="text-sm text-neutral-600 dark:text-neutral-400">
              {t("noticed")} — <span className="font-medium">{types(finding.type)}</span>
            </p>

            <blockquote
              lang="pl"
              className="border-s-2 border-neutral-300 ps-3 text-neutral-900 dark:border-neutral-700 dark:text-neutral-100"
            >
              {finding.quote}
            </blockquote>

            {finding.explanation ? (
              <p lang="pl" className="text-sm text-neutral-700 dark:text-neutral-300">
                {finding.explanation}{" "}
                <span className="text-xs text-neutral-500 dark:text-neutral-500">
                  ({t("modelOutput")})
                </span>
              </p>
            ) : null}

            {finding.neutral_alternative ? (
              <p className="text-sm text-neutral-600 dark:text-neutral-400">
                {t("neutralAlternative")}{" "}
                <span lang="pl" className="italic">
                  {finding.neutral_alternative}
                </span>
              </p>
            ) : null}

            <a
              href={sourceUrl}
              rel="noreferrer nofollow"
              target="_blank"
              className="inline-block text-sm underline underline-offset-2 hover:no-underline"
            >
              {t("checkSource")}
            </a>
          </div>
        </li>
      ))}
    </ol>
  );
}
