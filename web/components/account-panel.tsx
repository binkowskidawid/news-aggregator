"use client";

import { useTranslations } from "next-intl";
import { useState } from "react";

import { useRouter } from "@/i18n/navigation";
import { CATEGORIES, type Category } from "@/lib/api";
import { mutate, succeeded, withJson } from "@/lib/mutate";

/**
 * Category subscriptions and account deletion.
 *
 * Subscriptions are sent as the whole set, because that is what the endpoint takes: a
 * settings screen knows what the person wants to end up with, and a diff protocol would be
 * two round trips to describe one intention.
 */
export function AccountPanel({ subscribed }: { subscribed: readonly Category[] }) {
  const t = useTranslations("account");
  const categories = useTranslations("category");
  const router = useRouter();

  const [chosen, setChosen] = useState<readonly Category[]>(subscribed);
  // Three states, not two: "saved", "failed" and "nothing said yet" are different things,
  // and collapsing the last two makes a failure look exactly like never having pressed it.
  const [outcome, setOutcome] = useState<"saved" | "failed" | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);

  function toggle(category: Category) {
    setOutcome(null);
    setChosen((current) =>
      current.includes(category)
        ? current.filter((one) => one !== category)
        : [...current, category],
    );
  }

  async function save() {
    setBusy(true);
    const status = await mutate("/api/me/subscriptions", withJson("PUT", { categories: chosen }));
    setOutcome(succeeded(status) ? "saved" : "failed");
    setBusy(false);
    router.refresh();
  }

  async function remove() {
    setBusy(true);
    const status = await mutate("/api/me", { method: "DELETE" });

    // Only leave on success. Navigating away regardless would tell someone their account was
    // erased on the strength of a request that failed — the one claim here that has to be
    // true, because they will not come back to check.
    if (!succeeded(status)) {
      setOutcome("failed");
      setBusy(false);
      return;
    }
    router.refresh();
    router.push("/");
  }

  return (
    <div className="space-y-10">
      <section className="space-y-4">
        <div className="space-y-1">
          <h2 className="text-base font-semibold">{t("subscriptionsHeading")}</h2>
          <p className="max-w-prose text-sm text-neutral-600 dark:text-neutral-400">
            {t("subscriptionsExplainer")}
          </p>
        </div>

        <fieldset className="flex flex-wrap gap-x-4 gap-y-2">
          <legend className="sr-only">{t("subscriptionsHeading")}</legend>
          {CATEGORIES.map((category) => (
            <label key={category} className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={chosen.includes(category)}
                onChange={() => toggle(category)}
                className="size-4"
              />
              {categories(category)}
            </label>
          ))}
        </fieldset>

        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={save}
            disabled={busy}
            className="rounded bg-neutral-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50 dark:bg-neutral-100 dark:text-neutral-900"
          >
            {t("saveSubscriptions")}
          </button>
          <span
            aria-live="polite"
            className={
              outcome === "failed"
                ? "text-sm text-red-700 dark:text-red-400"
                : "text-sm text-neutral-600 dark:text-neutral-400"
            }
          >
            {outcome === "saved" ? t("subscriptionsSaved") : null}
            {outcome === "failed" ? t("failed") : null}
          </span>
        </div>
      </section>

      <section className="space-y-3 border-t border-neutral-200 pt-6 dark:border-neutral-800">
        <h2 className="text-base font-semibold">{t("deleteHeading")}</h2>
        <p className="max-w-prose text-sm text-neutral-600 dark:text-neutral-400">
          {t("deleteExplainer")}
        </p>

        {confirming ? (
          <div className="flex flex-wrap items-center gap-3">
            <button
              type="button"
              onClick={remove}
              disabled={busy}
              className="rounded bg-red-700 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
            >
              {t("deleteConfirm")}
            </button>
            <button
              type="button"
              onClick={() => setConfirming(false)}
              className="text-sm underline underline-offset-4 hover:no-underline"
            >
              {t("cancel")}
            </button>
            <span role="alert" className="text-sm text-red-700 dark:text-red-400">
              {outcome === "failed" ? t("failed") : null}
            </span>
          </div>
        ) : (
          <button
            type="button"
            onClick={() => setConfirming(true)}
            className="rounded border border-red-700 px-4 py-2 text-sm font-medium text-red-700 dark:text-red-400"
          >
            {t("deleteAccount")}
          </button>
        )}
      </section>
    </div>
  );
}
