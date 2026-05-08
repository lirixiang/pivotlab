import { useCallback, useEffect, useRef, useState } from "react";
import type { Candle, Level, AnalystConsensus } from "../types";

type Props = {
  candles: Candle[];
  levels: Level[];
  consensus?: AnalystConsensus | null;
};

/* ── constants ── */
const UP = "#ef4444";
const DN = "#10b981";
const GRID = "#141923";
const BG = "#0b0f19";
const TEXT = "#6b7388";
const CROSS = "#3a4254";
const GOLD = "#d4a857";
const SKY = "#7dd3fc";
const PRICE_LABEL_W = 72;
const VOL_RATIO = 0.18;      // volume pane height ratio
const PAD_T = 18;
const PAD_B = 28;
const PAD_L = 16;
const MIN_VISIBLE = 20;
const MAX_VISIBLE = 300;
const DEFAULT_VISIBLE = 90;

function clamp(v: number, lo: number, hi: number) { return Math.max(lo, Math.min(hi, v)); }
function fmtPrice(v: number, range: number) {
  const d = range < 2 ? 3 : range < 20 ? 2 : 1;
  return v.toFixed(d);
}
function fmtVol(v: number) {
  if (v >= 1e8) return `${(v / 1e8).toFixed(1)}亿`;
  if (v >= 1e4) return `${(v / 1e4).toFixed(0)}万`;
  return v.toFixed(0);
}
function fmtDate(s: string) { return s.length >= 10 ? s.slice(5) : s; }

