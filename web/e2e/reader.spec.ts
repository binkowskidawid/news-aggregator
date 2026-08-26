import { expect, test } from "@playwright/test";

/**
 * The reader's path, asserted against the corpus rather than against fixtures.
 *
 * The first test is the reason this file exists. Unit tests prove the segmentation function
 * cuts a string correctly; they cannot prove that the string reaching it is the one the API
 * reported, or that the offsets survived JSON, the rewrite and React. Only a browser holding
 * the rendered page next to the API's own answer can say that.
 */

type Finding = {
  field: "title" | "lead";
  start: number;
  end: number;
  quote: string;
};

/** Code points, matching Python's `str` indexing on the server — not `String.slice`, which
 * counts UTF-16 units and would disagree the moment a headline carries an emoji. */
const slice = (text: string, start: number, end: number) =>
  Array.from(text).slice(start, end).join("");

test("the highlighted fragment is the slice the API reported", async ({ page, request }) => {
  const feed = await (await request.get("/api/feed?has_findings=true&limit=1")).json();
  const [item] = feed.items;
  expect(item, "the corpus holds no analysed article with a finding").toBeTruthy();

  const article = await (await request.get(`/api/articles/${item.id}`)).json();
  const findings: Finding[] = [
    ...article.findings.filter((finding: Finding) => finding.field === "title"),
    ...article.findings.filter((finding: Finding) => finding.field === "lead"),
  ];

  await page.goto(`/pl/articles/${item.id}`);

  // The superscript is a sibling node inside <mark>, so only the text nodes are the quote.
  // An overlapping pair splits one finding across several marks, which is why the marks are
  // collected by the id they point at rather than positionally.
  const rendered = await page.evaluate(() =>
    [...document.querySelectorAll("main mark")].map((mark) => ({
      ids: (mark.getAttribute("aria-describedby") ?? "").split(" "),
      text: [...mark.childNodes]
        .filter((node) => node.nodeType === Node.TEXT_NODE)
        .map((node) => node.textContent ?? "")
        .join(""),
    })),
  );

  expect(rendered.length).toBeGreaterThan(0);

  findings.forEach((finding, index) => {
    const highlighted = rendered
      .filter((mark) => mark.ids.includes(`finding-${index + 1}`))
      .map((mark) => mark.text)
      .join("");

    expect(highlighted, `finding ${index + 1} of ${item.id}`).toBe(
      slice(article[finding.field], finding.start, finding.end),
    );
    expect(highlighted).toBe(finding.quote);
  });
});

test("an article with nothing reported says so without implying a clean bill", async ({
  page,
  request,
}) => {
  const feed = await (await request.get("/api/feed?has_findings=false&limit=1")).json();
  const [item] = feed.items;
  expect(item).toBeTruthy();

  await page.goto(`/pl/articles/${item.id}`);

  await expect(page.locator("main mark")).toHaveCount(0);
  await expect(page.getByText("brak zgłoszenia")).toBeVisible();
});

test("the findings filter narrows the feed and the total with it", async ({ page, request }) => {
  const everything = await (await request.get("/api/feed?limit=1")).json();
  const reported = await (await request.get("/api/feed?has_findings=true&limit=1")).json();

  expect(reported.total).toBeLessThan(everything.total);

  await page.goto("/pl?has_findings=true");

  await expect(page.getByText(`z ${reported.total}`)).toBeVisible();
  // Scoped to the list: "bez zgłoszeń" is also the wording of the filter chip that turns
  // this view off, and matching that would assert nothing about the rows.
  await expect(page.locator("main ul").getByText("bez zgłoszeń")).toHaveCount(0);
});

test("the English interface leaves the model's Polish text marked as Polish", async ({
  page,
  request,
}) => {
  const feed = await (await request.get("/api/feed?has_findings=true&limit=1")).json();
  const [item] = feed.items;

  await page.goto(`/en/articles/${item.id}`);

  await expect(page.locator("html")).toHaveAttribute("lang", "en");
  await expect(page.getByText("The model flagged this fragment").first()).toBeVisible();
  await expect(page.locator('main [lang="pl"]').first()).toBeVisible();
});
