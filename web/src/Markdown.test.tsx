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

describe("math", () => {
  it("draws inline and display math with KaTeX", () => {
    expect(html("so $x^2$ here")).toContain('class="katex"');
    expect(html("$$\\int_0^1 x\\,dx$$")).toContain("katex-display");
  });

  it("draws the \\( \\) and \\[ \\] styles", () => {
    expect(html("so \\(a+b\\) here")).toContain('class="katex"');
    expect(html("see \\[a+b\\] now")).toContain("katex-display");
  });

  it("leaves prices as text", () => {
    const out = html("It costs $5 and $10 in total.");
    expect(out).not.toContain("katex");
    expect(out).toContain("$5 and $10");
  });

  it("does not fail on math that will not parse", () => {
    expect(() => html("$\\frac{1}{$")).not.toThrow();
    expect(() => html("$\\notacommand{x}$")).not.toThrow();
  });

  it("does not let math run commands that fetch or link", () => {
    const out = html("$\\href{javascript:alert(1)}{click}$ and $\\includegraphics{http://x/y.png}$");
    expect(out).not.toMatch(/href=/i);                      // the equation's own source text may appear in its accessibility annotation; a link or image may not
    expect(out).not.toContain("<a ");
    expect(out).not.toContain("<img");
  });

  it("leaves dollar signs in code alone", () => {
    expect(html("run `echo $HOME and $PATH`")).not.toContain("katex");
  });
});

describe("plainText", () => {
  it("turns markdown into words for a preview", () => {
    expect(plainText("1. **Checks and Balances:** by requiring two chambers\n2. [link](http://x.y) here")).toBe("Checks and Balances: by requiring two chambers link here");
  });
});
