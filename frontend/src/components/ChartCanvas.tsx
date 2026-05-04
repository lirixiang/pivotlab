import { useMemo } from "react";
import type { Candle, Level } from "../types";

type Props = {
  candles: Candle[];
  levels: Level[];
};

const W = 1000;
const H_PRICE = 460;
const H_VOL = 90;
const PAD_L = 30;
const PAD_R = 30;

export function ChartCanvas({ candles, levels }: Props) {
  const { mappedCandles, priceToY, last } = useMemo(() => {
    if (!candles.length) {
      return { mappedCandles: [] as any[], priceToY: (_: number) => 0, last: 0 };
    }
    const lows = candles.map((c) => c.low);
    const highs = candles.map((c) => c.high);
    const minP = Math.min(...lows, ...levels.map((l) => l.price)) * 0.99;
    const maxP = Math.max(...highs, ...levels.map((l) => l.price)) * 1.01;
    const range = maxP - minP || 1;
    const xStart = PAD_L;
    const xEnd = W - PAD_R;
    const step = (xEnd - xStart) / Math.max(1, candles.length - 1);
    const priceToY = (p: number) => 12 + ((maxP - p) / range) * (H_PRICE - 24);
    const mapped = candles.map((c, i) => {
      const x = xStart + i * step;
      return {
        x,
        c,
        oY: priceToY(c.open),
        cY: priceToY(c.close),
        hY: priceToY(c.high),
        lY: priceToY(c.low),
        up: c.close >= c.open,
      };
    });
    return { mappedCandles: mapped, priceToY, last: candles[candles.length - 1].close };
  }, [candles, levels]);

  const maxVol = useMemo(
    () => Math.max(1, ...candles.map((c) => c.volume)),
    [candles]
  );

  const candleWidth = mappedCandles.length > 1 ? Math.max(2, (mappedCandles[1].x - mappedCandles[0].x) * 0.65) : 6;

  return (
    <>
      <svg viewBox={`0 0 ${W} ${H_PRICE}`} className="w-full h-[460px]" preserveAspectRatio="none">
        <defs>
          <pattern id="grid" width="50" height="46" patternUnits="userSpaceOnUse">
            <path d="M 50 0 L 0 0 0 46" fill="none" stroke="#141923" strokeWidth="0.5" />
          </pattern>
          <filter id="goldGlow">
            <feGaussianBlur stdDeviation="1.4" />
          </filter>
        </defs>
        <rect width={W} height={H_PRICE} fill="url(#grid)" />

        {/* Levels */}
        {levels.map((l, i) => {
          const y = priceToY(l.price);
          const color = l.kind === "resistance" ? "#d4a857" : "#7dd3fc";
          const stars = "★".repeat(l.strength) + "☆".repeat(Math.max(0, 5 - l.strength));
          const opacity = Math.min(0.95, 0.45 + l.strength * 0.1);
          // Stagger labels so resistance/support stacks don't overlap.
          const labelX = l.kind === "resistance" ? 8 : W - 8;
          const anchor = l.kind === "resistance" ? "start" : "end";
          return (
            <g key={i}>
              <line
                x1={0}
                y1={y}
                x2={W}
                y2={y}
                stroke={color}
                strokeWidth={l.strength >= 4 ? 1.4 : 1}
                strokeDasharray="6 4"
                strokeOpacity={opacity}
              />
              <text
                x={labelX}
                y={y - 4}
                fill={color}
                fontSize="10"
                fontFamily="JetBrains Mono"
                fillOpacity={opacity + 0.05}
                textAnchor={anchor}
              >
                {l.label}  {l.price.toFixed(2)}   {stars}   · 触及{l.touches}
              </text>
            </g>
          );
        })}

        {/* Candles */}
        {mappedCandles.map((m, i) => {
          const color = m.up ? "#ef4444" : "#10b981";
          const top = Math.min(m.oY, m.cY);
          const bot = Math.max(m.oY, m.cY);
          return (
            <g key={i}>
              <line x1={m.x} x2={m.x} y1={m.hY} y2={m.lY} stroke={color} strokeWidth="1" strokeOpacity="0.85" />
              <rect
                x={m.x - candleWidth / 2}
                y={top}
                width={candleWidth}
                height={Math.max(1, bot - top)}
                fill={color}
                fillOpacity={0.95}
              />
            </g>
          );
        })}

        {/* Latest price line */}
        {candles.length > 0 && (
          <g>
            <line
              x1={0}
              x2={W}
              y1={priceToY(last)}
              y2={priceToY(last)}
              stroke="#3a4254"
              strokeDasharray="2 3"
              strokeOpacity="0.6"
            />
            <rect
              x={W - 60}
              y={priceToY(last) - 8}
              width={56}
              height={16}
              fill="#1a2030"
              stroke="#d4a857"
              strokeWidth="0.6"
              rx="2"
            />
            <text
              x={W - 32}
              y={priceToY(last) + 4}
              fill="#e6c98a"
              fontSize="10"
              fontFamily="JetBrains Mono"
              textAnchor="middle"
            >
              {last.toFixed(2)}
            </text>
          </g>
        )}
      </svg>

      {/* Volume pane */}
      <svg viewBox={`0 0 ${W} ${H_VOL}`} className="w-full h-[90px] mt-1" preserveAspectRatio="none">
        <rect width={W} height={H_VOL} fill="url(#grid)" />
        {mappedCandles.map((m, i) => {
          const color = m.up ? "#ef4444" : "#10b981";
          const h = Math.max(1, (m.c.volume / maxVol) * (H_VOL - 12));
          return (
            <rect
              key={i}
              x={m.x - candleWidth / 2}
              y={H_VOL - h}
              width={candleWidth}
              height={h}
              fill={color}
              fillOpacity="0.55"
            />
          );
        })}
        <text x={8} y={14} fill="#6b7388" fontSize="10" fontFamily="JetBrains Mono">
          VOL
        </text>
      </svg>
    </>
  );
}
