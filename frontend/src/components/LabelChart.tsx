import { useEffect, useRef, useState } from "react";
import type { Candle, LabeledPoint } from "../types";

type Props = {
  candles: Candle[];
  labels: LabeledPoint[];
  code: string;
};

const UP = "#ef4444";
const DN = "#10b981";
const BG = "#0b0f19";
const GRID = "#141923";
const TEXT = "#6b7388";
const BUY_COLOR = "#22c55e";
const SELL_COLOR = "#ef4444";
const PAD_T = 24;
const PAD_B = 28;
const PAD_R = 64;
const PAD_L = 12;
const VOL_RATIO = 0.15;

function fmtPrice(v: number) {
  return v >= 100 ? v.toFixed(1) : v.toFixed(2);
}

export function LabelChart({ candles, labels, code }: Props) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const cvRef = useRef<HTMLCanvasElement>(null);
  const [hover, setHover] = useState<number | null>(null);
  const hoverRef = useRef(hover);
  hoverRef.current = hover;

  useEffect(() => {
    const wrap = wrapRef.current;
    const cv = cvRef.current;
    if (!wrap || !cv || !candles.length) return;

    const dpr = window.devicePixelRatio || 1;
    const W = wrap.clientWidth;
    const H = wrap.clientHeight;
    cv.width = W * dpr;
    cv.height = H * dpr;
    cv.style.width = W + "px";
    cv.style.height = H + "px";
    const ctx = cv.getContext("2d")!;
    ctx.scale(dpr, dpr);

    // Build label index: date -> label
    const labelMap = new Map<string, "buy" | "sell">();
    for (const p of labels) {
      labelMap.set(p.date.slice(0, 10), p.label);
    }

    const n = candles.length;
    const chartW = W - PAD_L - PAD_R;
    const mainH = (H - PAD_T - PAD_B) * (1 - VOL_RATIO);
    const volH = (H - PAD_T - PAD_B) * VOL_RATIO;
    const barW = Math.max(1, chartW / n);
    const bodyW = Math.max(1, barW * 0.65);

    // Price range
    let pMin = Infinity, pMax = -Infinity;
    let vMax = 0;
    for (const c of candles) {
      if (c.low < pMin) pMin = c.low;
      if (c.high > pMax) pMax = c.high;
      if (c.volume > vMax) vMax = c.volume;
    }
    const pPad = (pMax - pMin) * 0.06;
    pMin -= pPad;
    pMax += pPad;

    const yP = (p: number) => PAD_T + mainH * (1 - (p - pMin) / (pMax - pMin));
    const yV = (v: number) => H - PAD_B - (v / vMax) * volH;
    const xC = (i: number) => PAD_L + i * barW + barW / 2;

    // Draw
    ctx.fillStyle = BG;
    ctx.fillRect(0, 0, W, H);

    // Grid lines
    ctx.strokeStyle = GRID;
    ctx.lineWidth = 0.5;
    const gridN = 5;
    for (let i = 0; i <= gridN; i++) {
      const y = PAD_T + (mainH * i) / gridN;
      ctx.beginPath();
      ctx.moveTo(PAD_L, y);
      ctx.lineTo(W - PAD_R, y);
      ctx.stroke();
      // Price label
      const price = pMax - ((pMax - pMin) * i) / gridN;
      ctx.fillStyle = TEXT;
      ctx.font = "10px monospace";
      ctx.textAlign = "left";
      ctx.fillText(fmtPrice(price), W - PAD_R + 6, y + 3);
    }

    // Candles
    for (let i = 0; i < n; i++) {
      const c = candles[i];
      const x = xC(i);
      const up = c.close >= c.open;
      const color = up ? UP : DN;

      // Wick
      ctx.strokeStyle = color;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(x, yP(c.high));
      ctx.lineTo(x, yP(c.low));
      ctx.stroke();

      // Body
      const yTop = yP(Math.max(c.open, c.close));
      const yBot = yP(Math.min(c.open, c.close));
      const bh = Math.max(1, yBot - yTop);
      if (up) {
        ctx.strokeStyle = color;
        ctx.lineWidth = 1;
        ctx.strokeRect(x - bodyW / 2, yTop, bodyW, bh);
      } else {
        ctx.fillStyle = color;
        ctx.fillRect(x - bodyW / 2, yTop, bodyW, bh);
      }

      // Volume
      const vy = yV(c.volume);
      ctx.fillStyle = up ? "rgba(239,68,68,0.25)" : "rgba(16,185,129,0.25)";
      ctx.fillRect(x - bodyW / 2, vy, bodyW, H - PAD_B - vy);
    }

    // --- Draw label markers ---
    const markerR = barW < 4 ? 3 : 5;
    for (let i = 0; i < n; i++) {
      const c = candles[i];
      const lbl = labelMap.get(c.date.slice(0, 10));
      if (!lbl) continue;
      const x = xC(i);

      if (lbl === "buy") {
        // Green triangle up below the low
        const y = yP(c.low) + markerR + 4;
        ctx.fillStyle = BUY_COLOR;
        ctx.beginPath();
        ctx.moveTo(x, y - markerR * 2);
        ctx.lineTo(x - markerR, y);
        ctx.lineTo(x + markerR, y);
        ctx.closePath();
        ctx.fill();
      } else {
        // Red triangle down above the high
        const y = yP(c.high) - markerR - 4;
        ctx.fillStyle = SELL_COLOR;
        ctx.beginPath();
        ctx.moveTo(x, y + markerR * 2);
        ctx.lineTo(x - markerR, y);
        ctx.lineTo(x + markerR, y);
        ctx.closePath();
        ctx.fill();
      }
    }

    // --- Draw lines connecting consecutive labels (zigzag) ---
    const labelIndices: { i: number; label: string; price: number }[] = [];
    for (let i = 0; i < n; i++) {
      const lbl = labelMap.get(candles[i].date.slice(0, 10));
      if (lbl) {
        labelIndices.push({ i, label: lbl, price: lbl === "buy" ? candles[i].low : candles[i].high });
      }
    }
    if (labelIndices.length > 1) {
      ctx.strokeStyle = "rgba(212,168,87,0.35)";
      ctx.lineWidth = 1;
      ctx.setLineDash([4, 3]);
      ctx.beginPath();
      ctx.moveTo(xC(labelIndices[0].i), yP(labelIndices[0].price));
      for (let k = 1; k < labelIndices.length; k++) {
        ctx.lineTo(xC(labelIndices[k].i), yP(labelIndices[k].price));
      }
      ctx.stroke();
      ctx.setLineDash([]);
    }

    // --- Hover crosshair ---
    const hi = hoverRef.current;
    if (hi !== null && hi >= 0 && hi < n) {
      const c = candles[hi];
      const x = xC(hi);

      // Vertical line
      ctx.strokeStyle = "#3a4254";
      ctx.lineWidth = 0.5;
      ctx.setLineDash([3, 3]);
      ctx.beginPath();
      ctx.moveTo(x, PAD_T);
      ctx.lineTo(x, H - PAD_B);
      ctx.stroke();
      ctx.setLineDash([]);

      // Info box
      const lbl = labelMap.get(c.date.slice(0, 10));
      const up = c.close >= c.open;
      ctx.fillStyle = "rgba(20,25,35,0.92)";
      ctx.fillRect(PAD_L + 4, PAD_T + 2, 170, lbl ? 84 : 70);
      ctx.font = "11px monospace";
      ctx.textAlign = "left";
      ctx.fillStyle = "#a0a8c0";
      ctx.fillText(c.date.slice(0, 10), PAD_L + 10, PAD_T + 16);
      ctx.fillStyle = up ? UP : DN;
      ctx.fillText(`O ${fmtPrice(c.open)}  C ${fmtPrice(c.close)}`, PAD_L + 10, PAD_T + 32);
      ctx.fillText(`H ${fmtPrice(c.high)}  L ${fmtPrice(c.low)}`, PAD_L + 10, PAD_T + 46);
      ctx.fillStyle = "#a0a8c0";
      const vol = c.volume >= 1e8 ? (c.volume / 1e8).toFixed(1) + "亿" : c.volume >= 1e4 ? (c.volume / 1e4).toFixed(0) + "万" : String(c.volume);
      ctx.fillText(`成交 ${vol}`, PAD_L + 10, PAD_T + 60);
      if (lbl) {
        ctx.fillStyle = lbl === "buy" ? BUY_COLOR : SELL_COLOR;
        ctx.font = "bold 11px sans-serif";
        ctx.fillText(lbl === "buy" ? "▲ 买入信号" : "▼ 卖出信号", PAD_L + 10, PAD_T + 78);
      }
    }

    // X-axis date labels
    ctx.fillStyle = TEXT;
    ctx.font = "9px monospace";
    ctx.textAlign = "center";
    const step = Math.max(1, Math.floor(n / 8));
    for (let i = 0; i < n; i += step) {
      const d = candles[i].date;
      ctx.fillText(d.slice(2, 7).replace("-", "/"), xC(i), H - PAD_B + 14);
    }

    // --- Mouse handlers ---
    const handleMove = (e: MouseEvent) => {
      const rect = cv.getBoundingClientRect();
      const mx = e.clientX - rect.left;
      const idx = Math.floor((mx - PAD_L) / barW);
      setHover(idx >= 0 && idx < n ? idx : null);
    };
    const handleLeave = () => setHover(null);

    cv.addEventListener("mousemove", handleMove);
    cv.addEventListener("mouseleave", handleLeave);
    return () => {
      cv.removeEventListener("mousemove", handleMove);
      cv.removeEventListener("mouseleave", handleLeave);
    };
  }, [candles, labels, hover]);

  const buyCount = labels.filter((l) => l.label === "buy").length;
  const sellCount = labels.filter((l) => l.label === "sell").length;

  return (
    <div className="bg-ink-900 rounded-xl border border-ink-800 p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-xs font-semibold text-ink-300">
          <i className="fas fa-chart-line mr-1" /> {code} 标注可视化
        </h3>
        <div className="flex items-center gap-3 text-[11px]">
          <span className="flex items-center gap-1">
            <span className="inline-block w-0 h-0 border-l-[4px] border-r-[4px] border-b-[7px] border-l-transparent border-r-transparent border-b-green-500" />
            <span className="text-green-400">买入 {buyCount}</span>
          </span>
          <span className="flex items-center gap-1">
            <span className="inline-block w-0 h-0 border-l-[4px] border-r-[4px] border-t-[7px] border-l-transparent border-r-transparent border-t-red-500" />
            <span className="text-red-400">卖出 {sellCount}</span>
          </span>
          <span className="text-ink-500">{candles.length} 根K线</span>
        </div>
      </div>
      <div ref={wrapRef} className="w-full" style={{ height: 360 }}>
        <canvas ref={cvRef} className="block w-full h-full cursor-crosshair" />
      </div>
    </div>
  );
}
