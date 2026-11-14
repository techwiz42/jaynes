import { describe, expect, it } from "vitest";
import { normalizeMath } from "./math";

describe("normalizeMath", () => {
  it("leaves $x$ as it is", () => {
    expect(normalizeMath("a $x^2$ b")).toBe("a $x^2$ b");
  });

  it("puts $$x$$ on its own lines so it is drawn as display math", () => {
    expect(normalizeMath("a $$x+y$$ b")).toBe("a \n\n$$\nx+y\n$$\n\n b");
    expect(normalizeMath("a\n\n$$\nx\n$$\n\nb").replace(/\n{3,}/g, "\n\n")).toBe("a\n\n$$\nx\n$$\n\nb");
  });

  it("turns \\( \\) into $ $ and \\[ \\] into a display block", () => {
    expect(normalizeMath("so \\( a+b \\) here")).toBe("so $a+b$ here");
    expect(normalizeMath("see \\[ a+b \\] now")).toBe("see \n\n$$\na+b\n$$\n\n now");
  });

  it("does not read prices as math", () => {
    expect(normalizeMath("It costs $5 and $10 in total.")).toBe("It costs \\$5 and \\$10 in total.");
    expect(normalizeMath("Save $20.")).toBe("Save \\$20.");
  });

  it("keeps math and prices apart in one text", () => {
    expect(normalizeMath("$x$ costs $5")).toBe("$x$ costs \\$5");
  });

  it("does not run math across paragraphs", () => {
    expect(normalizeMath("$a\n\nb$")).toBe("\\$a\n\nb\\$");
  });

  it("leaves escaped dollars alone", () => {
    expect(normalizeMath("pay \\$5 now")).toBe("pay \\$5 now");
  });

  it("does not touch code", () => {
    const text = "run `echo $HOME and $PATH` or\n\n```sh\ncost=$5; echo $cost\n```\n";
    expect(normalizeMath(text)).toBe(text);
  });

  it("escapes an opening $ whose closing has not arrived yet, as while streaming", () => {
    expect(normalizeMath("the area is $\\pi r^")).toBe("the area is \\$\\pi r^");
  });
});
