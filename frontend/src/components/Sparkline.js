import React from 'react';

/**
 * Compact, dependency-free SVG sparkline.
 * Props:
 *   points:  array of numbers (price points, oldest -> newest)
 *   stroke:  CSS color (defaults to neon cyan)
 *   width:   intrinsic SVG width (scales via CSS)
 *   height:  intrinsic SVG height
 */
const Sparkline = ({ points = [], stroke = 'var(--neon-cyan)', width = 120, height = 36 }) => {
  if (!points || points.length < 2) {
    return <div className="sparkline-empty" aria-hidden="true">—</div>;
  }

  const min = Math.min(...points);
  const max = Math.max(...points);
  const range = max - min || 1;
  const stepX = width / (points.length - 1);

  const path = points
    .map((value, index) => {
      const x = index * stepX;
      const y = height - ((value - min) / range) * (height - 4) - 2;
      return `${index === 0 ? 'M' : 'L'} ${x.toFixed(2)} ${y.toFixed(2)}`;
    })
    .join(' ');

  // Shaded area under the curve for richer visual.
  const areaPath = `${path} L ${width.toFixed(2)} ${height} L 0 ${height} Z`;
  const fillId = `spark-fill-${Math.abs(hashCode(points.join(',')))}`;

  return (
    <div className="sparkline" role="img" aria-label={`Sparkline of ${points.length} price points`}>
      <svg viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none">
        <defs>
          <linearGradient id={fillId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={stroke} stopOpacity="0.45" />
            <stop offset="100%" stopColor={stroke} stopOpacity="0" />
          </linearGradient>
        </defs>
        <path d={areaPath} fill={`url(#${fillId})`} />
        <path d={path} stroke={stroke} />
      </svg>
    </div>
  );
};

function hashCode(s) {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = ((h << 5) - h + s.charCodeAt(i)) | 0;
  return h;
}

export default Sparkline;
