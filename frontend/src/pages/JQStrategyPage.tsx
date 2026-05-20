import { useState, useCallback, useRef } from "react";
import Editor from "@monaco-editor/react";
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceLine,
} from "recharts";

type Mode = "backtest" | "screener";

const API = "/api";

// ── 类型 ──────────────────────────────────────────────

interface BacktestResult {
  equity_curve: { date: string; equity: number; cash: number; positions_value: number; drawdown: number }[];
  trades: { date: string; security: string; side: string; qty: number; price: number; commission: number; amount: number }[];
  stats: Record<string, number | string>;
  logs: { level: string; dt: string; msg: string }[];
}

interface ScreenerItem {
  code: string; name: string; score: number; price: number;
  change_pct: number; volume_ratio: number; triggers: string[];
  market: string; industry: string; amount: number;
  rr_ratio: number; support_score: number;
  breakout_price?: number; pullback_price?: number;
}

interface ScreenerResult {
  items: ScreenerItem[];
  logs: { level: string; dt: string; msg: string }[];
  stats: { total_scanned: number; matched: number; elapsed_sec: number; match_rate_pct: number; strategy_name: string };
}

interface ScreenerTemplate { key: string; label: string; code: string; }

interface Strategy {
  id: number;
  name: string;
  code: string;
  description: string;
  type: string;
  updated_at: string;
}

// ── 工具 ──────────────────────────────────────────────

function fmt(n: number | string | undefined, digits = 2, suffix = "") {
  if (n === undefined || n === null) return "—";
  return `${Number(n).toFixed(digits)}${suffix}`;
}

function StatCard({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="bg-gray-800 rounded-lg p-3 flex flex-col gap-1">
      <span className="text-xs text-gray-400">{label}</span>
      <span className={`text-lg font-bold ${color ?? "text-white"}`}>{value}</span>
    </div>
  );
}

// ── 自定义 Tooltip ────────────────────────────────────

function EquityTooltip({ active, payload, label }: any) {
  if (!active || !payload?.length) return null;
  const d = payload[0]?.payload;
  return (
    <div className="bg-gray-900 border border-gray-700 rounded p-2 text-xs text-gray-200">
      <div className="font-semibold mb-1">{label}</div>
      <div>净值：<span className="text-amber-400">{fmt(d?.equity)}</span></div>
      <div>现金：<span className="text-blue-400">{fmt(d?.cash)}</span></div>
      <div>回撤：<span className="text-red-400">{fmt(d?.drawdown, 2, "%")}</span></div>
    </div>
  );
}

// ── 主页面 ────────────────────────────────────────────

