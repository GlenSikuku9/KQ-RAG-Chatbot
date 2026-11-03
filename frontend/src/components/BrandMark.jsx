import "./BrandMark.css";

// One semantic mark keeps the KQ initials aligned consistently throughout
// passenger and administrator surfaces while each context owns its colours.
export function BrandMark({ className = "" }) {
  return (
    <span className={`kq-logo ${className}`.trim()} aria-hidden="true">
      KQ
    </span>
  );
}
