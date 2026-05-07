import { useEffect, useRef, useState } from "react";
import type { Candle } from "../types";

type Marker = {
  date: string;
  type: "buy" | "sell";
  price: number;
  label?: string;
};

type Props = {
  candles: Candle[];
  markers: Marker[];
  title: string;
  /** Optional horizontal lines (e.g. stop loss, target) */
  hlines?: { price: number; color: string; label: string; dash?: boolean }[];
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

export function TradeChart({ candles, markers, title, hlines }: Props) {
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

    const markerMap = new Map<string, Marker[]>();
    for (const m of markers) {
      const key = m.date.slice(0, 10);
      if (!markerMap.has(key)) markerMap.set(key, []);
      markerMap.get(key)!.push(m);
    }

    const n = candles.length;
    const chartW = W - PAD_L - PAD_R;
    const mainH = (H - PAD_T - PAD_B) * (1 - VOL_RATIO);
    const volH = (H - PAD_T - PAD_B) * VOL_RATIO;
    const barW = Math.max(1, chartW / n);
    const bodyW = Math.max(1, barW * 0.65);

    let pMin = Infinity, pMax = -Infinity, vMax = 0;
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

    // Background
    ctx.fillStyle = BG;
    ctx.fillRect(0, 0, W, H);

    // Grid
    ctx.strokeStyle = GRID;
    ctx.lineWidth = 0.5;
    const gridN = 5;
    for (let i = 0; i <= gridN; i++) {
      const y = PAD_T + (mainH * i) / gridN;
      ctx.beginPath();
      ctx.moveTo(PAD_L, y);
      ctx.lineTo(W - PAD_R, y);
      ctx.stroke();
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

      ctx.strokeStyle = color;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(x, yP(c.high));
      ctx.lineTo(x, yP(c.low));
      ctx.stroke();

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
      ctx.fillStyle = up ? "rgba(239,68,68,0.2)" : "rgba(16,185,129,0.2)";
      ctx.fillRect(x - bodyW / 2, vy, bodyW, H - PAD_B - vy);
    }

    // Horizontal lines (stop, target, etc.)
    if (hlines) {
      for (const hl of hlines) {
        if (hl.price < pMin || hl.price > pMax) continue;
        const y = yP(hl.price);
        ctx.strokeStyle = hl.color;
        ctx.lineWidth = 1;
        if (hl.dash) ctx.setLineDash([5, 3]);
        ctx.beginPath();
        ctx.moveTo(PAD_L, y);
        ctx.lineTo(W - PAD_R, y);
        ctx.stroke();
        ctx.setLineDash([]);
        // Label
        ctx.fillStyle = hl.color;
        ctx.font = "9px sans-serif";
        ctx.textAlign = "right";
        ctx.fillText(`${hl.label} ${fmtPrice(hl.price)}`, W - PAD_R - 4, y - 3);
      }
    }

    // Trade markers
    const markerR = barW < 4 ? 4 : 6;
    for (let i = 0; i < n; i++) {
      const c = candles[i];
      const ms = markerMap.get(c.date.slice(0, 10));
      if (!ms) continue;
      const x = xC(i);

      for (const m of ms) {
        if (m.type === "buy") {
          const y = yP(c.low) + markerR + 4;
          ctx.fillStyle = BUY_COLOR;
          ctx.beginPath();
          ctx.moveTo(x, y - markerR * 2);
          ctx.lineTo(x - markerR, y);
          ctx.lineTo(x + markerR, y);
          ctx.closePath();
          ctx.fill();
          // Price label
          if (barW > 3) {
            ctx.font = "8px monospace";
            ctx.textAlign = "center";
            ctx.fillText(fmtPrice(m.price), x, y + 10);
          }
        } else {
          const y = yP(c.high) - markerR - 4;
          ctx.fillStyle = SELL_COLOR;
          ctx.beginPath();
          ctx.moveTo(x, y + markerR * 2);
          ctx.lineTo(x - markerR, y);
          ctx.lineTo(x + markerR, y);
          ctx.closePath();
          ctx.fill();
          if (barW > 3) {
            ctx.font = "8px monospace";
            ctx.textAlign = "center";
            ctx.fillText(fmtPrice(m.price), x, y - 4);
          }
        }
      }
    }

    // Connect buy→sell pairs with shaded regions
    const sortedMarkers = markers
      .map((m) => ({ ...m, idx: candles.findIndex((c) => c.date.slice(0, 10) === m.date.slice(0, 10)) }))
      .filter((m) => m.idx >= 0)
      .sort((a, b) => a.idx - b.idx);

    for (let k = 0; k < sortedMarkers.length - 1; k++) {
      const a = sortedMarkers[k];
      const b = sortedMarkers[k + 1];
      if (a.type === "buy" && b.type === "sell") {
        const x1 = xC(a.idx);
        const x2 = xC(b.idx);
        const win = b.price > a.price;
        ctx.fillStyle = win ? "rgba(34,197,94,0.06)" : "rgba(239,68,68,0.06)";
        ctx.fillRect(x1, PAD_T, x2 - x1, mainH);
      }
    }

    // Hover
    const hi = hoverRef.current;
    if (hi !== null && hi >= 0 && hi < n) {
      const c = candles[hi];
      const x = xC(hi);

      ctx.strokeStyle = "#3a4254";
      ctx.lineWidth = 0.5;
      ctx.setLineDash([3, 3]);
      ctx.beginPath();
      ctx.moveTo(x, PAD_T);
      ctx.lineTo(x, H - PAD_B);
      ctx.stroke();
      ctx.setLineDash([]);

      const ms = markerMap.get(c.date.slice(0, 10));
      const boxH = ms ? 84 : 70;
      ctx.fillStyle = "rgba(20,25,35,0.92)";
      ctx.fillRect(PAD_L + 4, PAD_T + 2, 170, boxH);
      ctx.font = "11px monospace";
      ctx.textAlign = "left";
      const up = c.close >= c.open;
      ctx.fillStyle = "#a0a8c0";
      ctx.fillText(c.date.slice(0, 10), PAD_L + 10, PAD_T + 16);
      ctx.fillStyle = up ? UP : DN;
      ctx.fillText(`O ${fmtPrice(c.open)}  C ${fmtPrice(c.close)}`, PAD_L + 10, PAD_T + 32);
      ctx.fillText(`H ${fmtPrice(c.high)}  L ${fmtPrice(c.low)}`, PAD_L + 10, PAD_T + 46);
      ctx.fillStyle = "#a0a8c0";
      const vol = c.volume >= 1e8 ? (c.volume / 1e8).toFixed(1) + "亿" : c.volume >= 1e4 ? (c.volume / 1e4).toFixed(0) + "万" : String(c.volume);
      ctx.fillText(`成交 ${vol}`, PAD_L + 10, PAD_T + 60);
      if (ms) {
        const m = ms[0];
        ctx.fillStyle = m.type === "buy" ? BUY_COLOR : SELL_COLOR;
        ctx.font = "bold 11px sans-serif";
        ctx.fillText(m.type === "buy" ? `▲ 买入 ${fmtPrice(m.price)}` : `▼ 卖出 ${fmtPrice(m.price)}`, PAD_L + 10, PAD_T + 78);
      }
    }

    // X-axis dates
    ctx.fillStyle = TEXT;
    ctx.font = "9px monospace";
    ctx.textAlign = "center";
    const step = Math.max(1, Math.floor(n / 8));
    for (let i = 0; i < n; i += step) {
      ctx.fillText(candles[i].date.slice(2, 7).replace("-", "/"), xC(i), H - PAD_B + 14);
    }

    // Mouse handlers
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
  }, [candles, markers, hlines, hover]);

  const buys = markers.filter((m) => m.type === "buy").length;
  const sells = markers.filter((m) => m.type === "sell").length;

  return (
    <div className="bg-ink-900 rounded-xl border border-ink-800 p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-xs font-semibold text-ink-300">
          <i className="fas fa-chart-line mr-1" /> {title}
        </h3>
        <div className="flex items-center gap-3 text-[11px]">
          {buys > 0 && (
            <span className="flex items-center gap-1">
              <span className="inline-block w-0 h-0 border-l-[4px] border-r-[4px] border-b-[7px] border-l-transparent border-r-transparent border-b-green-500" />
              <span className="text-green-400">买入 {buys}</span>
            </span>
          )}
          {sells > 0 && (
            <span className="flex items-center gap-1">
              <span className="inline-block w-0 h-0 border-l-[4px] border-r-[4px] border-t-[7px] border-l-transparent border-r-transparent border-t-red-500" />
              <span className="text-red-400">卖出 {sells}</span>
            </span>
          )}
          <span className="text-ink-500">{candles.length} K线</span>
        </div>
      </div>
      <div ref={wrapRef} className="w-full" style={{ height: 340 }}>
        <canvas ref={cvRef} className="block w-full h-full cursor-crosshair" />
      </div>
    </div>
  );
}
