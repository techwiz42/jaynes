import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { BrainIcon, LOGO_URL } from "./BrainIcon";

describe("the logo", () => {
  const files = Object.keys(import.meta.glob("../public/*.png"));

  it("is drawn from the supplied image, at its own proportions", () => {
    const html = renderToStaticMarkup(<BrainIcon size={26} />);
    expect(html).toContain(`src="${LOGO_URL}"`);
    expect(html).toContain('height="26"');
    expect(html).toContain('width="25"');
    expect(html).toContain('alt="Jaynes"');
  });

  it("has the image files the header and the favicon point to", () => {
    expect(files).toContain(`../public${LOGO_URL}`);
    expect(files).toContain("../public/bicameral_brain.png");
  });
});
