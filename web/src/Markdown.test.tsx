import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { MarkdownText, plainText } from "./Markdown";

const html = (markdown: string): string => renderToStaticMarkup(<MarkdownText>{markdown}</MarkdownText>);

describe("MarkdownText", () => {
  it("renders emphasis, lists and code", () => {
    const out = html("**Checks and Balances:** one\n\n1. first\n2. second\n\nUse `ls`.");
    expect(out).toContain("<strong>Checks and Balances:</strong>");
    expect(out).toContain("<ol>");
    expect(out).toContain("<code>ls</code>");
    expect(out).not.toContain("**");
  });

  it("renders a table", () => {
    expect(html("| a | b |\n| - | - |\n| 1 | 2 |")).toContain("<table>");
  });

  it("does not render raw HTML from the model", () => {
    const out = html('hi <script>alert(1)</script> <img src=x onerror="alert(1)">');
    expect(out).not.toContain("<script");
    expect(out).not.toContain("<img");
  });

  it("opens links in a new tab and drops javascript: addresses", () => {
    expect(html("[site](https://example.com)")).toContain('target="_blank"');
    expect(html("[bad](javascript:alert(1))")).not.toContain("javascript:");
  });

  it("copes with markdown that is not finished yet, as while streaming", () => {
    expect(() => html("**bold so far, and `code ▍")).not.toThrow();
  });
});

describe("plainText", () => {
  it("turns markdown into words for a preview", () => {
    expect(plainText("1. **Checks and Balances:** by requiring two chambers\n2. [link](http://x.y) here")).toBe("Checks and Balances: by requiring two chambers link here");
  });
});
