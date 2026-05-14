/**
 * 涨跌停 / 炸板 / 一字板 / T字板 / 连板 等事件判定（纯函数, 仅依赖 OHLC）
 *
 * A 股涨跌停规则：
 *   主板（沪 60 / 深 00）        ±10%
 *   创业板（30）/ 科创板（68）    ±20%
 *   北交所（8 / 92）              ±30%
 *   ST 个股                        ±5%
 *
 * 涨跌停价 = round(prev_close * (1 ± pct), 2)
 *
 * 事件：
 *   zt        涨停（收盘 ≥ 涨停价 - 0.005）
 *   dt        跌停（收盘 ≤ 跌停价 + 0.005）
 *   yi_zi     一字板（涨停 且 当日最低 = 涨停价）
 *   t_zi      T字板  （涨停 且 盘中最低 < 昨收 -1%）
 *   zb        炸板  （盘中触及涨停 但 收盘未封板）
 *   consec    N 连板（涨停 + 之前连续涨停的天数, 含今日 ≥ 2）
 *   high_vol  放巨量（成交量 ≥ 20 日均量 × 3）
 */

export type CandleEvent = {
  /** 主标签 */
  tag: "zt" | "dt" | "zb" | "yi_zi" | "t_zi" | null;
  /** N 连板次数（含今日）, 1 表示首板 */
  consecutive: number;
  /** 今日是否放巨量 */
  highVol: boolean;
  /** 涨停价 */
  limitUp: number;
  /** 跌停价 */
  limitDown: number;
  /** 涨跌幅 % */
  changePct: number;
  /** 5 日量能均线 */
  volMa5: number;
  /** 20 日量能均线 */
  volMa20: number;
  /** 量比 = 今日量 / 5日均量 */
  volRatio: number;
  /**
   * 量价信号（独立于涨停 tag, 可同时存在）：
   *   ju_yang   巨量长阳：vRatio20 ≥ 3 + 中阳/大阳, 主力进场
   *   ju_yin    巨量长阴：vRatio20 ≥ 3 + 中阴/大阴, 主力出货
   *   tian_liang 天量天价：60 日量+价同创新高, 见顶警告
   *   di_liang   地量地价：60 日量创新低, 抛压衰竭
   *   zt_suo    涨停缩量：连板途中量 < MA5, 锁仓强势
   *   zt_fenqi  涨停巨量分歧：3 连板后再放巨量, 警惕
   *   suo_xipan 缩量洗盘：上升趋势中 vol < MA5 × 0.7
   */
  volSignal:
    | "ju_yang"
    | "ju_yin"
    | "tian_liang"
    | "di_liang"
    | "zt_suo"
    | "zt_fenqi"
    | "suo_xipan"
    | null;
  /** 量堆：连续 3+ 天 vol_ratio > 1.5（建仓 / 蓄势特征） */
  volStack: boolean;
  /** 量价背离：top=价新高量未新高（顶背离 ↘）, bottom=价新低量未新低（底背离 ↗） */
  divergence: "top" | "bottom" | null;
  /** 价格均线 */
  priceMa5: number;
  priceMa10: number;
  priceMa20: number;
  priceMa60: number;
};

type OHLC = {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};

const EPS = 0.005; // 容差：tick 价精度 + 浮点误差

/**
 * 根据股票代码 + 名称返回涨跌停幅度（小数, 例 0.10 = 10%）
 */
export function limitPctFor(code: string | undefined, name?: string | undefined): number {
  if (name && /\bST\b|^ST|\sST/i.test(name)) return 0.05;
  if (!code) return 0.10;
  const c = code.trim();
  if (c.startsWith("30")) return 0.20;          // 创业板
  if (c.startsWith("68")) return 0.20;          // 科创板
  if (c.startsWith("8") || c.startsWith("92")) return 0.30; // 北交所
  if (c.startsWith("4")) return 0.30;           // 老三板/北交（少数）
  return 0.10;                                   // 主板
}

/**
 * A 股涨跌停价采用「四舍五入到分」, 与交易所一致
 */
function roundTick(price: number): number {
  return Math.round(price * 100) / 100;
}

/**
 * 计算单根 K 线的事件标签
 *
 * @param prev    昨日 K（首根传 null）
 * @param history 之前若干根（含 prev, 用于推算连板数和量能均线, 可只传最近 25 根）
 */
