import { describe, expect, it } from "vitest";
import { LEFT_FOLDS, LEFT_LOBE } from "./brain";
import favicon from "../public/favicon.svg?raw";

describe("the brain icon", () => {
  it("is the same drawing in the page header and in the favicon", () => {
    for (const path of [LEFT_LOBE, ...LEFT_FOLDS]) expect(favicon).toContain(`d="${path}"`);
  });

  it("is mirrored for the second lobe, and a valid, self-contained SVG", () => {
    expect(favicon).toContain('transform="translate(32 0) scale(-1 1)"');
    expect(favicon.startsWith("<svg")).toBe(true);
    expect(favicon).not.toMatch(/<script|href="http/);
  });
});
