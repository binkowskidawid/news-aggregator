"use client";

import { useTranslations } from "next-intl";

import { Link, useRouter } from "@/i18n/navigation";
import { mutate } from "@/lib/mutate";

/**
 * The header's account corner.
 *
 * Takes the account as a prop rather than fetching it: the layout already resolved the
 * session server-side, and asking again from the browser would make every page flicker
 * through a signed-out state it is not in.
 */
export function SessionNav({ email, role }: { email: string | null; role: string | null }) {
  const t = useTranslations("account");
  const ops = useTranslations("ops");
  const router = useRouter();

  async function signOut() {
    // Refresh regardless of the answer, and deliberately: the header is rendered from the
    // session the server can see, so if the sign-out failed the refresh puts back a header
    // saying so. Navigating to a "signed out" state the server disagrees with is the one
    // outcome worth avoiding.
    await mutate("/api/auth/logout", { method: "POST" });
    router.refresh();
    router.push("/");
  }

  if (email === null) {
    return (
      <Link href="/signin" className="underline-offset-4 hover:underline">
        {t("signIn")}
      </Link>
    );
  }

  return (
    <div className="flex items-center gap-3">
      {/* Hidden from everyone else, but the hiding is cosmetic: the page itself answers 404
          to a reader without the role, because the API does. */}
      {role === "admin" ? (
        <Link href="/ops" className="underline-offset-4 hover:underline">
          {ops("heading")}
        </Link>
      ) : null}
      <Link href="/saved" className="underline-offset-4 hover:underline">
        {t("saved")}
      </Link>
      <Link href="/account" className="underline-offset-4 hover:underline">
        {t("account")}
      </Link>
      <button type="button" onClick={signOut} className="underline-offset-4 hover:underline">
        {t("signOut")}
      </button>
    </div>
  );
}