function detectOne(
  cur: OHLC,
  prev: OHLC | null,
  history: OHLC[],
  pct: number,
): CandleEvent {
  const prevClose = prev ? prev.close : cur.open;
  const limitUp = roundTick(prevClose * (1 + pct));
  const limitDown = roundTick(prevClose * (1 - pct));
  const changePct = prevClose > 0 ? ((cur.close - prevClose) / prevClose) * 100 : 0;

  // ── 量能均线 ──
  const tail20 = history.slice(-20);
  const tail5 = history.slice(-5);
  const volMa20 = tail20.length ? tail20.reduce((s, h) => s + (h.volume || 0), 0) / tail20.length : 0;
  const volMa5 = tail5.length ? tail5.reduce((s, h) => s + (h.volume || 0), 0) / tail5.length : 0;

  // ── 价格均线（含当日 close）──
  const allClose = [...history.map(h => h.close), cur.close];
  const maCalc = (n: number) => {
    if (allClose.length < n) return 0;
    const w = allClose.slice(-n);
    return w.reduce((s, v) => s + v, 0) / n;
  };
  const priceMa5 = maCalc(5);
  const priceMa10 = maCalc(10);
  const priceMa20 = maCalc(20);
  const priceMa60 = maCalc(60);
  const volRatio = volMa5 > 0 ? cur.volume / volMa5 : 0;
  const highVol = volMa20 > 0 && cur.volume >= volMa20 * 3;

  // ── 涨跌停 / 炸板 ──
  let tag: CandleEvent["tag"] = null;
  const isZt = cur.close >= limitUp - EPS;
  const isDt = cur.close <= limitDown + EPS;
  const touchedUp = cur.high >= limitUp - EPS;
  if (isZt) {
    const isYiZi = cur.low >= limitUp - EPS;
    const isTZi = !isYiZi && cur.low <= prevClose * 0.99;
    tag = isYiZi ? "yi_zi" : (isTZi ? "t_zi" : "zt");
  } else if (isDt) {
    tag = "dt";
  } else if (touchedUp) {
    tag = "zb";
  }

  // ── N 连板 ──
  let consec = 0;
  if (isZt) {
    consec = 1;
    for (let j = history.length - 1; j >= 0; j--) {
      const h = history[j];
      const hPrev = j > 0 ? history[j - 1].close : h.open;
      const hLimit = roundTick(hPrev * (1 + pct));
      if (h.close >= hLimit - EPS) consec += 1;
      else break;
    }
  }

  // ── 量价信号（按优先级判一个）──
  let volSignal: CandleEvent["volSignal"] = null;
  const bodyPct = prevClose > 0 ? ((cur.close - cur.open) / prevClose) * 100 : 0;
  const isUpBar = cur.close > cur.open;
  const isDnBar = cur.close < cur.open;

  // 60 日窗口（用于天量/地量/趋势）
  const tail60 = history.slice(-60);
  const vol60Min = tail60.length >= 30
    ? tail60.reduce((m, h) => Math.min(m, h.volume || Infinity), Infinity)
    : Infinity;
  const vol60Max = tail60.length >= 30
    ? tail60.reduce((m, h) => Math.max(m, h.volume || 0), 0)
    : 0;
  const high60Max = tail60.length >= 30
    ? tail60.reduce((m, h) => Math.max(m, h.high || 0), 0)
    : 0;
  const low60Min = tail60.length >= 30
    ? tail60.reduce((m, h) => Math.min(m, h.low || Infinity), Infinity)
    : Infinity;
  // 上升趋势：现价 > 20 日 close 均
  const closeMa20 = tail20.length
    ? tail20.reduce((s, h) => s + h.close, 0) / tail20.length
    : 0;
  const isUptrend = closeMa20 > 0 && cur.close > closeMa20;

  if (isZt && consec >= 3 && volMa20 > 0 && cur.volume >= volMa20 * 3) {
    volSignal = "zt_fenqi";                 // 高位连板再放巨量 → 分歧/出货警惕
  } else if (isZt && consec >= 2 && volMa5 > 0 && cur.volume < volMa5) {
    volSignal = "zt_suo";                   // 连板途中缩量 → 锁仓强势
  } else if (
    high60Max > 0 && cur.high >= high60Max - EPS &&
    vol60Max > 0 && cur.volume >= vol60Max
  ) {
    volSignal = "tian_liang";               // 天量天价
  } else if (
    Number.isFinite(vol60Min) && cur.volume <= vol60Min &&
    Number.isFinite(low60Min) && low60Min > 0 &&
    cur.low <= low60Min * 1.05 &&            // 价也在近 60 日低位区(≤ 最低价 +5%)
    tail60.length >= 30
  ) {
    volSignal = "di_liang";                 // 地量地价
  } else if (highVol && isUpBar && bodyPct >= 4) {
    volSignal = "ju_yang";                  // 巨量长阳
  } else if (highVol && isDnBar && bodyPct <= -4) {
    volSignal = "ju_yin";                   // 巨量长阴
  } else if (isUptrend && volMa5 > 0 && cur.volume < volMa5 * 0.7 && !isZt) {
    volSignal = "suo_xipan";                // 上升趋势中缩量洗盘
  }

  return {
    tag,
    consecutive: consec,
    highVol,
    limitUp,
    limitDown,
    changePct,
    volMa5,
    volMa20,
    volRatio,
    volSignal,
    volStack: false,                        // 由 detectEvents 后处理填充
    divergence: null,                       // 由 detectEvents 后处理填充
    priceMa5,
    priceMa10,
    priceMa20,
    priceMa60,
  };
}

