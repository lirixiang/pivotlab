import { useCallback, useEffect, useState } from "react";
import { TopBar, IndexStrip, type TabKey } from "./components/TopBar";
import { WorkspacePage } from "./pages/WorkspacePage";
import { ScreenerPage } from "./pages/ScreenerPage";
import { BacktestPage } from "./pages/BacktestPage";
import { MonitorPage } from "./pages/MonitorPage";
import { SyncPage } from "./pages/SyncPage";
import { StrategyPage } from "./pages/StrategyPage";
import type { ScreenerItem } from "./types";

// ── URL ↔ state helpers ──
const TAB_PATHS: Record<TabKey, string> = {
  workspace: "/",
  screener: "/screener",
  backtest: "/backtest",
  strategy: "/strategy",
  monitor: "/monitor",
  sync: "/sync",
};
const PATH_TO_TAB: Record<string, TabKey> = Object.fromEntries(
  Object.entries(TAB_PATHS).map(([k, v]) => [v, k as TabKey]),
) as Record<string, TabKey>;

function parseLocation(): { tab: TabKey; code: string } {
  const p = window.location.pathname;
  // /stock/600519 → workspace with code
  const stockMatch = p.match(/^\/stock\/(\d{6})$/);
  if (stockMatch) return { tab: "workspace", code: stockMatch[1] };
  // /algo → redirect to strategy (merged)
  if (p === "/algo") return { tab: "strategy", code: "" };
  return { tab: PATH_TO_TAB[p] ?? "workspace", code: "" };
}

export default function App() {
  const [tab, setTab] = useState<TabKey>(() => parseLocation().tab);
  const [code, setCode] = useState(() => parseLocation().code || "600519");
  const [breakoutResults, setBreakoutResults] = useState<ScreenerItem[]>([]);
  const [bottomResults, setBottomResults] = useState<ScreenerItem[]>([]);

  const highCount =
    breakoutResults.filter((i) => i.score >= 80).length +
    bottomResults.filter((i) => i.score >= 80).length;

  // Push URL on tab/code change
  const pushUrl = useCallback((t: TabKey, c?: string) => {
    const path = t === "workspace" ? `/stock/${c || code}` : TAB_PATHS[t];
    if (window.location.pathname !== path) {
      window.history.pushState(null, "", path);
    }
  }, [code]);

  // Handle tab changes
  const handleTabChange = useCallback((t: TabKey) => {
    setTab(t);
    pushUrl(t);
  }, [pushUrl]);

  // Handle stock selection — navigate to workspace
  const goWorkspace = useCallback((c: string) => {
    setCode(c);
    setTab("workspace");
    const path = `/stock/${c}`;
    if (window.location.pathname !== path) {
      window.history.pushState(null, "", path);
    }
  }, []);

  // Handle code selection within workspace (no tab change)
  const handleSelectCode = useCallback((c: string) => {
    setCode(c);
    const path = `/stock/${c}`;
    if (window.location.pathname !== path) {
      window.history.pushState(null, "", path);
    }
  }, []);

  // Listen to browser back/forward
  useEffect(() => {
    const onPop = () => {
      const { tab: t, code: c } = parseLocation();
      setTab(t);
      if (c) setCode(c);
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  // Set initial URL if at root
  useEffect(() => {
    if (window.location.pathname === "/") {
      window.history.replaceState(null, "", `/stock/${code}`);
    }
  }, []);

  return (
    <div className="min-h-screen flex flex-col">
      <header className="grad-head border-b border-ink-700 sticky top-0 z-30">
        <TopBar tab={tab} onTabChange={handleTabChange} onSearch={goWorkspace} />
        <IndexStrip />
      </header>

      {tab === "workspace" && (
        <WorkspacePage
          code={code}
          onSelect={handleSelectCode}
          onScanResults={(r) => {
            setBreakoutResults(r.breakout);
            setBottomResults(r.bottom);
          }}
          scanCounts={{
            breakout: breakoutResults.length,
            bottom: bottomResults.length,
            high: highCount,
          }}
          breakoutResults={breakoutResults}
          bottomResults={bottomResults}
        />
      )}

      {tab === "screener" && <ScreenerPage onPickStock={goWorkspace} />}
      {tab === "backtest" && <BacktestPage defaultCode={code} />}
      {tab === "strategy" && <StrategyPage defaultCode={code} />}
      {tab === "monitor" && <MonitorPage onPickStock={goWorkspace} />}
      {tab === "sync" && <SyncPage />}

      <footer className="h-8 border-t border-ink-800 grad-head flex items-center px-4 text-[11px] text-ink-500 gap-4">
        <span className="flex items-center gap-1.5">
          <span className="dot bg-cn-dn" /> akshare 在线
        </span>
        <span className="flex items-center gap-1.5">
          <span className="dot bg-cn-dn" /> 算法引擎 v0.2.0
        </span>
        <span>已扫描 {breakoutResults.length + bottomResults.length} 信号</span>
        <span className="flex-1" />
        <span>
          <span className="kbd">F</span> 重新画线
        </span>
        <span>
          <span className="kbd">S</span> 切筛选
        </span>
        <span className="text-ink-600">© PivotLab · 开源数据 · 仅供研究</span>
      </footer>
    </div>
  );
}
