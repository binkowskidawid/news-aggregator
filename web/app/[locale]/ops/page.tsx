import { getTranslations } from "next-intl/server";
import { notFound } from "next/navigation";

import { operatorPanel } from "@/lib/session";

/**
 * The operator panel.
 *
 * Everything here is a statement about the model and the pipeline, never about a publisher.
 * That constraint is the product's, not this page's: `fetch_errors` is grouped by source
 * because a source whose failures spike is a broken selector, and there is deliberately no
 * finding density per outlet anywhere in `/ops/overview` for this page to render.
 *
 * `notFound()` for a reader without the role, matching what the API already answered. A
 * "forbidden" page here would confirm the panel exists to exactly the person the API
 * declined to confirm it to.
 */

/** Table headers are the SQL column names, and the cells are what the database holds:
 * `emotional_load`, `failed_permanent`, `v1.1.3`. Translating them would rename the things
 * an operator has to grep for — the chrome around them is what carries a language. */
function DataTable({ rows }: { rows: readonly Record<string, unknown>[] }) {
  if (rows.length === 0) return <p className="text-sm text-neutral-500">—</p>;
  const columns = Object.keys(rows[0]);

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-max text-left text-sm tabular-nums">
        <thead>
          <tr className="border-b border-neutral-300 dark:border-neutral-700">
            {columns.map((column) => (
              <th key={column} className="py-1.5 pr-4 font-medium">
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={index} className="border-b border-neutral-200 dark:border-neutral-800">
              {columns.map((column) => (
                <td key={column} className="py-1.5 pr-4">
                  {row[column] === null || row[column] === undefined ? "—" : String(row[column])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-2">
      <h2 className="text-base font-semibold">{title}</h2>
      {children}
    </section>
  );
}

/** The overview's sections are untyped counters, so each is narrowed at the point of use
 * rather than by a schema nothing else consumes. */
const rowsOf = (value: unknown): Record<string, unknown>[] =>
  Array.isArray(value) ? (value as Record<string, unknown>[]) : [];

export default async function OpsPage() {
  const panel = await operatorPanel();
  if (!panel) notFound();

  const { checks, overview } = panel;
  const t = await getTranslations("ops");
  const corpus = (overview.corpus ?? {}) as Record<string, unknown>;
  const failing = checks.filter((check) => !check.passing);

  return (
    <div className="space-y-8">
      <div className="space-y-1">
        <h1 className="text-xl font-semibold">{t("heading")}</h1>
        <p className="max-w-prose text-sm text-neutral-600 dark:text-neutral-400">
          {t("explainer")}
        </p>
      </div>

      <Section title={t("checks")}>
        <p
          className={
            failing.length === 0
              ? "text-sm text-neutral-600 dark:text-neutral-400"
              : "text-sm font-medium text-red-700 dark:text-red-400"
          }
        >
          {failing.length === 0 ? t("allPassing") : t("someFailing", { count: failing.length })}
        </p>
        <ul className="space-y-2">
          {checks.map((check) => (
            <li
              key={check.name}
              className="border-b border-neutral-200 pb-2 text-sm last:border-0 dark:border-neutral-800"
            >
              <p className="flex items-baseline justify-between gap-4">
                <span className="font-medium">{check.name}</span>
                <span
                  className={
                    check.passing
                      ? "text-neutral-500"
                      : "font-medium text-red-700 dark:text-red-400"
                  }
                >
                  {check.passing ? t("passing") : t("failures", { count: check.failures })}
                </span>
              </p>
              <p className="text-neutral-500 dark:text-neutral-500">{check.why}</p>
            </li>
          ))}
        </ul>
      </Section>

      <Section title={t("corpus")}>
        <DataTable rows={[corpus]} />
      </Section>

      <Section title={t("promptVersions")}>
        <p className="max-w-prose text-sm text-neutral-600 dark:text-neutral-400">
          {t("promptVersionsWhy")}
        </p>
        <DataTable rows={rowsOf(overview.prompt_versions)} />
      </Section>

      <Section title={t("queue")}>
        <DataTable rows={rowsOf(overview.queue)} />
      </Section>

      <Section title={t("findingTypes")}>
        <DataTable rows={rowsOf(overview.finding_types)} />
      </Section>

      <Section title={t("drift")}>
        <p className="max-w-prose text-sm text-neutral-600 dark:text-neutral-400">
          {t("driftWhy")}
        </p>
        <DataTable rows={rowsOf(overview.drift)} />
      </Section>

      <Section title={t("fetchErrors")}>
        <DataTable rows={rowsOf(overview.fetch_errors)} />
      </Section>
    </div>
  );
}
