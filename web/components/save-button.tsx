"use client";

import { useTranslations } from "next-intl";
import { useState } from "react";

import { useRouter } from "@/i18n/navigation";
import { mutate, succeeded } from "@/lib/mutate";

/**
 * Save or unsave one article.
 *
 * The state is optimistic and **reverted by hand** when the request fails. It cannot be
 * reconciled by `router.refresh()`: `useState` takes the prop once, so a later render with a
 * corrected `saved` would be ignored, and the button would sit there claiming the opposite of
 * what the database holds. A bookmark that lags a round trip behind the tap reads as broken,
 * which is why the optimistic update stays — but it has to be undone rather than assumed.
 */
export function SaveButton({ articleId, saved }: { articleId: string; saved: boolean }) {
  const t = useTranslations("account");
  const router = useRouter();
  const [on, setOn] = useState(saved);

  async function toggle() {
    const next = !on;
    setOn(next);

    const status = await mutate(`/api/me/saved/${articleId}`, {
      method: next ? "PUT" : "DELETE",
    });

    if (!succeeded(status)) {
      setOn(!next);
      return;
    }
    router.refresh();
  }

  return (
    <button
      type="button"
      onClick={toggle}
      aria-pressed={on}
      className="rounded border border-neutral-300 px-3 py-1.5 text-sm underline-offset-4 hover:underline dark:border-neutral-700"
    >
      {t(on ? "unsave" : "save")}
    </button>
  );
}
