import { getFormatter, getTranslations } from "next-intl/server";
import { notFound } from "next/navigation";

import { AnnotatedText } from "@/components/annotated-text";
import { FindingList } from "@/components/finding-list";
import { ProvenanceNote } from "@/components/provenance-note";
import { ReliabilityNote } from "@/components/reliability-note";
import { SaveButton } from "@/components/save-button";
import { Link } from "@/i18n/navigation";
import { fetchArticle, type Finding } from "@/lib/api";
import { currentAccount, savedArticles } from "@/lib/session";

/** The API orders findings by `field, quote_start`, and 'lead' sorts before 'title'. The
 * reader meets the headline first, so the page renumbers them in reading order and hands
 * FindingList the same sequence — otherwise the mark above a headline carries one number and
 * its explanation below carries another. */
const inField = (findings: readonly Finding[], field: "title" | "lead") =>
  findings.filter((finding) => finding.field === field);

export default async function ArticlePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const article = await fetchArticle(id);
  if (!article) notFound();

  const t = await getTranslations("detail");
  const assessments = await getTranslations("assessment");
  const categories = await getTranslations("category");
  const format = await getFormatter();

  const titleFindings = inField(article.findings, "title");
  const leadFindings = inField(article.findings, "lead");
  const inReadingOrder = [...titleFindings, ...leadFindings];

  // Gated on the account rather than reached for blindly: `savedArticles` would answer 401
  // for every anonymous reader, which is a round trip spent to learn what the layout already
  // knows. `currentAccount` is memoised per request, so this one is free.
  //
  // ponytail: the whole saved list to decide one boolean. Fine for a reader's bookmarks;
  // if that list ever grows past a page, this wants `GET /me/saved/{id}` answering 204/404.
  const saved = (await currentAccount()) ? await savedArticles() : null;

  return (
    <article className="space-y-6">
      <Link href="/" className="text-sm underline underline-offset-4 hover:no-underline">
        {t("backToFeed")}
      </Link>

      <p className="flex flex-wrap items-center gap-x-2 text-xs text-neutral-500 dark:text-neutral-500">
        <span>{article.source}</span>
        {article.published_at ? (
          <>
            <span aria-hidden>·</span>
            <time dateTime={article.published_at}>
              {format.dateTime(new Date(article.published_at), { dateStyle: "medium" })}
            </time>
          </>
        ) : null}
        {article.category ? (
          <>
            <span aria-hidden>·</span>
            <span>{categories(article.category)}</span>
          </>
        ) : null}
        {article.overall_assessment ? (
          <>
            <span aria-hidden>·</span>
            <span>{assessments(article.overall_assessment)}</span>
          </>
        ) : null}
      </p>

      <h1 className="text-2xl font-semibold leading-snug">
        <AnnotatedText
          text={article.title}
          spans={titleFindings}
          firstNumber={1}
          className="[&>mark]:decoration-amber-700"
        />
      </h1>

      {article.lead ? (
        <p className="text-lg leading-relaxed text-neutral-800 dark:text-neutral-200">
          <AnnotatedText
            text={article.lead}
            spans={leadFindings}
            firstNumber={titleFindings.length + 1}
          />
        </p>
      ) : null}

      {article.findings.length > 0 ? (
        <>
          <ReliabilityNote variant="long" />
          <h2 className="sr-only">{t("findingsHeading")}</h2>
          <FindingList findings={inReadingOrder} sourceUrl={article.url} />
        </>
      ) : (
        <p className="rounded-md border border-neutral-200 p-3 text-sm text-neutral-600 dark:border-neutral-800 dark:text-neutral-400">
          {t("nothingReported")}
        </p>
      )}

      <div className="space-y-3 border-t border-neutral-200 pt-4 dark:border-neutral-800">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <a
            href={article.url}
            rel="noreferrer nofollow"
            target="_blank"
            className="text-sm underline underline-offset-4 hover:no-underline"
          >
            {t("openSource")}
          </a>
          {saved ? (
            <SaveButton articleId={article.id} saved={saved.some((one) => one.id === article.id)} />
          ) : null}
        </div>
        <ProvenanceNote provenance={article.provenance} />
      </div>
    </article>
  );
}
