import assert from "node:assert/strict";
import { test } from "node:test";

import { segment, type Span } from "./segments.ts";

const rebuild = (text: string, spans: Span[]) =>
  segment(text, spans)
    .map((piece) => piece.text)
    .join("");

const marked = (text: string, spans: Span[]) =>
  segment(text, spans)
    .filter((piece) => piece.marks.length > 0)
    .map((piece) => piece.text);

test("nothing reported leaves the text in one piece", () => {
  assert.deepEqual(segment("Rada podjęła uchwałę", []), [
    { text: "Rada podjęła uchwałę", marks: [] },
  ]);
});

test("the marked piece is exactly the slice the server reported", () => {
  const title = "Wstrząsająca relacja świadka";
  const span = { start: 13, end: 20 };

  assert.deepEqual(marked(title, [span]), [title.slice(span.start, span.end)]);
});

test("concatenating the segments returns the original string", () => {
  const title = "Spektakularna akcja ukraińskich komandosów. Rosjanie wpadli w pułapkę";
  const spans = [
    { start: 0, end: 42 },
    { start: 43, end: 68 },
    { start: 14, end: 19 },
  ];

  assert.equal(rebuild(title, spans), title);
});

test("overlapping spans give one piece carrying both marks", () => {
  // 68 such pairs exist in the corpus, so this is the shape the renderer meets, not a
  // hypothetical. Wrapping each span on its own would nest elements and lose text.
  const pieces = segment("abcdef", [
    { start: 0, end: 4 },
    { start: 2, end: 6 },
  ]);

  assert.deepEqual(pieces, [
    { text: "ab", marks: [0] },
    { text: "cd", marks: [0, 1] },
    { text: "ef", marks: [1] },
  ]);
});

test("a span reaching the last character keeps it", () => {
  const pieces = segment("uchwała", [{ start: 3, end: 7 }]);

  assert.deepEqual(marked("uchwała", [{ start: 3, end: 7 }]), ["wała"]);
  assert.equal(pieces.map((piece) => piece.text).join(""), "uchwała");
});

test("offsets are code points, so a character outside the BMP does not shift them", () => {
  // Python counts "👍" as one character and JavaScript's string indexing as two. Slicing the
  // raw string here would return "es" rather than "test" and nothing would look wrong.
  const title = "👍 test";

  assert.deepEqual(marked(title, [{ start: 2, end: 6 }]), ["test"]);
});

test("an out-of-range span is clamped rather than dropping text", () => {
  assert.equal(rebuild("krótki", [{ start: 2, end: 999 }]), "krótki");
  assert.deepEqual(marked("krótki", [{ start: 2, end: 999 }]), ["ótki"]);
});

test("an empty span marks nothing", () => {
  assert.deepEqual(segment("tekst", [{ start: 2, end: 2 }]), [{ text: "tekst", marks: [] }]);
});
