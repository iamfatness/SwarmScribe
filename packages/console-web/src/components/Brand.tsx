// The SwarmScribe mark and wordmark, drawn inline (the CSP allows no image from elsewhere,
// and an inline SVG costs no request). The shapes are the public site's logo.svg; the
// colours come from classes in styles/shell.css, never from a style attribute.

export function BrandMark({ size = 30 }: { size?: number }) {
  return (
    <svg
      className="brand-mark"
      width={size}
      height={size}
      viewBox="0 0 64 64"
      aria-hidden="true"
      focusable="false"
    >
      <path className="brand-hex" d="M32 4 56.25 18v28L32 60 7.75 46V18z" />
      <path className="brand-nib" d="M32 51 21 30l4-14h14l4 14z" />
      <path className="brand-slit" d="M32 51V33" />
      <circle className="brand-eye" cx="32" cy="30.5" r="3" />
    </svg>
  );
}

/** "Swarm" in bold sans, "Scribe" in italic serif amber: one word to a screen reader. */
export function Wordmark() {
  return (
    <span className="wordmark">
      Swarm<em>Scribe</em>
    </span>
  );
}
