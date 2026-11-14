export const REPOSITORY = "https://github.com/techwiz42/jaynes";

/** The page footer: who made it, how to reach them, the year, and the source. */
export function Footer() {
  return (
    <footer className="site-footer">
      <span>© 2026 Peter Sisk</span>
      <a href="mailto:pete@cyberiad.ai">pete@cyberiad.ai</a>
      <a href={REPOSITORY} target="_blank" rel="noopener noreferrer">
        GitHub
      </a>
    </footer>
  );
}
