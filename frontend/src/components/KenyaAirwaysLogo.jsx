import "./KenyaAirwaysLogo.css";

const LOGO_VARIANTS = {
  white: { path: "/images/kenya-airways-logo.svg", width: 292, height: 56 },
  colour: { path: "/images/kenya-airways-logo-colour.svg", width: 299, height: 68 },
};

// The official artwork already contains the airline name and tagline. Keeping
// it as an image prevents accidental recolouring or distortion of the SVG.
export function KenyaAirwaysLogo({ className = "", variant = "white" }) {
  // White artwork belongs on fixed navy admin surfaces; the full-colour
  // artwork belongs directly on passenger-facing white and light surfaces.
  const logo = LOGO_VARIANTS[variant] || LOGO_VARIANTS.white;
  return (
    <span className={`kenya-airways-logo kenya-airways-logo--${variant} ${className}`.trim()}>
      <img
        src={logo.path}
        alt="Kenya Airways — The Pride of Africa"
        width={logo.width}
        height={logo.height}
      />
    </span>
  );
}
