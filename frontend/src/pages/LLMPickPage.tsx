import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, type LlmPickItem, type LlmProvider } from "../services/api";

// ── Sparkline ──
function Sparkline({ data, width = 60, height = 20 }: { data: number[]; width?: number; height?: number }) {
  if (!data || data.length < 2) return <span className="text-ink-700">—</span>;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const span = max - min || 1;
  const stepX = width / (data.length - 1);
  const pts = data.map((v, i) => `${(i * stepX).toFixed(1)},${(height - ((v - min) / span) * height).toFixed(1)}`).join(" ");
  const up = data[data.length - 1] >= data[0];
  return (
    <svg width={width} height={height} className="inline-block align-middle">
      <polyline fill="none" stroke={up ? "#22c55e" : "#ef4444"} strokeWidth="1.2" points={pts} />
    </svg>
  );
}

// ── Filter badge ──
function FilterBadge({ pass: ok, label }: { pass: boolean; label: string }) {
  return (
    <span className={`text-[9px] px-1.5 py-0.5 rounded-full font-medium ${
      ok ? "bg-green-900/30 text-green-400" : "bg-red-900/30 text-red-400"
    }`}>
      {ok ? "✓" : "✗"} {label}
    </span>
  );
}

// ── Format helpers ──
const fmtAmt = (v: number | null | undefined) => {
  if (v == null) return "—";
  if (v >= 1e8) return (v / 1e8).toFixed(2) + "亿";
  if (v >= 1e4) return (v / 1e4).toFixed(0) + "万";
  return v.toFixed(0);
};
const fmtPct = (v: number | null | undefined) => v == null ? "—" : (v >= 0 ? "+" : "") + v.toFixed(2) + "%";
const fmtMCap = (v: number | null | undefined) => {
  if (v == null) return "—";
  if (v >= 1e8) return (v / 1e8).toFixed(0) + "亿";
  return (v / 1e4).toFixed(0) + "万";
};

type SortKey = "total_score" | "close" | "change_pct" | "amount" | "pe_percentile" | "amount_pctile" | "pe_ratio" | "roe";
type SortDir = "asc" | "desc";

const SORT_COLS: { key: SortKey; label: string; defaultDir: SortDir }[] = [
  { key: "total_score",   label: "综合评分", defaultDir: "desc" },
  { key: "close",         label: "现价",     defaultDir: "desc" },
  { key: "change_pct",    label: "涨跌",     defaultDir: "desc" },
  { key: "amount",        label: "成交额",   defaultDir: "desc" },
  { key: "pe_ratio",      label: "PE",       defaultDir: "asc"  },
  { key: "pe_percentile", label: "PE分位",   defaultDir: "asc"  },
  { key: "amount_pctile", label: "拥挤度",   defaultDir: "asc"  },
  { key: "roe",           label: "ROE",      defaultDir: "desc" },
];

function SortTh({ k, label, sortKey, sortDir, onClick }: {
  k: SortKey; label: string; sortKey: SortKey; sortDir: SortDir; onClick: (k: SortKey) => void;
}) {
  const active = sortKey === k;
  return (
    <th className={`text-right font-normal px-2 cursor-pointer select-none hover:text-ink-200 transition whitespace-nowrap ${active ? "text-gold" : ""}`}
        onClick={() => onClick(k)}>
      {label}
      {active && <i className={`fas fa-caret-${sortDir === "desc" ? "down" : "up"} ml-1 text-[9px]`} />}
    </th>
  );
}

// ── Sub-tab type ──
type SubTab = "manual" | "auto";

