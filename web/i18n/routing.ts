import { defineRouting } from "next-intl/routing";

// Polish first: the prompt is written in Polish and measured only on Polish press, so the
// language the product was built for is the one it defaults to. English translates the
// interface, not the analysis — see FindingList, which marks the model's own words `lang="pl"`.
export const routing = defineRouting({
  locales: ["pl", "en"],
  defaultLocale: "pl",
});
