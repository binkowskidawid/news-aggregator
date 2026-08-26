import { useTranslations } from "next-intl";

/**
 * The measured accuracy of a finding, stated where findings are shown.
 *
 * A reader deciding how much weight to give an underline needs the numbers in the same view
 * as the underline. Putting them in a documentation file nobody opens would be a way of
 * technically disclosing them.
 *
 * The long variant carries both: how often the underline lands on the right fragment (47%)
 * and how often the technique named next to it is also right (42%). They are two different
 * claims and the page makes both, so it states both.
 *
 * The numbers live in the message catalogues because they are prose, and they describe one
 * measured configuration — `gemma4:latest` under prompt v1.1.3 on Polish press, 64 articles
 * the prompt was never tuned against. An operator who swaps the model has to replace them;
 * `MODEL_CARD.md` says so, and nothing here can detect it.
 */
export function ReliabilityNote({ variant }: { variant: "short" | "long" }) {
  const t = useTranslations("reliability");

  return (
    <p
      className={
        variant === "short"
          ? "text-xs text-neutral-500 dark:text-neutral-500"
          : "rounded-md border border-amber-300/60 bg-amber-50/60 p-3 text-sm text-neutral-700 dark:border-amber-500/30 dark:bg-amber-500/5 dark:text-neutral-300"
      }
    >
      {t(variant)}
    </p>
  );
}