/**
 * 批量检测：返回与 candles 等长的事件数组
 */
export function detectEvents(
  candles: OHLC[],
  code?: string,
  name?: string,
): CandleEvent[] {
  const pct = limitPctFor(code, name);
  const out: CandleEvent[] = [];
  for (let i = 0; i < candles.length; i++) {
    const prev = i > 0 ? candles[i - 1] : null;
    // history 取到 i-1（不含当日）, 最多 60 根（地量/天量需要）
    const histStart = Math.max(0, i - 60);
    const history = candles.slice(histStart, i);
    out.push(detectOne(candles[i], prev, history, pct));
  }
  // 量堆后处理：当前根所在的连续放量段长度 ≥ 3 即标 volStack
  let run = 0;
  for (let i = 0; i < out.length; i++) {
    if (out[i].volRatio >= 1.5) run += 1;
    else run = 0;
    if (run >= 3) {
      // 把当前 run 内最近的根都标上
      for (let k = i - run + 1; k <= i; k++) out[k].volStack = true;
    }
  }
  // 量价背离后处理:近 6 根(含当日)窗口
  //   顶背离:当日 high 创窗口新高, 但成交量显著萎缩(<= 窗口均量,且 <= 窗口峰量×0.7)
  //   底背离:当日 low  创窗口新低, 但成交量显著萎缩(<= 窗口均量,且 <= 窗口峰量×0.7)
  //
  // 注意:仅"量未到最小"远远不够。价新低+巨量是恐慌破位/出货,不是底背离;
  // 价新高+巨量是放量突破,也不是顶背离。必须真的"缩量"才算背离。
  const DIV_WIN = 6;
  for (let i = DIV_WIN - 1; i < candles.length; i++) {
    const cur = candles[i];
    let maxH = -Infinity, minL = Infinity, maxV = 0;
    let sumV = 0, cntV = 0;
    for (let k = i - DIV_WIN + 1; k <= i; k++) {
      const c = candles[k];
      if (c.high > maxH) maxH = c.high;
      if (c.low < minL) minL = c.low;
      if (c.volume > maxV) maxV = c.volume;
      if (c.volume > 0) { sumV += c.volume; cntV += 1; }
    }
    const avgV = cntV > 0 ? sumV / cntV : 0;
    const shrunk = maxV > 0 && avgV > 0
      && cur.volume <= avgV
      && cur.volume <= maxV * 0.7;
    if (cur.high >= maxH - EPS && shrunk) {
      out[i].divergence = "top";
    } else if (cur.low <= minL + EPS && shrunk) {
      out[i].divergence = "bottom";
    }
  }
  return out;
}

// ── 颜色配置（A 股习惯 红涨绿跌）──
export const EVENT_COLORS = {
  zt:    { fill: "#ff1744", stroke: "#ffeb3b", glyph: "▲", label: "涨停"   },  // 亮红 + 金边
  dt:    { fill: "#00e676", stroke: "#00bcd4", glyph: "▼", label: "跌停"   },  // 亮绿 + 青边
  zb:    { fill: "#ef4444", stroke: "#ff9800", glyph: "⚡", label: "炸板"   },  // 红实体 + 橙边
  yi_zi: { fill: "#ff1744", stroke: "#e91e63", glyph: "▌", label: "一字板" },  // 亮红 + 粉边
  t_zi:  { fill: "#ff1744", stroke: "#9c27b0", glyph: "⊥", label: "T字板"  },  // 亮红 + 紫边
} as const;

/**
 * 给定事件返回最适合的可视化样式（若无主标签返回 null）
 */
export function styleFor(ev: CandleEvent): { fill: string; stroke: string; glyph: string; label: string } | null {
  if (!ev.tag) return null;
  return EVENT_COLORS[ev.tag];
}

// ── 量价信号样式（用于 VOL 区顶端小色块 + tooltip）──
export const VOL_SIGNAL_STYLE: Record<NonNullable<CandleEvent["volSignal"]>, { color: string; label: string; tip: string }> = {
  ju_yang:    { color: "#ff1744", label: "巨阳",   tip: "巨量长阳 · 主力进场" },
  ju_yin:     { color: "#00e676", label: "巨阴",   tip: "巨量长阴 · 主力出货" },
  tian_liang: { color: "#ff9800", label: "天量",   tip: "天量天价 · 见顶警告" },
  di_liang:   { color: "#22d3ee", label: "地量",   tip: "地量地价 · 抛压衰竭" },
  zt_suo:     { color: "#fbbf24", label: "锁仓",   tip: "涨停缩量 · 主力锁仓" },
  zt_fenqi:   { color: "#a855f7", label: "分歧",   tip: "高位连板放量 · 警惕" },
  suo_xipan:  { color: "#94a3b8", label: "缩量",   tip: "上升趋势缩量洗盘" },
};
export const VOL_STACK_COLOR = "#a855f7"; // 紫色 underline
