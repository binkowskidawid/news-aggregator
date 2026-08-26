/**
 * Turning reported spans into renderable pieces of text.
 *
 * This is the only non-trivial logic in the front end and the one place it can lie. A
 * finding carries `start`/`end` indexing the original title or lead; the highlight a reader
 * sees is that slice, and the quote validator upstream exists to guarantee it matches what
 * the model reported. Anything that shifts an offset here shows a confident underline
 * beneath words nothing was said about.
 *
 * Two properties hold by construction and are asserted in segments.test.ts:
 *   - concatenating every segment returns the original string, character for character
 *   - overlapping spans produce one segment carrying both marks, never nested elements
 */

export type Span = { start: number; end: number };

export type Segment = {
  text: string;
  /** Indices into the spans array. Empty means ordinary text. */
  marks: number[];
};

/**
 * Cut `text` at every span boundary and label each piece with the spans covering it.
 *
 * Offsets are counted in code points, matching Python's `str` indexing on the server.
 * JavaScript strings are UTF-16, so a single emoji would otherwise count as two and every
 * offset after it would land one character early. The current corpus holds no such
 * character; an operator pointing this at their own sources may.
 */
export function segment(text: string, spans: readonly Span[]): Segment[] {
  const characters = Array.from(text);
  const clamp = (index: number) => Math.min(Math.max(index, 0), characters.length);

  const boundaries = new Set<number>([0, characters.length]);
  for (const span of spans) {
    if (span.end <= span.start) continue;
    boundaries.add(clamp(span.start));
    boundaries.add(clamp(span.end));
  }

  const cuts = [...boundaries].sort((left, right) => left - right);
  const segments: Segment[] = [];

  for (let index = 0; index < cuts.length - 1; index += 1) {
    const from = cuts[index];
    const to = cuts[index + 1];
    segments.push({
      text: characters.slice(from, to).join(""),
      marks: spans.reduce<number[]>(
        (found, span, position) =>
          span.start <= from && span.end >= to && span.end > span.start
            ? [...found, position]
            : found,
        [],
      ),
    });
  }

  return segments;
}
