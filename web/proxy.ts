import createMiddleware from "next-intl/middleware";

import { routing } from "./i18n/routing";

export default createMiddleware(routing);

export const config = {
  // `api` is excluded deliberately: those paths are rewritten to FastAPI and must not be
  // given a locale prefix. Everything with a file extension is a static asset.
  matcher: "/((?!api|_next|_vercel|.*\\..*).*)",
};
