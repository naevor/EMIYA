/**
 * LorenzPanel - attractor canvas plus energy/focus/openness bars and raw x/y/z.
 *
 * Props:
 *   trail:    [{ x, y, z, energy, focus, openness, timestamp }]
 *   current:  { energy, focus, openness, raw_x, raw_y, raw_z }
 *   onToggleAscii: () => void
 *   asciiMode: bool
 */

import { useEffect, useRef } from 'react';

const MINT = '#3DDBB1';
const MINT_DIM = 'rgba(61, 219, 177, 0.2)';

export default function LorenzPanel({ trail, current, asciiMode, onToggleAscii }) {
  const canvasRef = useRef(null);
  const lastPointRef = useRef(null);
  const transitionRef = useRef({ from: null, to: null, startedAt: 0 });

  useEffect(() => {
    const next = trail?.[trail.length - 1];
    if (!next) return;

    const prev = lastPointRef.current ?? next;
    const changed =
      !lastPointRef.current ||
      prev.x !== next.x ||
      prev.y !== next.y ||
      prev.z !== next.z;

    if (changed) {
      transitionRef.current = {
        from: prev,
        to: next,
        startedAt: performance.now(),
      };
      lastPointRef.current = next;
    }
  }, [trail]);

  /* Canvas drawing: simple 3D-to-2D projection with smoothed current point. */
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || asciiMode) return undefined;

    const ctx = canvas.getContext('2d');
    let frame;

    const draw = (now) => {
      const dpr = window.devicePixelRatio || 1;
      const W = Math.max(1, Math.floor(canvas.offsetWidth * dpr));
      const H = Math.max(1, Math.floor(canvas.offsetHeight * dpr));

      if (canvas.width !== W || canvas.height !== H) {
        canvas.width = W;
        canvas.height = H;
      }

      ctx.clearRect(0, 0, W, H);

      if (!trail || trail.length === 0) {
        frame = requestAnimationFrame(draw);
        return;
      }

      const xs = trail.map(p => p.x ?? 0);
      const ys = trail.map(p => p.y ?? 0);
      const zs = trail.map(p => p.z ?? 0);

      const xMin = Math.min(...xs), xMax = Math.max(...xs);
      const yMin = Math.min(...ys), yMax = Math.max(...ys);
      const zMin = Math.min(...zs), zMax = Math.max(...zs);

      const xRange = xMax - xMin || 1;
      const yRange = yMax - yMin || 1;
      const zRange = zMax - zMin || 1;
      const padding = 24 * dpr;

      const project = (p) => ({
        x: padding + (((p.x ?? 0) - xMin) / xRange) * (W - padding * 2),
        y: padding + (((p.y ?? 0) - yMin) / yRange) * (H - padding * 2),
      });

      const lerpPoint = (from, to, t) => ({
        x: (from.x ?? 0) + ((to.x ?? 0) - (from.x ?? 0)) * t,
        y: (from.y ?? 0) + ((to.y ?? 0) - (from.y ?? 0)) * t,
        z: (from.z ?? 0) + ((to.z ?? 0) - (from.z ?? 0)) * t,
      });

      for (let i = 0; i < trail.length; i++) {
        const p = trail[i];
        const t = i / trail.length;
        const point = project(p);
        const zNorm = ((p.z ?? 0) - zMin) / zRange;

        const alpha = 0.05 + 0.5 * t * (0.3 + 0.7 * zNorm);
        ctx.fillStyle = `rgba(61, 219, 177, ${alpha})`;
        ctx.fillRect(point.x, point.y, 1.5 * dpr, 1.5 * dpr);
      }

      const last = trail[trail.length - 1];
      const transition = transitionRef.current;
      const progress = Math.min(1, Math.max(0, (now - transition.startedAt) / 900));
      const eased = progress * (2 - progress);
      const currentPoint = transition.from && transition.to
        ? lerpPoint(transition.from, transition.to, eased)
        : last;
      const point = project(currentPoint);
      const pulse = 1 + Math.sin(now / 180) * 0.25;

      ctx.fillStyle = MINT;
      ctx.shadowColor = MINT;
      ctx.shadowBlur = 12 * dpr;
      ctx.beginPath();
      ctx.arc(point.x, point.y, (3.2 + pulse) * dpr, 0, Math.PI * 2);
      ctx.fill();
      ctx.shadowBlur = 0;

      frame = requestAnimationFrame(draw);
    };

    frame = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(frame);
  }, [trail, asciiMode]);

  /* ASCII fallback: simple 60x24 projection. */
  const renderAscii = () => {
    if (!trail || trail.length === 0) return ' '.repeat(60).split('').map(() => '·').join('');

    const W = 60, H = 24;
    const grid = Array.from({ length: H }, () => Array(W).fill(' '));

    const xs = trail.map(p => p.x ?? 0);
    const ys = trail.map(p => p.y ?? 0);
    const xMin = Math.min(...xs), xMax = Math.max(...xs);
    const yMin = Math.min(...ys), yMax = Math.max(...ys);

    const xR = xMax - xMin || 1;
    const yR = yMax - yMin || 1;

    trail.forEach((p, i) => {
      const t = i / trail.length;
      const cx = Math.floor(((p.x - xMin) / xR) * (W - 1));
      const cy = Math.floor(((p.y - yMin) / yR) * (H - 1));
      if (cx < 0 || cx >= W || cy < 0 || cy >= H) return;
      const char = t < 0.3 ? '·' : t < 0.6 ? '∙' : t < 0.9 ? '▒' : '█';
      grid[cy][cx] = char;
    });

    /* Pulsing current point. */
    const last = trail[trail.length - 1];
    if (last) {
      const cx = Math.floor(((last.x - xMin) / xR) * (W - 1));
      const cy = Math.floor(((last.y - yMin) / yR) * (H - 1));
      if (cx >= 0 && cx < W && cy >= 0 && cy < H) {
        grid[cy][cx] = '●';
      }
    }

    return grid.map(row => row.join('')).join('\n');
  };

  const safe = (v, p = 2) => (typeof v === 'number' ? v.toFixed(p) : '—');

  return (
    <div className="panel">
      <div className="panel__header">
        <span>LORENZ STATE</span>
        <button className="panel__header-action" onClick={onToggleAscii}>
          {asciiMode ? 'CANVAS' : 'ASCII'}
        </button>
      </div>

      {asciiMode ? (
        <div className="ascii-zone">{renderAscii()}</div>
      ) : (
        <canvas ref={canvasRef} className="lorenz-canvas" />
      )}

      <div className="mood-bars">
        <div className="mood-bar">
          <span className="mood-bar__label">ENERGY</span>
          <div className="mood-bar__track">
            <div className="mood-bar__fill" style={{ width: `${(current?.energy ?? 0.5) * 100}%` }} />
          </div>
          <span className="mood-bar__value">{Math.round((current?.energy ?? 0.5) * 100)}</span>
        </div>

        <div className="mood-bar">
          <span className="mood-bar__label">FOCUS</span>
          <div className="mood-bar__track">
            <div className="mood-bar__fill" style={{ width: `${(current?.focus ?? 0.5) * 100}%` }} />
          </div>
          <span className="mood-bar__value">{Math.round((current?.focus ?? 0.5) * 100)}</span>
        </div>

        <div className="mood-bar">
          <span className="mood-bar__label">OPENNESS</span>
          <div className="mood-bar__track">
            <div className="mood-bar__fill" style={{ width: `${(current?.openness ?? 0.5) * 100}%` }} />
          </div>
          <span className="mood-bar__value">{Math.round((current?.openness ?? 0.5) * 100)}</span>
        </div>

        <div className="lorenz-raw">
          <span>x {safe(current?.raw_x)}</span>
          <span>y {safe(current?.raw_y)}</span>
          <span>z {safe(current?.raw_z)}</span>
        </div>
      </div>
    </div>
  );
}
