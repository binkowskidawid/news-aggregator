import { useFormatter, useTranslations } from "next-intl";

import type { ArticleDetail } from "@/lib/api";

/**
 * The disclosure required of generated content by Article 50 of Regulation (EU) 2024/1689.
 *
 * The API sends the fact (`ai_generated`, the model, the prompt version, the timestamp) and
 * never the sentence; the wording is a translated string and lives here with the others.
 */
export function ProvenanceNote({ provenance }: { provenance: ArticleDetail["provenance"] }) {
  const t = useTranslations("provenance");
  const format = useFormatter();

  return (
    <p className="text-xs text-neutral-500 dark:text-neutral-500">
      {t("generated")}{" "}
      {t("details", {
        model: provenance.model_name,
        version: provenance.prompt_version,
        date: format.dateTime(new Date(provenance.analysed_at), {
          dateStyle: "medium",
          timeStyle: "short",
        }),
      })}
    </p>
  );
}