export function ChartCanvas({ candles, levels, consensus }: Props) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const cvRef = useRef<HTMLCanvasElement>(null);
  const rafRef = useRef(0);

  // viewport: [start, end) indices into candles
  const vpRef = useRef<{ start: number; end: number } | null>(null);
  // interaction state
  const dragRef = useRef<{ sx: number; vpStart: number; vpEnd: number; moved: boolean } | null>(null);
  const [hover, setHover] = useState<{ x: number; y: number } | null>(null);
  const hoverRef = useRef(hover);
  hoverRef.current = hover;

  /* ── ensure viewport ── */
  const ensureVp = useCallback(() => {
    const n = candles.length;
    if (n === 0) { vpRef.current = { start: 0, end: 0 }; return; }
    if (!vpRef.current) {
      const cnt = Math.min(n, DEFAULT_VISIBLE);
      vpRef.current = { start: n - cnt, end: n };
      return;
    }
    // keep existing viewport but clamp
    const vp = vpRef.current;
    const cnt = clamp(vp.end - vp.start, MIN_VISIBLE, MAX_VISIBLE);
    let end = clamp(vp.end, cnt, n);
    let start = end - cnt;
    if (start < 0) { start = 0; end = Math.min(n, cnt); }
    vpRef.current = { start, end };
  }, [candles.length]);

  /* ── draw ── */
  const draw = useCallback(() => {
    const cv = cvRef.current;
    const wrap = wrapRef.current;
    if (!cv || !wrap) return;
    const dpr = window.devicePixelRatio || 1;
    const W = wrap.clientWidth;
    const H = wrap.clientHeight;
    if (W < 10 || H < 10) return;
    if (cv.width !== W * dpr || cv.height !== H * dpr) {
      cv.width = W * dpr;
      cv.height = H * dpr;
      cv.style.width = `${W}px`;
      cv.style.height = `${H}px`;
    }
    const ctx = cv.getContext("2d")!;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);

    ensureVp();
    const vp = vpRef.current!;
    const slice = candles.slice(vp.start, vp.end);
    const n = slice.length;
    if (n === 0) { ctx.fillStyle = TEXT; ctx.font = "13px sans-serif"; ctx.fillText("暂无数据", W / 2 - 28, H / 2); return; }

    const padR = PRICE_LABEL_W + 8;
    const plotW = W - PAD_L - padR;
    const volH = Math.max(40, Math.min(96, H * VOL_RATIO));
    const gap = 12;
    const priceH = H - PAD_T - PAD_B - volH - gap;
    const volTop = PAD_T + priceH + gap;

    const step = plotW / n;
    const cw = Math.max(1, step * 0.65);
    const xOf = (i: number) => PAD_L + i * step + step / 2;

    // price range
    let pMin = Infinity, pMax = -Infinity;
    for (const c of slice) { if (c.low < pMin) pMin = c.low; if (c.high > pMax) pMax = c.high; }
    for (const l of levels) { if (l.price < pMin) pMin = l.price; if (l.price > pMax) pMax = l.price; }
    if (consensus?.consensus_target) {
      const ct = consensus.consensus_target;
      if (ct > pMax) pMax = ct;
      if (ct < pMin) pMin = ct;
    }
    const pPad = (pMax - pMin) * 0.04 || 1;
    pMin -= pPad; pMax += pPad;
    const pRange = pMax - pMin || 1;
    const priceY = (p: number) => PAD_T + ((pMax - p) / pRange) * priceH;

    // vol range
    let vMax = 0;
    for (const c of slice) { if (c.volume > vMax) vMax = c.volume; }
    vMax = vMax || 1;

    /* ── grid ── */
    ctx.strokeStyle = GRID;
    ctx.lineWidth = 0.5;
    // horizontal grid (price area)
    const gridRows = 5;
    for (let i = 0; i <= gridRows; i++) {
      const y = PAD_T + (priceH / gridRows) * i;
      ctx.beginPath(); ctx.moveTo(PAD_L, y); ctx.lineTo(W - padR, y); ctx.stroke();
    }
    // price axis labels
    ctx.fillStyle = TEXT;
    ctx.font = "10px 'JetBrains Mono', monospace";
    ctx.textAlign = "left";
    for (let i = 0; i <= gridRows; i++) {
      const y = PAD_T + (priceH / gridRows) * i;
      const p = pMax - (pRange / gridRows) * i;
      ctx.fillText(fmtPrice(p, pRange), W - padR + 6, y + 3);
    }
    // date axis (every ~N candles)
    ctx.textAlign = "center";
    const dateStep = Math.max(1, Math.floor(n / 6));
    for (let i = 0; i < n; i += dateStep) {
      const x = xOf(i);
      ctx.fillText(fmtDate(slice[i].date), x, H - 6);
      ctx.beginPath(); ctx.moveTo(x, PAD_T); ctx.lineTo(x, PAD_T + priceH); ctx.stroke();
    }

    /* ── levels (strength-based visuals) ── */
    for (const l of levels) {
      const y = priceY(l.price);
      if (y < PAD_T - 10 || y > PAD_T + priceH + 10) continue;
      const color = l.kind === "resistance" ? GOLD : SKY;
      const score = l.score ?? (l.strength * 20);

      // Strength-based visual parameters
      const lineW = score >= 70 ? 2.2 : score >= 50 ? 1.6 : score >= 30 ? 1.0 : 0.7;
      const alpha = Math.min(0.95, 0.3 + score / 120);
      const dashPattern: number[] = score >= 60 ? [10, 4] : score >= 40 ? [6, 4] : [4, 5];

      ctx.save();

      // Glow effect for strong levels (score >= 60)
      if (score >= 60) {
        ctx.globalAlpha = 0.12 + (score - 60) * 0.003;
        ctx.strokeStyle = color;
        ctx.lineWidth = lineW + 4;
        ctx.setLineDash([]);
        ctx.beginPath(); ctx.moveTo(PAD_L, y); ctx.lineTo(W - padR, y); ctx.stroke();

        // Subtle zone band
        ctx.fillStyle = color;
        ctx.globalAlpha = 0.04 + (score - 60) * 0.001;
        const bandH = score >= 80 ? 8 : 5;
        ctx.fillRect(PAD_L, y - bandH / 2, W - PAD_L - padR, bandH);
      }

      // Main dashed line
      ctx.globalAlpha = alpha;
      ctx.strokeStyle = color;
      ctx.lineWidth = lineW;
      ctx.setLineDash(dashPattern);
      ctx.beginPath(); ctx.moveTo(PAD_L, y); ctx.lineTo(W - padR, y); ctx.stroke();
      ctx.setLineDash([]);

      // Label with score and stars
      ctx.font = "10px 'JetBrains Mono', monospace";
      ctx.fillStyle = color;
      const stars = "★".repeat(l.strength) + "☆".repeat(Math.max(0, 5 - l.strength));
      const scoreTxt = score > 0 ? ` ${score.toFixed(0)}分` : "";
      const noteTxt = l.note ? ` · ${l.note}` : "";
      const txt = `${l.label}  ${l.price.toFixed(2)}  ${stars}${scoreTxt}${noteTxt}`;
      if (l.kind === "resistance") {
        ctx.textAlign = "left";
        ctx.fillText(txt, PAD_L + 4, y - 5);
      } else {
        ctx.textAlign = "right";
        ctx.fillText(txt, W - padR - 4, y - 5);
      }
      ctx.restore();
    }

    /* ── consensus target price line (single) ── */
    if (consensus?.consensus_target) {
      const PURPLE = "#c084fc";
      const price = consensus.consensus_target;
      const y = priceY(price);
      if (y >= PAD_T - 10 && y <= PAD_T + priceH + 10) {
        ctx.save();
        // Glow
        ctx.globalAlpha = 0.10;
        ctx.strokeStyle = PURPLE;
        ctx.lineWidth = 5;
        ctx.setLineDash([]);
        ctx.beginPath(); ctx.moveTo(PAD_L, y); ctx.lineTo(W - padR, y); ctx.stroke();
        // Dashed line
        ctx.globalAlpha = 0.85;
        ctx.strokeStyle = PURPLE;
        ctx.lineWidth = 1.8;
        ctx.setLineDash([8, 4]);
        ctx.beginPath(); ctx.moveTo(PAD_L, y); ctx.lineTo(W - padR, y); ctx.stroke();
        ctx.setLineDash([]);
        // Label
        ctx.font = "10px 'JetBrains Mono', monospace";
        ctx.fillStyle = PURPLE;
        ctx.globalAlpha = 0.9;
        ctx.textAlign = "right";
        ctx.fillText(`◎ 一致目标价  ${price.toFixed(2)}`, W - padR - 4, y - 5);
        ctx.restore();
      }
    }

    /* ── candles ── */
    for (let i = 0; i < n; i++) {
      const c = slice[i];
      const x = xOf(i);
      const up = c.close >= c.open;
      const color = up ? UP : DN;
      // wick
      ctx.strokeStyle = color;
      ctx.lineWidth = 1;
      ctx.globalAlpha = 0.85;
      ctx.beginPath(); ctx.moveTo(x, priceY(c.high)); ctx.lineTo(x, priceY(c.low)); ctx.stroke();
      // body
      ctx.globalAlpha = 0.95;
      ctx.fillStyle = color;
      const oY = priceY(c.open), cY = priceY(c.close);
      const top = Math.min(oY, cY), h = Math.max(1, Math.abs(oY - cY));
      ctx.fillRect(x - cw / 2, top, cw, h);
      ctx.globalAlpha = 1;
    }

    /* ── latest price line ── */
    const last = slice[n - 1].close;
    const lastY = priceY(last);
    ctx.strokeStyle = CROSS;
    ctx.lineWidth = 1;
    ctx.setLineDash([2, 3]);
    ctx.globalAlpha = 0.6;
    ctx.beginPath(); ctx.moveTo(PAD_L, lastY); ctx.lineTo(W - padR, lastY); ctx.stroke();
    ctx.setLineDash([]);
    ctx.globalAlpha = 1;
    // price badge
    ctx.fillStyle = "#1a2030";
    ctx.strokeStyle = GOLD;
    ctx.lineWidth = 0.6;
    const bx = W - padR + 2, bw = PRICE_LABEL_W - 4, bh = 16;
    ctx.beginPath();
    ctx.roundRect(bx, lastY - bh / 2, bw, bh, 2);
    ctx.fill(); ctx.stroke();
    ctx.fillStyle = "#e6c98a";
    ctx.font = "10px 'JetBrains Mono', monospace";
    ctx.textAlign = "center";
    ctx.fillText(last.toFixed(2), bx + bw / 2, lastY + 4);

    /* ── volume bars ── */
    // vol grid line
    ctx.strokeStyle = GRID; ctx.lineWidth = 0.5;
    ctx.beginPath(); ctx.moveTo(PAD_L, volTop); ctx.lineTo(W - padR, volTop); ctx.stroke();
    ctx.fillStyle = TEXT; ctx.font = "10px 'JetBrains Mono', monospace";
    ctx.textAlign = "left"; ctx.fillText("VOL", PAD_L + 4, volTop + 12);
    for (let i = 0; i < n; i++) {
      const c = slice[i];
      const x = xOf(i);
      const up = c.close >= c.open;
      const h = Math.max(1, (c.volume / vMax) * (volH - 12));
      ctx.fillStyle = up ? UP : DN;
      ctx.globalAlpha = 0.55;
      ctx.fillRect(x - cw / 2, volTop + volH - h, cw, h);
    }
    ctx.globalAlpha = 1;

    /* ── crosshair + tooltip ── */
    const mouse = hoverRef.current;
    if (mouse && mouse.x >= PAD_L && mouse.x <= W - padR && mouse.y >= PAD_T && mouse.y <= volTop + volH) {
      // find nearest candle
      const ci = clamp(Math.round((mouse.x - PAD_L - step / 2) / step), 0, n - 1);
      const cx = xOf(ci);
      const c = slice[ci];
      // vertical line
      ctx.strokeStyle = "#4a5568";
      ctx.lineWidth = 0.5;
      ctx.setLineDash([3, 3]);
      ctx.beginPath(); ctx.moveTo(cx, PAD_T); ctx.lineTo(cx, volTop + volH); ctx.stroke();
      // horizontal line
      ctx.beginPath(); ctx.moveTo(PAD_L, mouse.y); ctx.lineTo(W - padR, mouse.y); ctx.stroke();
      ctx.setLineDash([]);
      // price at cursor
      if (mouse.y >= PAD_T && mouse.y <= PAD_T + priceH) {
        const hp = pMax - ((mouse.y - PAD_T) / priceH) * pRange;
        ctx.fillStyle = "#1e293b";
        ctx.beginPath();
        ctx.roundRect(W - padR + 2, mouse.y - 8, PRICE_LABEL_W - 4, 16, 2);
        ctx.fill();
        ctx.fillStyle = "#cbd5e1";
        ctx.font = "10px 'JetBrains Mono', monospace";
        ctx.textAlign = "center";
        ctx.fillText(fmtPrice(hp, pRange), W - padR + 2 + (PRICE_LABEL_W - 4) / 2, mouse.y + 4);
      }
      // date at cursor
      ctx.fillStyle = "#1e293b";
      ctx.beginPath();
      ctx.roundRect(cx - 32, H - PAD_B + 2, 64, 16, 2);
      ctx.fill();
      ctx.fillStyle = "#cbd5e1";
      ctx.textAlign = "center";
      ctx.fillText(fmtDate(c.date), cx, H - PAD_B + 14);
      // OHLC tooltip top-left
      const up = c.close >= c.open;
      ctx.font = "11px 'JetBrains Mono', monospace";
      ctx.textAlign = "left";
      const items = [
        { l: "开", v: c.open.toFixed(2) },
        { l: "高", v: c.high.toFixed(2) },
        { l: "低", v: c.low.toFixed(2) },
        { l: "收", v: c.close.toFixed(2) },
        { l: "量", v: fmtVol(c.volume) },
      ];
      let tx = PAD_L + 8;
      const ty = PAD_T + 12;
      for (const it of items) {
        ctx.fillStyle = TEXT;
        ctx.fillText(it.l, tx, ty);
        tx += 14;
        ctx.fillStyle = up ? UP : DN;
        ctx.fillText(it.v, tx, ty);
        tx += ctx.measureText(it.v).width + 12;
      }
    }
  }, [candles, levels, consensus, ensureVp]);

  /* ── schedule draw ── */
  const scheduleDraw = useCallback(() => {
    cancelAnimationFrame(rafRef.current);
    rafRef.current = requestAnimationFrame(draw);
  }, [draw]);

  /* ── resize observer ── */
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => scheduleDraw());
    ro.observe(el);
    scheduleDraw();
    return () => { ro.disconnect(); cancelAnimationFrame(rafRef.current); };
  }, [scheduleDraw]);

  /* ── redraw on data change ── */
  useEffect(() => {
    // reset viewport when candle data changes significantly
    const n = candles.length;
    const vp = vpRef.current;
    if (!vp || Math.abs((vp.end - vp.start) - 0) === 0) {
      vpRef.current = null; // will re-init in ensureVp
    }
    scheduleDraw();
  }, [candles, levels, consensus, scheduleDraw]);

  /* ── mouse handlers ── */
  const getPos = (e: React.MouseEvent) => {
    const r = cvRef.current!.getBoundingClientRect();
    return { x: e.clientX - r.left, y: e.clientY - r.top };
  };

  const onMouseDown = (e: React.MouseEvent) => {
    if (e.button !== 0) return;
    const { x } = getPos(e);
    const vp = vpRef.current;
    if (!vp) return;
    dragRef.current = { sx: x, vpStart: vp.start, vpEnd: vp.end, moved: false };
    (e.target as HTMLElement).setPointerCapture((e.nativeEvent as PointerEvent).pointerId);
  };

  const onMouseMove = (e: React.MouseEvent) => {
    const pos = getPos(e);
    const drag = dragRef.current;
    if (drag) {
      const dx = pos.x - drag.sx;
      if (Math.abs(dx) > 2) drag.moved = true;
      if (!drag.moved) return;
      const wrap = wrapRef.current!;
      const padR = PRICE_LABEL_W + 8;
      const plotW = wrap.clientWidth - PAD_L - padR;
      const cnt = drag.vpEnd - drag.vpStart;
      const step = plotW / cnt;
      const shift = Math.round(-dx / step);
      const n = candles.length;
      let start = clamp(drag.vpStart + shift, 0, n - cnt);
      let end = start + cnt;
      if (end > n) { end = n; start = n - cnt; }
      vpRef.current = { start, end };
      scheduleDraw();
    } else {
      setHover(pos);
      scheduleDraw();
    }
  };

  const onMouseUp = () => { dragRef.current = null; };
  const onMouseLeave = () => { dragRef.current = null; setHover(null); scheduleDraw(); };

  const onWheel = (e: React.WheelEvent) => {
    e.preventDefault();
    const vp = vpRef.current;
    if (!vp) return;
    const n = candles.length;
    const cnt = vp.end - vp.start;
    const delta = e.deltaY > 0 ? Math.max(1, Math.round(cnt * 0.1)) : -Math.max(1, Math.round(cnt * 0.1));
    let newCnt = clamp(cnt + delta, MIN_VISIBLE, Math.min(n, MAX_VISIBLE));
    // zoom centered on cursor
    const pos = getPos(e as any);
    const wrap = wrapRef.current!;
    const padR = PRICE_LABEL_W + 8;
    const plotW = wrap.clientWidth - PAD_L - padR;
    const ratio = clamp((pos.x - PAD_L) / plotW, 0, 1);
    const grow = newCnt - cnt;
    let start = Math.round(vp.start - grow * ratio);
    let end = start + newCnt;
    if (start < 0) { start = 0; end = newCnt; }
    if (end > n) { end = n; start = Math.max(0, n - newCnt); }
    vpRef.current = { start, end };
    scheduleDraw();
  };

  return (
    <div ref={wrapRef} className="w-full" style={{ height: "560px" }}>
      <canvas
        ref={cvRef}
        className="block cursor-crosshair"
        onPointerDown={onMouseDown}
        onPointerMove={onMouseMove}
        onPointerUp={onMouseUp}
        onPointerLeave={onMouseLeave}
        onWheel={onWheel}
        style={{ touchAction: "none" }}
      />
    </div>
  );
}
