/** The Jaynes logo (bicameral_brain.png): a brain seen from above, two hemispheres joined by circuitry. The header copy has a transparent background, so it sits on the page in either theme. */
export const LOGO_URL = "/bicameral_brain_transparent.png";
const ASPECT = 208 / 216;           // the image's width over its height

export function BrainIcon({ size = 24 }: { size?: number }) {
  return <img className="brain" src={LOGO_URL} width={Math.round(size * ASPECT)} height={size} alt="Jaynes" />;
}
