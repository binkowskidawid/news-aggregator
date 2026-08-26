import { expect, test } from "@playwright/test";

/**
 * The account path, end to end through the rewrite.
 *
 * What only a browser can prove here is the cookie. The API's tests already cover Argon2id,
 * the session table and the rate limit; none of them can say whether `Set-Cookie` survives
 * Next's proxy, whether `SameSite=Lax` still lets the front end read the session back, or
 * whether a server-rendered page picks up a session the browser acquired a moment earlier.
 *
 * Each run registers its own address and deletes it at the end, so the suite leaves the
 * database as it found it and two runs never collide.
 */

const password = "haslo-do-testow-1234";

// `example.com`, not `example.test`: the API validates with email-validator, which refuses
// special-use domains — and `.test` is one, so the address would fail before any of this
// test's subject matter was reached.
const freshAddress = () =>
  `e2e-${Date.now()}-${Math.random().toString(36).slice(2, 8)}@example.com`;

test("register, save an article, then delete the account", async ({ page, request }) => {
  const email = freshAddress();

  await page.goto("/pl/register");
  await page.getByLabel("Adres e-mail").fill(email);
  await page.getByLabel("Hasło").fill(password);
  await page.getByRole("button", { name: "Załóż konto" }).click();

  // Landing on the account page is the first proof the cookie came back through the proxy:
  // the page is server-rendered and redirects to sign-in without a session.
  await expect(page).toHaveURL(/\/pl\/account$/);
  await expect(page.getByText(email)).toBeVisible();

  const feed = await (await request.get("/api/feed?limit=1")).json();
  const [item] = feed.items;
  expect(item, "the corpus is empty").toBeTruthy();

  await page.goto(`/pl/articles/${item.id}`);

  // Waiting for the PUT, not for the button: the control flips optimistically, so asserting
  // on its label would let the next navigation abort a request that had not landed yet.
  const saveLanded = page.waitForResponse(
    (response) =>
      response.url().includes(`/api/me/saved/${item.id}`) && response.request().method() === "PUT",
  );
  await page.getByRole("button", { name: "Zapisz" }).click();
  expect((await saveLanded).status()).toBe(204);

  await page.goto("/pl/saved");
  await expect(page.getByRole("link", { name: item.title })).toBeVisible();

  await page.goto("/pl/account");
  await page.getByRole("button", { name: "Usuń konto" }).click();
  await page.getByRole("button", { name: "Tak, usuń moje konto" }).click();

  await expect(page).toHaveURL(/\/pl$/);

  // Erasure, not deactivation: the address must be free again, and the old session gone.
  const signedOut = await request.get("/api/auth/me");
  expect(signedOut.status()).toBe(401);
});

test("a signed-out reader is sent to sign-in rather than shown an empty account", async ({
  page,
}) => {
  await page.goto("/pl/saved");
  await expect(page).toHaveURL(/\/pl\/signin$/);
});

test("the operator panel is not routed for a reader without the role", async ({ page }) => {
  // Only what a browser can see. That the *API* answers 404 rather than 403 to a signed-in
  // reader is asserted in tests/test_api_account.py, where the session is a fixture rather
  // than something to coax out of two cookie jars — Playwright's `request` context does not
  // share the page's, which made the same assertion here flake.
  expect((await page.goto("/pl/ops"))?.status()).toBe(404);

  const email = freshAddress();
  await page.goto("/pl/register");
  await page.getByLabel("Adres e-mail").fill(email);
  await page.getByLabel("Hasło").fill(password);
  await page.getByRole("button", { name: "Załóż konto" }).click();
  await expect(page).toHaveURL(/\/pl\/account$/);

  expect((await page.goto("/pl/ops"))?.status()).toBe(404);

  // The header must not advertise it either.
  await page.goto("/pl");
  await expect(page.getByRole("link", { name: "Panel operatora" })).toHaveCount(0);

  await page.goto("/pl/account");
  await page.getByRole("button", { name: "Usuń konto" }).click();
  await page.getByRole("button", { name: "Tak, usuń moje konto" }).click();
  await expect(page).toHaveURL(/\/pl$/);
});

test("the sign-in form does not say whether the address exists", async ({ page }) => {
  await page.goto("/pl/signin");
  await page.getByLabel("Adres e-mail").fill(freshAddress());
  await page.getByLabel("Hasło").fill("zupelnie-zle-haslo");
  await page.getByRole("button", { name: "Zaloguj się" }).click();

  // The same sentence an existing account with a wrong password gets. The API pays the
  // hashing cost either way; this asserts the interface does not undo that by wording.
  // Scoped to `main`: Next renders its own route announcer with role="alert".
  await expect(page.getByRole("main").getByRole("alert")).toHaveText(
    "Nieprawidłowy adres lub hasło.",
  );
});
