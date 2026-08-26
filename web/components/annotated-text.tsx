import { segment, type Span } from "@/lib/segments";

type Props = {
  text: string;
  spans: readonly Span[];
  /** Position of the first span within the article's whole list, so the numbers a reader
   * sees run 1, 2, 3 across the headline and the lead rather than restarting. */
  firstNumber: number;
  className?: string;
};

/**
 * The headline or lead with the reported fragments underlined.
 *
 * A dotted underline and a small number, not a coloured background: the average reported
 * fragment is 39 of a headline's 60-90 characters, and a highlighted half-headline reads as
 * a verdict. Under prompt v1.1.3 the underline lands on the right fragment 47% of the time,
 * so the visual weight has to stay at the level of "look here", which is all the product can
 * honestly claim.
 *
 * Every mark carries `aria-describedby` pointing at its explanation below, so the connection
 * survives without the visual one.
 */
export function AnnotatedText({ text, spans, firstNumber, className }: Props) {
  const pieces = segment(text, spans);

  return (
    <span className={className}>
      {pieces.map((piece, index) =>
        piece.marks.length === 0 ? (
          <span key={index}>{piece.text}</span>
        ) : (
          <mark
            key={index}
            aria-describedby={piece.marks.map((mark) => `finding-${firstNumber + mark}`).join(" ")}
            className="bg-transparent text-inherit decoration-dotted decoration-2 underline underline-offset-4 decoration-amber-700 dark:decoration-amber-400"
          >
            {piece.text}
            <sup className="ms-0.5 text-[0.6em] font-semibold text-amber-800 dark:text-amber-300">
              {piece.marks.map((mark) => firstNumber + mark).join(",")}
            </sup>
          </mark>
        ),
      )}
    </span>
  );
}
