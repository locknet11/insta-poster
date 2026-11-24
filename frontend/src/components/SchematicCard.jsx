import React from 'react';

function CornerMark({ position }) {
  return (
    <svg
      className={`crop-mark crop-${position}`}
      width="10"
      height="10"
      viewBox="0 0 10 10"
      fill="none"
      aria-hidden="true"
    >
      <path d="M0.5 10V0.5H10" stroke="currentColor" strokeWidth="1" />
    </svg>
  );
}

export default function SchematicCard({
  as: Component = 'div',
  label,
  right,
  children,
  className = '',
  bodyClassName = '',
  highlight = false,
  noPadding = false,
  ...rest
}) {
  return (
    <Component
      className={`schematic-card ${highlight ? 'is-highlighted' : ''} ${className}`}
      {...rest}
    >
      <CornerMark position="tl" />
      <CornerMark position="tr" />
      <CornerMark position="bl" />
      <CornerMark position="br" />

      {(label || right) && (
        <div className="schematic-card-header">
          {label ? <span className="schematic-eyebrow">{label}</span> : <span />}
          {right && <div className="schematic-card-right">{right}</div>}
        </div>
      )}

      <div className={`schematic-card-body ${noPadding ? 'no-pad' : ''} ${bodyClassName}`}>
        {children}
      </div>
    </Component>
  );
}