export function LLMPickPage({ onPickStock }: { onPickStock?: (c: string) => void }) {
  const [subTab, setSubTab] = useState<SubTab>("manual");
  const [providers, setProviders] = useState<LlmProvider[]>([]);
  const [selectedProvider, setSelectedProvider] = useState("deepseek");
  const [prompt, setPrompt] = useState("");
  const [manualInput, setManualInput] = useState("");
  const [results, setResults] = useState<LlmPickItem[]>([]);
  const [summary, setSummary] = useState<{ total: number; passed: number; filtered: number } | null>(null);
  const [llmInfo, setLlmInfo] = useState<{ provider: string; model: string; raw_response: string; candidate_count: number } | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sortKey, setSortKey] = useState<SortKey>("total_score");
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const [showRaw, setShowRaw] = useState(false);
  const [showOnlyPassed, setShowOnlyPassed] = useState(false);
  // Filter params
  const [peMaxPctile, setPeMaxPctile] = useState(0.80);
  const [crowdingMaxPctile, setCrowdingMaxPctile] = useState(0.90);
  const [requireAboveMa20, setRequireAboveMa20] = useState(true);
  const [requirePositiveFlow, setRequirePositiveFlow] = useState(true);
  // History
  const [history, setHistory] = useState<{ ts: string; mode: string; total: number; passed: number; provider: string }[]>([]);
  const [histOpen, setHistOpen] = useState(false);
  const histRef = useRef<HTMLDivElement>(null);

  // Load providers
  useEffect(() => {
    api.llmProviders().then(d => {
      setProviders(d.providers);
      const configured = d.providers.find(p => p.configured);
      if (configured) setSelectedProvider(configured.key);
    }).catch(() => {});
  }, []);

  // Load default prompt
  useEffect(() => {
    api.llmDefaultPrompt().then(d => setPrompt(d.prompt)).catch(() => {});
  }, []);

  // Load history
  const fetchHistory = useCallback(() => {
    api.llmHistory(20).then(d => setHistory(d.history)).catch(() => {});
  }, []);
  useEffect(() => { fetchHistory(); }, [fetchHistory]);

  // Close history on outside click
  useEffect(() => {
    if (!histOpen) return;
    const h = (e: MouseEvent) => {
      if (histRef.current && !histRef.current.contains(e.target as Node)) setHistOpen(false);
    };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, [histOpen]);

  // ── Manual validate ──
  const handleManualValidate = async () => {
    setError(null);
    setLlmInfo(null);
    // Parse input: support "600519 贵州茅台", comma/newline separated, or JSON
    const candidates = parseManualInput(manualInput);
    if (!candidates.length) {
      setError("请输入至少一个股票代码（每行一个，或逗号分隔）");
      return;
    }
    setLoading(true);
    try {
      const resp = await api.llmValidate({
        candidates,
        pe_max_pctile: peMaxPctile,
        crowding_max_pctile: crowdingMaxPctile,
        require_above_ma20: requireAboveMa20,
        require_positive_flow: requirePositiveFlow,
      });
      setResults(resp.results);
      setSummary({ total: resp.total, passed: resp.passed, filtered: resp.filtered });
      fetchHistory();
    } catch (e: any) {
      setError(e.message || "验证失败");
    } finally {
      setLoading(false);
    }
  };

  // ── Auto generate ──
  const handleGenerate = async () => {
    setError(null);
    setLlmInfo(null);
    setLoading(true);
    try {
      const resp = await api.llmGenerate({
        provider: selectedProvider,
        prompt,
        auto_validate: true,
        pe_max_pctile: peMaxPctile,
        crowding_max_pctile: crowdingMaxPctile,
        require_above_ma20: requireAboveMa20,
        require_positive_flow: requirePositiveFlow,
      });
      setResults(resp.results);
      setSummary({ total: resp.total, passed: resp.passed, filtered: resp.filtered });
      if (resp.llm) setLlmInfo(resp.llm);
      if (resp.message) setError(resp.message);
      fetchHistory();
    } catch (e: any) {
      setError(e.message || "生成失败");
    } finally {
      setLoading(false);
    }
  };

  // Load history snapshot
  const loadHistoryItem = async (ts: string) => {
    setHistOpen(false);
    setLoading(true);
    try {
      const d = await api.llmHistoryDetail(ts);
      setResults(d.results || []);
      setSummary({ total: d.total, passed: d.passed, filtered: d.filtered });
    } catch {
      setError("加载历史记录失败");
    } finally {
      setLoading(false);
    }
  };

  const handleSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortDir(d => d === "desc" ? "asc" : "desc");
    } else {
      const col = SORT_COLS.find(c => c.key === key)!;
      setSortKey(key);
      setSortDir(col.defaultDir);
    }
  };

  const sorted = useMemo(() => {
    let arr = showOnlyPassed ? results.filter(r => r.passed) : results;
    const mul = sortDir === "desc" ? -1 : 1;
    return [...arr].sort((a, b) => {
      const va = (a as any)[sortKey] ?? -Infinity;
      const vb = (b as any)[sortKey] ?? -Infinity;
      return (va - vb) * mul;
    });
  }, [results, sortKey, sortDir, showOnlyPassed]);

  const openStock = (code: string) => {
    if (onPickStock) onPickStock(code);
    else window.open(`/stock/${code}`, "_blank");
  };

  return (
    <div className="flex-1 flex flex-col bg-ink-950 overflow-hidden">
      {/* ── Top bar ── */}
      <div className="flex items-center justify-between px-5 py-3 border-b border-ink-800 grad-head flex-wrap gap-y-2">
        <div className="flex items-center gap-4 flex-wrap">
          {/* Sub-tab */}
          <div className="seg">
            <button className={subTab === "manual" ? "on" : ""} onClick={() => setSubTab("manual")}>
              <i className="fas fa-paste mr-1 text-[9px]" /> 手动验证
            </button>
            <button className={subTab === "auto" ? "on" : ""} onClick={() => setSubTab("auto")}>
              <i className="fas fa-robot mr-1 text-[9px]" /> 大模型生成
            </button>
          </div>

          {/* Filter params */}
          <div className="flex items-center gap-2 text-[11px] text-ink-400">
            <span>PE分位≤</span>
            <input type="range" min={0.5} max={1} step={0.05} value={peMaxPctile}
                   onChange={e => setPeMaxPctile(Number(e.target.value))}
                   className="w-16 accent-amber-400" />
            <span className="num text-amber-300 w-9">{(peMaxPctile * 100).toFixed(0)}%</span>
          </div>

          <div className="flex items-center gap-2 text-[11px] text-ink-400">
            <span>拥挤度≤</span>
            <input type="range" min={0.5} max={1} step={0.05} value={crowdingMaxPctile}
                   onChange={e => setCrowdingMaxPctile(Number(e.target.value))}
                   className="w-16 accent-purple-400" />
            <span className="num text-purple-300 w-9">{(crowdingMaxPctile * 100).toFixed(0)}%</span>
          </div>

          <label className="flex items-center gap-1.5 text-[11px] text-ink-400 cursor-pointer">
            <input type="checkbox" checked={requireAboveMa20}
                   onChange={e => setRequireAboveMa20(e.target.checked)}
                   className="accent-green-500" />
            站上MA20
          </label>

          <label className="flex items-center gap-1.5 text-[11px] text-ink-400 cursor-pointer">
            <input type="checkbox" checked={requirePositiveFlow}
                   onChange={e => setRequirePositiveFlow(e.target.checked)}
                   className="accent-blue-500" />
            量能放大
          </label>

          {/* Stats */}
          {summary && (
            <div className="flex items-center gap-2 text-[11px]">
              <span className="chip"><span className="text-ink-100 num mr-1">{summary.total}</span> 候选</span>
              <span className="chip"><span className="text-green-400 num mr-1">{summary.passed}</span> 通过</span>
              <span className="chip"><span className="text-red-400 num mr-1">{summary.filtered}</span> 过滤</span>
            </div>
          )}

          {/* Show only passed toggle */}
          <label className="flex items-center gap-1.5 text-[11px] text-ink-400 cursor-pointer">
            <input type="checkbox" checked={showOnlyPassed}
                   onChange={e => setShowOnlyPassed(e.target.checked)}
                   className="accent-gold" />
            仅看通过
          </label>
        </div>

        <div className="flex items-center gap-2">
          {/* History */}
          <div className="relative" ref={histRef}>
            <button
              className="px-2 py-1.5 text-[11px] rounded-md border border-ink-700 text-ink-400 hover:text-ink-200 hover:border-ink-600 transition"
              onClick={() => { fetchHistory(); setHistOpen(!histOpen); }}
            >
              <i className="fas fa-clock-rotate-left mr-1 text-[9px]" /> 历史
              <i className="fas fa-chevron-down ml-1 text-[8px]" />
            </button>
            {histOpen && (
              <div className="absolute right-0 top-full mt-1 z-50 w-64 bg-ink-850 border border-ink-700 rounded-lg shadow-xl py-1 max-h-72 overflow-y-auto scrollbar">
                {history.length === 0 && <div className="px-3 py-4 text-[10px] text-ink-600 text-center">暂无历史</div>}
                {history.map(h => (
                  <button key={h.ts}
                    className="w-full text-left px-3 py-2 text-[11px] hover:bg-ink-800 text-ink-300 flex justify-between"
                    onClick={() => loadHistoryItem(h.ts)}
                  >
                    <span>
                      {h.ts.replace("_", " ")}
                      <span className="text-ink-600 ml-1">[{h.mode === "generate" ? "AI" : "手动"}]</span>
                    </span>
                    <span className="text-green-400 num">{h.passed}/{h.total}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* ── Input area ── */}
      <div className="px-5 py-3 border-b border-ink-800 bg-ink-900/30">
        {subTab === "manual" ? (
          <div className="flex gap-3">
            <div className="flex-1">
              <textarea
                className="w-full bg-ink-850 border border-ink-700 rounded-lg px-3 py-2 text-[12px] text-ink-200 font-mono resize-none scrollbar placeholder-ink-600"
                rows={4}
                value={manualInput}
                onChange={e => setManualInput(e.target.value)}
                placeholder={"输入股票代码，每行一个或逗号分隔：\n600519 贵州茅台\n000001\n300750, 688981\n\n也可以粘贴JSON:\n[{\"code\":\"600519\",\"logic\":\"消费复苏\",\"risk\":\"社零低迷\"}]"}
              />
            </div>
            <button
              className="px-4 py-2 rounded-md grad-gold text-ink-950 font-semibold text-[12px] self-end disabled:opacity-50"
              onClick={handleManualValidate}
              disabled={loading || !manualInput.trim()}
            >
              {loading ? <><i className="fas fa-circle-notch fa-spin mr-1" /> 验证中…</> : <><i className="fas fa-filter mr-1" /> 量化验证</>}
            </button>
          </div>
        ) : (
          <div className="flex flex-col gap-3">
            <div className="flex items-center gap-3">
              <span className="text-[11px] text-ink-400">模型:</span>
              <div className="seg">
                {providers.map(p => (
                  <button
                    key={p.key}
                    className={selectedProvider === p.key ? "on" : ""}
                    onClick={() => setSelectedProvider(p.key)}
                    title={p.configured ? "已配置API Key" : `未配置 — 请设置环境变量`}
                  >
                    {p.label}
                    {!p.configured && <i className="fas fa-lock ml-1 text-[8px] text-red-400" />}
                  </button>
                ))}
              </div>
            </div>
            <textarea
              className="w-full bg-ink-850 border border-ink-700 rounded-lg px-3 py-2 text-[12px] text-ink-200 resize-none scrollbar"
              rows={5}
              value={prompt}
              onChange={e => setPrompt(e.target.value)}
              placeholder="输入提示词..."
            />
            <div className="flex items-center gap-3">
              <button
                className="px-4 py-2 rounded-md grad-gold text-ink-950 font-semibold text-[12px] disabled:opacity-50"
                onClick={handleGenerate}
                disabled={loading || !providers.find(p => p.key === selectedProvider)?.configured}
              >
                {loading ? (
                  <><i className="fas fa-circle-notch fa-spin mr-1" /> 生成中…</>
                ) : (
                  <><i className="fas fa-wand-magic-sparkles mr-1" /> 一键生成 + 量化验证</>
                )}
              </button>
              {llmInfo && (
                <button className="text-[10px] text-ink-500 hover:text-ink-300" onClick={() => setShowRaw(!showRaw)}>
                  <i className="fas fa-code mr-1" />{showRaw ? "隐藏原始回复" : "查看原始回复"}
                </button>
              )}
              {llmInfo && (
                <span className="text-[10px] text-ink-600">
                  {llmInfo.provider}/{llmInfo.model} · {llmInfo.candidate_count}只候选
                </span>
              )}
            </div>
            {showRaw && llmInfo?.raw_response && (
              <pre className="text-[11px] text-ink-400 bg-ink-900 border border-ink-800 rounded-lg p-3 max-h-40 overflow-y-auto scrollbar whitespace-pre-wrap">
                {llmInfo.raw_response}
              </pre>
            )}
          </div>
        )}
        {error && (
          <div className="mt-2 text-[11px] text-red-400">
            <i className="fas fa-exclamation-circle mr-1" />{error}
          </div>
        )}
      </div>

      {/* ── Results table ── */}
      <div className="overflow-y-auto scrollbar flex-1">
        <table className="w-full text-[12px] num">
          <thead className="text-ink-500 text-[10px] tracking-wider uppercase sticky top-0 grad-head z-10">
            <tr className="border-b border-ink-800">
              <th className="text-left font-normal px-5 py-2.5 w-8">#</th>
              <th className="text-left font-normal px-2">代码 / 名称</th>
              <th className="text-left font-normal px-2 max-w-[180px]">核心逻辑</th>
              <th className="text-left font-normal px-2 max-w-[180px]">风险点</th>
              <th className="text-left font-normal px-2">主题</th>
              <SortTh k="total_score" label="评分" sortKey={sortKey} sortDir={sortDir} onClick={handleSort} />
              <SortTh k="close" label="现价" sortKey={sortKey} sortDir={sortDir} onClick={handleSort} />
              <SortTh k="change_pct" label="涨跌" sortKey={sortKey} sortDir={sortDir} onClick={handleSort} />
              <SortTh k="amount" label="成交额" sortKey={sortKey} sortDir={sortDir} onClick={handleSort} />
              <SortTh k="pe_ratio" label="PE" sortKey={sortKey} sortDir={sortDir} onClick={handleSort} />
              <SortTh k="pe_percentile" label="PE分位" sortKey={sortKey} sortDir={sortDir} onClick={handleSort} />
              <SortTh k="roe" label="ROE" sortKey={sortKey} sortDir={sortDir} onClick={handleSort} />
              <SortTh k="amount_pctile" label="拥挤度" sortKey={sortKey} sortDir={sortDir} onClick={handleSort} />
              <th className="text-center font-normal px-2">量能</th>
              <th className="text-center font-normal px-2">均线</th>
              <th className="text-center font-normal px-2">筛选</th>
              <th className="text-center font-normal px-2 w-16">走势</th>
            </tr>
          </thead>
          <tbody className="text-ink-200">
            {sorted.length === 0 && !loading && (
              <tr>
                <td colSpan={17} className="text-center text-ink-500 py-16">
                  <i className="fas fa-microscope text-2xl text-ink-700 block mb-3" />
                  {subTab === "manual"
                    ? "粘贴大模型给出的股票列表，点击「量化验证」开始筛选"
                    : "选择大模型，编辑提示词，点击「一键生成」"}
                </td>
              </tr>
            )}
            {loading && sorted.length === 0 && (
              <tr>
                <td colSpan={17} className="text-center text-ink-500 py-16">
                  <i className="fas fa-circle-notch fa-spin text-xl text-amber-400 block mb-3" />
                  {subTab === "auto" ? "正在调用大模型并量化验证…" : "正在量化验证…"}
                </td>
              </tr>
            )}
            {sorted.map((r, i) => {
              const up = (r.change_pct ?? 0) >= 0;
              return (
                <tr key={r.code}
                    className={`row-hover border-b border-ink-850/70 cursor-pointer ${!r.passed ? "opacity-50" : ""}`}
                    onClick={() => openStock(r.code)}>
                  <td className="px-5 py-2.5 text-ink-500">{i + 1}</td>
                  <td className="px-2">
                    <div className="font-sans text-ink-100">{r.name}</div>
                    <div className="text-[10px] text-ink-500">{r.code} · {r.market || "—"}</div>
                  </td>
                  <td className="px-2 max-w-[180px]">
                    <div className="text-[11px] text-ink-300 truncate" title={r.logic}>{r.logic || "—"}</div>
                  </td>
                  <td className="px-2 max-w-[180px]">
                    <div className="text-[11px] text-red-400/70 truncate" title={r.risk}>{r.risk || "—"}</div>
                  </td>
                  <td className="px-2">
                    {r.theme ? (
                      <span className="text-[9px] px-1.5 py-0.5 rounded bg-blue-900/30 text-blue-400">{r.theme}</span>
                    ) : "—"}
                  </td>
                  <td className="text-right px-2">
                    <span className={`font-medium ${r.total_score >= 70 ? "text-green-400" : r.total_score >= 50 ? "text-amber-400" : "text-red-400"}`}>
                      {r.total_score.toFixed(1)}
                    </span>
                  </td>
                  <td className="text-right px-2 text-ink-300">{r.close.toFixed(2)}</td>
                  <td className={"text-right px-2 " + (up ? "text-cn-up" : "text-cn-dn")}>{fmtPct(r.change_pct)}</td>
                  <td className="text-right px-2 text-ink-400">{fmtAmt(r.amount)}</td>
                  <td className="text-right px-2 text-ink-400">{r.pe_ratio != null ? r.pe_ratio.toFixed(1) : "—"}</td>
                  <td className="text-right px-2">
                    {r.pe_percentile != null ? (
                      <span className={r.pe_percentile > 0.8 ? "text-red-400" : r.pe_percentile > 0.5 ? "text-amber-400" : "text-green-400"}>
                        {(r.pe_percentile * 100).toFixed(0)}%
                      </span>
                    ) : "—"}
                  </td>
                  <td className="text-right px-2 text-ink-400">
                    {r.roe != null ? r.roe.toFixed(1) + "%" : "—"}
                  </td>
                  <td className="text-right px-2">
                    {r.amount_pctile != null ? (
                      <span className={r.amount_pctile > 0.9 ? "text-red-400" : r.amount_pctile > 0.7 ? "text-amber-400" : "text-green-400"}>
                        {(r.amount_pctile * 100).toFixed(0)}%
                      </span>
                    ) : "—"}
                  </td>
                  <td className="text-center px-2">
                    {r.vol_ma5_ratio != null ? (
                      <span className={r.vol_ma5_ratio > 1.2 ? "text-green-400" : r.vol_ma5_ratio < 0.7 ? "text-red-400" : "text-ink-400"}>
                        {r.vol_ma5_ratio.toFixed(2)}x
                      </span>
                    ) : "—"}
                  </td>
                  <td className="text-center px-2">
                    {r.ma_aligned ? (
                      <span className="text-[9px] text-green-400"><i className="fas fa-arrow-trend-up" /> 多头</span>
                    ) : r.above_ma20 ? (
                      <span className="text-[9px] text-amber-400">站上MA20</span>
                    ) : (
                      <span className="text-[9px] text-red-400">破MA20</span>
                    )}
                  </td>
                  <td className="text-center px-2">
                    <div className="flex flex-wrap gap-0.5 justify-center">
                      <FilterBadge pass={r.pass_valuation} label="估值" />
                      <FilterBadge pass={r.pass_flow} label="量" />
                      <FilterBadge pass={r.pass_crowding} label="挤" />
                      <FilterBadge pass={r.pass_technical} label="技" />
                    </div>
                    {(r.fail_reasons ?? []).length > 0 && (
                      <div className="text-[8px] text-red-400/60 mt-0.5 leading-tight" title={(r.fail_reasons ?? []).join("; ")}>
                        {r.fail_reasons[0]}
                      </div>
                    )}
                  </td>
                  <td className="text-center px-2">
                    <Sparkline data={r.sparkline} />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}


// ── Parse manual input ──
function parseManualInput(input: string): { code: string; name?: string; logic?: string; risk?: string; theme?: string }[] {
  const trimmed = input.trim();
  if (!trimmed) return [];

  // Try JSON first
  if (trimmed.startsWith("[")) {
    try {
      const arr = JSON.parse(trimmed);
      if (Array.isArray(arr)) {
        return arr.filter((item: any) => item.code).map((item: any) => ({
          code: String(item.code).trim(),
          name: item.name,
          logic: item.logic,
          risk: item.risk,
          theme: item.theme,
        }));
      }
    } catch {
      // Fall through to text parsing
    }
  }

  // Text parsing: split by newline, comma, space
  const lines = trimmed.split(/[\n,;]+/).map(s => s.trim()).filter(Boolean);
  const parsed: { code: string; name?: string }[] = [];
  for (const line of lines) {
    const parts = line.split(/\s+/);
    const code = parts[0].replace(/[^0-9]/g, "");
    if (code.length === 6) parsed.push({ code, name: parts[1] });
  }
  return parsed;
}
