"use client";

import { useTranslations } from "next-intl";
import { useState, type FormEvent } from "react";

import { useRouter } from "@/i18n/navigation";
import { mutate, succeeded, withJson } from "@/lib/mutate";

/**
 * Sign-in and sign-up, which differ by one endpoint and their copy.
 *
 * A client component on purpose: the API answers with `Set-Cookie`, and letting the browser
 * store it is the whole mechanism. A Server Action would have to lift that header out of the
 * response and replay it through `cookies().set()` — hand-rolling in the authentication path
 * the one thing the platform already does correctly.
 */

const PASSWORD_FLOOR = 12;

/** The API's status codes, mapped to what a person is told. Anything unlisted falls back to
 * the generic failure: an unexpected status is a fault here, not a message to translate. */
const MESSAGES: Record<number, string> = {
  401: "wrongCredentials",
  409: "addressTaken",
  422: "invalidInput",
  429: "tooManyAttempts",
};

export function AuthForm({ mode }: { mode: "login" | "register" }) {
  const t = useTranslations("account");
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);

    const form = new FormData(event.currentTarget);
    const status = await mutate(
      `/api/auth/${mode}`,
      withJson("POST", {
        email: String(form.get("email") ?? ""),
        password: String(form.get("password") ?? ""),
      }),
    );

    if (!succeeded(status)) {
      // `setBusy(false)` before returning on every path: a form left disabled with nothing
      // said is the worst of the failure modes, and an unreachable server is the one most
      // likely to produce it.
      setError(MESSAGES[status] ?? "failed");
      setBusy(false);
      return;
    }

    // `refresh` before `push`: the header and every account page are server-rendered from
    // the cookie, and navigating without discarding the cached render shows the signed-out
    // header on the page you just signed in to.
    router.refresh();
    router.push("/account");
  }

  return (
    <form onSubmit={submit} className="max-w-sm space-y-4">
      <div className="space-y-1">
        <label htmlFor="email" className="block text-sm font-medium">
          {t("email")}
        </label>
        <input
          id="email"
          name="email"
          type="email"
          autoComplete="email"
          required
          className="w-full rounded border border-neutral-300 px-3 py-2 text-sm dark:border-neutral-700 dark:bg-neutral-900"
        />
      </div>

      <div className="space-y-1">
        <label htmlFor="password" className="block text-sm font-medium">
          {t("password")}
        </label>
        <input
          id="password"
          name="password"
          type="password"
          autoComplete={mode === "login" ? "current-password" : "new-password"}
          required
          // Only on sign-up. Enforcing the floor on sign-in would reject an older password
          // that is still valid, and tell a guesser where the boundary sits.
          minLength={mode === "register" ? PASSWORD_FLOOR : undefined}
          aria-describedby={mode === "register" ? "password-hint" : undefined}
          className="w-full rounded border border-neutral-300 px-3 py-2 text-sm dark:border-neutral-700 dark:bg-neutral-900"
        />
        {mode === "register" ? (
          <p id="password-hint" className="text-xs text-neutral-500 dark:text-neutral-500">
            {t("passwordHint", { count: PASSWORD_FLOOR })}
          </p>
        ) : null}
      </div>

      <p role="alert" aria-live="polite" className="min-h-5 text-sm text-red-700 dark:text-red-400">
        {error ? t(error) : null}
      </p>

      <button
        type="submit"
        disabled={busy}
        className="rounded bg-neutral-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50 dark:bg-neutral-100 dark:text-neutral-900"
      >
        {t(mode === "login" ? "signIn" : "createAccount")}
      </button>
    </form>
  );
}