export function JQStrategyPage() {
  const [code, setCode] = useState<string>("");
  const [startDate, setStartDate] = useState("2022-01-01");
  const [endDate, setEndDate] = useState("2024-12-31");
  const [initialCash, setInitialCash] = useState("1000000");
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<BacktestResult | null>(null);
  const [error, setError] = useState<string>("");
  const [activeTab, setActiveTab] = useState<"chart" | "trades" | "logs">("chart");

  // 模式切换
  const [mode, setMode] = useState<Mode>("backtest");

  // 筛选模式状态
  const [universe, setUniverse] = useState("all");
  const [scanLimit, setScanLimit] = useState("0");
  const [screenerResult, setScreenerResult] = useState<ScreenerResult | null>(null);
  const [screenerTab, setScreenerTab] = useState<"items" | "logs">("items");
  const [screenerTemplates, setScreenerTemplates] = useState<ScreenerTemplate[]>([]);
  const [showTemplateMenu, setShowTemplateMenu] = useState(false);

  // 策略列表
  const [strategies, setStrategies] = useState<Strategy[]>([]);
  const [showSaveDialog, setShowSaveDialog] = useState(false);
  const [saveName, setSaveName] = useState("");
  const [loadingList, setLoadingList] = useState(false);
  const editorRef = useRef<any>(null);

  // ── 加载示例代码 ──────────────────────────────────
  const loadTemplate = useCallback(async () => {
    const url = mode === "screener" ? `${API}/jq/screener/template` : `${API}/jq/template`;
    const res = await fetch(url);
    const data = await res.json();
    setCode(data.code ?? "");
  }, [mode]);

  // ── 加载内置模板列表（筛选模式） ─────────────────
  const loadScreenerTemplates = useCallback(async () => {
    if (screenerTemplates.length > 0) { setShowTemplateMenu(true); return; }
    const res = await fetch(`${API}/jq/screener/templates`);
    const data: ScreenerTemplate[] = await res.json();
    setScreenerTemplates(data);
    setShowTemplateMenu(true);
  }, [screenerTemplates.length]);

  // ── 运行回测 ──────────────────────────────────────
  const runBacktest = useCallback(async () => {
    if (!code.trim()) { setError("请先输入策略代码"); return; }
    setRunning(true);
    setError("");
    setResult(null);
    try {
      const res = await fetch(`${API}/jq/backtest`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          code,
          start_date: startDate,
          end_date: endDate,
          initial_cash: Number(initialCash),
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail ?? "回测失败");
      setResult(data);
      setActiveTab("chart");
    } catch (e: any) {
      setError(e.message ?? String(e));
    } finally {
      setRunning(false);
    }
  }, [code, startDate, endDate, initialCash]);

  // ── 运行筛选 ──────────────────────────────────────
  const runScreener = useCallback(async () => {
    if (!code.trim()) { setError("请先输入筛选策略代码"); return; }
    setRunning(true);
    setError("");
    setScreenerResult(null);
    try {
      const res = await fetch(`${API}/jq/screener/run`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          code,
          universe,
          max_workers: 8,
          timeout_sec: 600,
          limit: Number(scanLimit),
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail ?? "筛选失败");
      setScreenerResult(data);
      setScreenerTab("items");
    } catch (e: any) {
      setError(e.message ?? String(e));
    } finally {
      setRunning(false);
    }
  }, [code, universe, scanLimit]);

  // ── 加载策略列表 ─────────────────────────────────
  const loadStrategies = useCallback(async () => {
    setLoadingList(true);
    try {
      const res = await fetch(`${API}/jq/strategies`);
      setStrategies(await res.json());
    } finally {
      setLoadingList(false);
    }
  }, []);

  // ── 保存策略 ────────────────────────────────────
  const saveStrategy = useCallback(async () => {
    if (!saveName.trim()) return;
    await fetch(`${API}/jq/strategies`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: saveName, code, type: mode }),
    });
    setSaveName("");
    setShowSaveDialog(false);
    loadStrategies();
  }, [saveName, code, mode, loadStrategies]);

  // ── 删除策略 ────────────────────────────────────
  const deleteStrategy = useCallback(async (id: number) => {
    await fetch(`${API}/jq/strategies/${id}`, { method: "DELETE" });
    setStrategies(prev => prev.filter(s => s.id !== id));
  }, []);

  const stats = result?.stats ?? {};

  return (
    <div className="flex h-full bg-gray-950 text-gray-100 overflow-hidden">

      {/* ── 左侧：编辑器 + 策略列表 ─────────────────── */}
      <div className="flex flex-col w-[52%] min-w-0 border-r border-gray-800">

        {/* 工具栏 */}
        <div className="flex items-center gap-2 px-3 py-2 bg-gray-900 border-b border-gray-800 flex-shrink-0">
          {/* 模式切换 */}
          <div className="flex rounded overflow-hidden border border-gray-700 mr-1">
            {(["backtest", "screener"] as Mode[]).map(m => (
              <button key={m}
                onClick={() => { setMode(m); setError(""); setResult(null); setScreenerResult(null); }}
                className={`px-3 py-1 text-xs transition-colors ${mode === m ? "bg-amber-500 text-black font-semibold" : "bg-gray-800 text-gray-400 hover:bg-gray-700"}`}
              >
                {m === "backtest" ? "回测" : "筛选"}
              </button>
            ))}
          </div>
          <button
            onClick={loadTemplate}
            className="px-3 py-1 text-xs bg-gray-700 hover:bg-gray-600 rounded transition-colors"
          >示例代码</button>
          {mode === "screener" && (
            <div className="relative">
              <button
                onClick={loadScreenerTemplates}
                className="px-3 py-1 text-xs bg-gray-700 hover:bg-gray-600 rounded transition-colors"
              >内置模板 ▾</button>
              {showTemplateMenu && (
                <div className="absolute left-0 top-full mt-1 z-50 w-44 bg-gray-900 border border-gray-700 rounded shadow-xl py-1"
                  onMouseLeave={() => setShowTemplateMenu(false)}>
                  {screenerTemplates.map(t => (
                    <button key={t.key}
                      className="w-full text-left px-3 py-1.5 text-xs text-gray-300 hover:bg-gray-800 hover:text-white"
                      onClick={() => { setCode(t.code); setShowTemplateMenu(false); }}
                    >{t.label}</button>
                  ))}
                </div>
              )}
            </div>
          )}
          <button
            onClick={() => { setShowSaveDialog(true); loadStrategies(); }}
            className="px-3 py-1 text-xs bg-gray-700 hover:bg-gray-600 rounded transition-colors"
          >保存</button>
          <button
            onClick={loadStrategies}
            className="px-3 py-1 text-xs bg-gray-700 hover:bg-gray-600 rounded transition-colors"
          >我的策略</button>
          <div className="flex-1" />
          <button
            onClick={mode === "backtest" ? runBacktest : runScreener}
            disabled={running}
            className="px-4 py-1.5 text-sm bg-amber-500 hover:bg-amber-400 disabled:opacity-50
                       text-black font-semibold rounded transition-colors flex items-center gap-1.5"
          >
            {running ? (
              <><span className="animate-spin inline-block w-3 h-3 border-2 border-black border-t-transparent rounded-full" />{mode === "backtest" ? "运行中…" : "筛选中…"}</>
            ) : mode === "backtest" ? "▶ 运行回测" : "▶ 运行筛选"}
          </button>
        </div>

        {/* 参数行 */}
        {mode === "backtest" ? (
          <div className="flex items-center gap-3 px-3 py-1.5 bg-gray-900/60 border-b border-gray-800 flex-shrink-0 text-xs">
            <label className="flex items-center gap-1 text-gray-400">
              起始
              <input type="date" value={startDate} onChange={e => setStartDate(e.target.value)}
                className="bg-gray-800 border border-gray-700 rounded px-2 py-0.5 text-gray-200 w-32" />
            </label>
            <label className="flex items-center gap-1 text-gray-400">
              结束
              <input type="date" value={endDate} onChange={e => setEndDate(e.target.value)}
                className="bg-gray-800 border border-gray-700 rounded px-2 py-0.5 text-gray-200 w-32" />
            </label>
            <label className="flex items-center gap-1 text-gray-400">
              初始资金
              <input type="number" value={initialCash} onChange={e => setInitialCash(e.target.value)}
                className="bg-gray-800 border border-gray-700 rounded px-2 py-0.5 text-gray-200 w-28" />
            </label>
          </div>
        ) : (
          <div className="flex items-center gap-3 px-3 py-1.5 bg-gray-900/60 border-b border-gray-800 flex-shrink-0 text-xs">
            <label className="flex items-center gap-1 text-gray-400">
              股票池
              <select value={universe} onChange={e => setUniverse(e.target.value)}
                className="bg-gray-800 border border-gray-700 rounded px-2 py-0.5 text-gray-200">
                <option value="all">全主板</option>
                <option value="hs300">沪深300</option>
              </select>
            </label>
            <label className="flex items-center gap-1 text-gray-400">
              调试限制
              <input type="number" min="0" value={scanLimit} onChange={e => setScanLimit(e.target.value)}
                className="bg-gray-800 border border-gray-700 rounded px-2 py-0.5 text-gray-200 w-20"
                placeholder="0=不限" />
              <span className="text-gray-600">只 (0=全量)</span>
            </label>
          </div>
        )}

        {/* Monaco 编辑器 */}
        <div className="flex-1 overflow-hidden">
          <Editor
            height="100%"
            language="python"
            theme="vs-dark"
            value={code}
            onChange={v => setCode(v ?? "")}
            onMount={e => { editorRef.current = e; }}
            options={{
              fontSize: 13,
              minimap: { enabled: false },
              scrollBeyondLastLine: false,
              wordWrap: "on",
              lineNumbers: "on",
              folding: true,
              automaticLayout: true,
              padding: { top: 8 },
            }}
          />
        </div>

        {/* 我的策略列表（折叠面板） */}
        {strategies.length > 0 && (
          <div className="border-t border-gray-800 max-h-48 overflow-y-auto flex-shrink-0">
            <div className="px-3 py-1.5 text-xs text-gray-500 font-semibold bg-gray-900">我的策略</div>
            {strategies.map(s => (
              <div key={s.id}
                className="flex items-center gap-2 px-3 py-1.5 hover:bg-gray-800 cursor-pointer group">
                <span className={`text-[10px] px-1 rounded ${s.type === "screener" ? "bg-blue-900/50 text-blue-400" : "bg-amber-900/50 text-amber-400"}`}>
                  {s.type === "screener" ? "筛选" : "回测"}
                </span>
                <span className="flex-1 text-sm text-gray-200 truncate"
                  onClick={() => setCode(s.code)}>{s.name}</span>
                <span className="text-xs text-gray-500 hidden group-hover:block"
                  onClick={() => setCode(s.code)}>加载</span>
                <button className="text-xs text-red-500 hidden group-hover:block ml-1"
                  onClick={() => deleteStrategy(s.id)}>删除</button>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* ── 右侧：结果区 ───────────────────────────── */}
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">

        {/* 错误提示 */}
        {error && (
          <div className="mx-4 mt-3 px-4 py-2 bg-red-900/50 border border-red-700 rounded text-red-300 text-sm">
            {error}
          </div>
        )}

        {/* 等待态 */}
        {running && (
          <div className="flex-1 flex flex-col items-center justify-center text-gray-500 gap-3">
            <div className="w-8 h-8 border-3 border-amber-500 border-t-transparent rounded-full animate-spin" />
            <span className="text-sm">{mode === "backtest" ? "回测运行中，请稍候…" : "筛选中，全量扫描约需 1~3 分钟…"}</span>
          </div>
        )}

        {/* 空态 */}
        {!running && !result && !screenerResult && !error && (
          <div className="flex-1 flex flex-col items-center justify-center text-gray-600 gap-3 select-none">
            <div className="text-5xl">{mode === "backtest" ? "📈" : "🔍"}</div>
            <div className="text-sm">{mode === "backtest" ? "点击「示例代码」加载策略，然后运行回测" : "点击「示例代码」加载筛选策略，然后运行筛选"}</div>
            <div className="text-xs text-gray-700">
              {mode === "backtest"
                ? "支持 initialize / handle_data / before_trading_start / after_trading_end"
                : "支持 initialize / filter(context, code, name, candles, weekly) / before_scan / after_scan"}
            </div>
          </div>
        )}

        {/* ── 筛选结果 ─────────────────────────── */}
        {!running && screenerResult && mode === "screener" && (
          <div className="flex-1 flex flex-col overflow-hidden p-3 gap-2">
            {/* 统计行 */}
            <div className="flex items-center gap-3 text-xs text-gray-400 flex-shrink-0 px-1">
              <span>扫描 <span className="text-white font-semibold">{screenerResult.stats.total_scanned}</span> 只</span>
              <span>·</span>
              <span>命中 <span className="text-amber-400 font-semibold">{screenerResult.stats.matched}</span> 只</span>
              <span>·</span>
              <span>耗时 <span className="text-white">{screenerResult.stats.elapsed_sec}s</span></span>
              <span>·</span>
              <span>命中率 <span className="text-white">{screenerResult.stats.match_rate_pct}%</span></span>
              <span className="ml-2 text-gray-600">{screenerResult.stats.strategy_name}</span>
            </div>

            {/* 子 Tab */}
            <div className="flex gap-1 flex-shrink-0">
              {([["items", `命中列表 (${screenerResult.items.length})`], ["logs", `日志 (${screenerResult.logs.length})`]] as const).map(([t, label]) => (
                <button key={t}
                  onClick={() => setScreenerTab(t)}
                  className={`px-3 py-1 text-xs rounded transition-colors ${screenerTab === t ? "bg-amber-500 text-black font-semibold" : "bg-gray-800 text-gray-400 hover:bg-gray-700"}`}
                >{label}</button>
              ))}
            </div>

            {/* 命中列表 */}
            {screenerTab === "items" && (
              <div className="flex-1 overflow-auto min-h-0">
                <table className="w-full text-xs text-left">
                  <thead className="sticky top-0 bg-gray-900">
                    <tr className="text-gray-400 border-b border-gray-800">
                      <th className="px-2 py-1.5 w-8">#</th>
                      <th className="px-2 py-1.5">代码/名称</th>
                      <th className="px-2 py-1.5 text-right w-16">评分</th>
                      <th className="px-2 py-1.5 text-right">现价</th>
                      <th className="px-2 py-1.5 text-right">涨跌幅</th>
                      <th className="px-2 py-1.5 text-right">量比</th>
                      <th className="px-2 py-1.5 text-right">盈亏比</th>
                      <th className="px-2 py-1.5 text-right">成交额(万)</th>
                      <th className="px-2 py-1.5">市场</th>
                      <th className="px-2 py-1.5">行业</th>
                      <th className="px-2 py-1.5">触发条件</th>
                    </tr>
                  </thead>
                  <tbody>
                    {screenerResult.items.map((it, i) => {
                      const up = it.change_pct >= 0;
                      const sc = it.score;
                      const scoreColor = sc >= 80 ? "text-amber-400" : sc >= 60 ? "text-sky-400" : "text-gray-400";
                      return (
                        <tr key={it.code} className="border-t border-gray-800 hover:bg-gray-800/50">
                          <td className="px-2 py-1 text-gray-500">{i + 1}</td>
                          <td className="px-2 py-1">
                            <span className="text-gray-100">{it.name}</span>
                            <span className="text-gray-500 ml-1.5 text-[10px]">{it.code}</span>
                          </td>
                          <td className="px-2 py-1 text-right">
                            <span className={scoreColor + " font-semibold"}>{Math.round(sc)}</span>
                          </td>
                          <td className={`px-2 py-1 text-right ${up ? "text-green-400" : "text-red-400"}`}>{it.price.toFixed(2)}</td>
                          <td className={`px-2 py-1 text-right ${up ? "text-green-400" : "text-red-400"}`}>{up ? "+" : ""}{it.change_pct.toFixed(2)}%</td>
                          <td className={`px-2 py-1 text-right ${it.volume_ratio >= 1.5 ? "text-amber-400" : ""}`}>{it.volume_ratio.toFixed(2)}</td>
                          <td className={`px-2 py-1 text-right ${it.rr_ratio >= 3 ? "text-amber-400" : "text-gray-300"}`}>{it.rr_ratio ? it.rr_ratio.toFixed(1) : "—"}</td>
                          <td className="px-2 py-1 text-right text-gray-300">{it.amount ? it.amount.toLocaleString() : "—"}</td>
                          <td className="px-2 py-1 text-gray-400 text-[10px]">{it.market}</td>
                          <td className="px-2 py-1 text-gray-400 text-[10px] max-w-[80px] truncate">{it.industry}</td>
                          <td className="px-2 py-1">
                            <div className="flex flex-wrap gap-0.5">
                              {it.triggers.map((t, j) => (
                                <span key={j} className="text-[10px] px-1.5 py-0.5 rounded bg-amber-900/40 text-amber-300">{t}</span>
                              ))}
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                    {screenerResult.items.length === 0 && (
                      <tr><td colSpan={11} className="px-2 py-6 text-center text-gray-600">无命中结果，请调整筛选条件</td></tr>
                    )}
                  </tbody>
                </table>
              </div>
            )}

            {/* 筛选日志 */}
            {screenerTab === "logs" && (
              <div className="flex-1 overflow-auto min-h-0 font-mono text-xs">
                {screenerResult.logs.map((l, i) => (
                  <div key={i} className={`px-2 py-0.5 border-b border-gray-800/50 ${l.level === "ERROR" ? "text-red-400" : l.level === "WARN" ? "text-yellow-400" : "text-gray-300"}`}>
                    <span className="text-gray-500 mr-2">{l.dt}</span>
                    <span className={`mr-2 ${l.level === "ERROR" ? "text-red-500" : l.level === "WARN" ? "text-yellow-500" : "text-blue-400"}`}>[{l.level}]</span>
                    {l.msg}
                  </div>
                ))}
                {screenerResult.logs.length === 0 && (
                  <div className="px-2 py-6 text-center text-gray-600">无日志输出</div>
                )}
              </div>
            )}
          </div>
        )}

        {/* ── 回测结果 ─────────────────────────────── */}
        {result && mode === "backtest" && (
          <div className="flex-1 flex flex-col overflow-hidden p-3 gap-3">

            {/* 指标卡片 */}
            <div className="grid grid-cols-4 gap-2 flex-shrink-0">
              <StatCard label="总收益" value={fmt(stats.total_return, 2, "%")}
                color={Number(stats.total_return) >= 0 ? "text-green-400" : "text-red-400"} />
              <StatCard label="年化收益" value={fmt(stats.annual_return, 2, "%")}
                color={Number(stats.annual_return) >= 0 ? "text-green-400" : "text-red-400"} />
              <StatCard label="最大回撤" value={fmt(stats.max_drawdown, 2, "%")} color="text-red-400" />
              <StatCard label="夏普比率" value={fmt(stats.sharpe, 3)} />
              <StatCard label="交易次数" value={String(stats.trade_count ?? result.trades.length)} />
              <StatCard label="最终净值" value={`¥${Number(stats.final_equity ?? 0).toLocaleString()}`} />
              <StatCard label="回测天数" value={String(stats.total_days ?? "—")} />
              <StatCard label="耗时" value={`${stats.elapsed_sec ?? "—"}s`} color="text-gray-400" />
            </div>

            {/* 子 Tab */}
            <div className="flex gap-1 flex-shrink-0">
              {(["chart", "trades", "logs"] as const).map(t => (
                <button key={t}
                  onClick={() => setActiveTab(t)}
                  className={`px-3 py-1 text-xs rounded transition-colors ${
                    activeTab === t
                      ? "bg-amber-500 text-black font-semibold"
                      : "bg-gray-800 text-gray-400 hover:bg-gray-700"
                  }`}
                >
                  {t === "chart" ? "净值曲线" : t === "trades" ? `成交记录 (${result.trades.length})` : `日志 (${result.logs.length})`}
                </button>
              ))}
            </div>

            {/* 净值曲线 */}
            {activeTab === "chart" && (
              <div className="flex-1 min-h-0">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={result.equity_curve}
                    margin={{ top: 4, right: 16, left: 0, bottom: 4 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
                    <XAxis dataKey="date" tick={{ fontSize: 10, fill: "#9ca3af" }}
                      tickFormatter={v => v.slice(2)} interval="preserveStartEnd" />
                    <YAxis tick={{ fontSize: 10, fill: "#9ca3af" }} width={72}
                      tickFormatter={v => `¥${(v / 10000).toFixed(0)}w`} />
                    <Tooltip content={<EquityTooltip />} />
                    <ReferenceLine y={Number(initialCash)} stroke="#6b7280" strokeDasharray="4 2" />
                    <Line type="monotone" dataKey="equity" stroke="#f59e0b"
                      dot={false} strokeWidth={2} name="净值" />
                    <Line type="monotone" dataKey="cash" stroke="#3b82f6"
                      dot={false} strokeWidth={1} strokeOpacity={0.5} name="现金" />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            )}

            {/* 成交记录 */}
            {activeTab === "trades" && (
              <div className="flex-1 overflow-auto min-h-0">
                <table className="w-full text-xs text-left">
                  <thead className="sticky top-0 bg-gray-900">
                    <tr className="text-gray-400">
                      <th className="px-2 py-1.5">日期</th>
                      <th className="px-2 py-1.5">标的</th>
                      <th className="px-2 py-1.5">方向</th>
                      <th className="px-2 py-1.5 text-right">数量</th>
                      <th className="px-2 py-1.5 text-right">价格</th>
                      <th className="px-2 py-1.5 text-right">成交额</th>
                      <th className="px-2 py-1.5 text-right">手续费</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.trades.map((t, i) => (
                      <tr key={i} className="border-t border-gray-800 hover:bg-gray-800/50">
                        <td className="px-2 py-1 text-gray-400">{t.date}</td>
                        <td className="px-2 py-1 font-mono">{t.security}</td>
                        <td className={`px-2 py-1 font-semibold ${t.side === "buy" ? "text-green-400" : "text-red-400"}`}>
                          {t.side === "buy" ? "买入" : "卖出"}
                        </td>
                        <td className="px-2 py-1 text-right">{t.qty.toLocaleString()}</td>
                        <td className="px-2 py-1 text-right">{t.price.toFixed(3)}</td>
                        <td className="px-2 py-1 text-right">{t.amount.toLocaleString()}</td>
                        <td className="px-2 py-1 text-right text-gray-500">{t.commission.toFixed(2)}</td>
                      </tr>
                    ))}
                    {result.trades.length === 0 && (
                      <tr><td colSpan={7} className="px-2 py-6 text-center text-gray-600">无成交记录</td></tr>
                    )}
                  </tbody>
                </table>
              </div>
            )}

            {/* 运行日志 */}
            {activeTab === "logs" && (
              <div className="flex-1 overflow-auto min-h-0 font-mono text-xs">
                {result.logs.map((l, i) => (
                  <div key={i} className={`px-2 py-0.5 border-b border-gray-800/50 ${
                    l.level === "ERROR" ? "text-red-400" :
                    l.level === "WARN"  ? "text-yellow-400" :
                    "text-gray-300"
                  }`}>
                    <span className="text-gray-500 mr-2">{l.dt}</span>
                    <span className={`mr-2 ${l.level === "ERROR" ? "text-red-500" : l.level === "WARN" ? "text-yellow-500" : "text-blue-400"}`}>
                      [{l.level}]
                    </span>
                    {l.msg}
                  </div>
                ))}
                {result.logs.length === 0 && (
                  <div className="px-2 py-6 text-center text-gray-600">无日志输出</div>
                )}
              </div>
            )}
          </div>
        )}
      </div>

      {/* ── 保存策略对话框 ───────────────────────────── */}
      {showSaveDialog && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50"
          onClick={() => setShowSaveDialog(false)}>
          <div className="bg-gray-900 border border-gray-700 rounded-lg p-5 w-80 shadow-2xl"
            onClick={e => e.stopPropagation()}>
            <h3 className="text-sm font-semibold mb-3 text-gray-200">保存策略</h3>
            <input
              autoFocus
              value={saveName}
              onChange={e => setSaveName(e.target.value)}
              onKeyDown={e => e.key === "Enter" && saveStrategy()}
              placeholder="策略名称…"
              className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-gray-200
                         placeholder-gray-600 focus:outline-none focus:border-amber-500 mb-3"
            />
            <div className="flex justify-end gap-2">
              <button onClick={() => setShowSaveDialog(false)}
                className="px-3 py-1.5 text-sm bg-gray-700 hover:bg-gray-600 rounded text-gray-300">取消</button>
              <button onClick={saveStrategy}
                className="px-3 py-1.5 text-sm bg-amber-500 hover:bg-amber-400 rounded text-black font-semibold">保存</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
