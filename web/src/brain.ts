/**
 * The brain icon: seen from above, two hemispheres either side of the central fissure, each with a few folds. One definition, drawn by BrainIcon.tsx in the header and
 * copied into public/favicon.svg (a test checks the two stay the same). 32 x 32 grid; the right hemisphere is the left one mirrored about x = 16.
 */
export const BRAIN_VIEWBOX = "0 0 32 32";

/** The left hemisphere's outline, closed along the fissure at x = 14.8. */
export const LEFT_LOBE =
  "M14.8 5.4 C13.2 3.4 9.6 3.4 7.9 5.7 C5.4 6.1 3.6 8.4 4.1 10.9 C2.4 12.5 2.4 15.4 4 17 C3.4 19.4 4.7 21.8 7 22.6 C8 25 11.2 26.2 13.3 24.8 C13.9 25.4 14.4 25.7 14.8 25.8 Z";

/** The folds of the left hemisphere, as open curves. */
export const LEFT_FOLDS = [
  "M7.9 5.7 C9.4 7.2 9.4 9.2 8 10.4",
  "M4.1 10.9 C6.2 11 7.6 12.2 7.9 13.9 C8.1 15.2 7.2 16.2 5.8 16.6",
  "M4 17 C5.8 17.6 6.9 18.8 6.9 20.4",
  "M7 22.6 C8.6 21.8 10.2 22.2 10.9 23.6",
  "M14.8 9.6 C12.8 9.4 11.2 10.4 11 12.2 C10.8 13.8 12 15 13.6 15.2",
  "M14.8 18.6 C13 18.6 11.4 19.4 10.8 20.8",
];

/** The right hemisphere is the left one mirrored about the vertical centre line (x = 16). */
export const MIRROR = "translate(32 0) scale(-1 1)";
