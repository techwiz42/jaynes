import { BRAIN_VIEWBOX, LEFT_FOLDS, LEFT_LOBE, MIRROR } from "./brain";

/** The brain, from above: two hemispheres and the fissure between them. Drawn in the text colour, so it follows light and dark mode. */
export function BrainIcon({ size = 24 }: { size?: number }) {
  const hemisphere = (
    <>
      <path d={LEFT_LOBE} />
      {LEFT_FOLDS.map((fold) => (
        <path key={fold} d={fold} />
      ))}
    </>
  );
  return (
    <svg className="brain" width={size} height={size} viewBox={BRAIN_VIEWBOX} fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" role="img" aria-label="Jaynes">
      <g>{hemisphere}</g>
      <g transform={MIRROR}>{hemisphere}</g>
    </svg>
  );
}
