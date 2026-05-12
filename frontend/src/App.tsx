import { useCallback, useEffect, useState } from "react";
import { TopBar, IndexStrip, type TabKey } from "./components/TopBar";
import { WorkspacePage } from "./pages/WorkspacePage";
import { ScreenerPage } from "./pages/ScreenerPage";
import { AIScanPage } from "./pages/AIScanPage";
import { BacktestPage } from "./pages/BacktestPage";
import { MonitorPage } from "./pages/MonitorPage";
import { SyncPage } from "./pages/SyncPage";
import { StrategyPage } from "./pages/StrategyPage";
import { RecommendPage } from "./pages/RecommendPage";
import { LLMPickPage } from "./pages/LLMPickPage";
import { AgentPage } from "./pages/AgentPage";

// ── URL ↔ state helpers ──
const TAB_PATHS: Record<TabKey, string> = {
  recommend: "/recommend",
  workspace: "/",
  screener: "/screener",
  aiscan: "/aiscan",
  llmpick: "/llmpick",
  agent: "/agent",
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
  const [recommendInitCode, setRecommendInitCode] = useState<string>("");

  // Push URL on tab/code change
  const pushUrl = useCallback((t: TabKey, c?: string) => {
    const path = t === "workspace" ? `/stock/${c || code}` : TAB_PATHS[t];
    // Strip query/hash when switching top-level tabs — each page has its
    // own sub-state in ?params, those don't carry meaning across tabs.
    const cur = window.location.pathname + window.location.search + window.location.hash;
    if (cur !== path) {
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

  // Bridge: jump from Screener to Recommend filtered by stock code
  const goRecommend = useCallback((c: string) => {
    setRecommendInitCode(c);
    setTab("recommend");
    if (window.location.pathname !== "/recommend") {
      window.history.pushState(null, "", "/recommend");
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
    <div className="h-screen flex flex-col overflow-hidden">
      <header className="grad-head border-b border-ink-700 sticky top-0 z-30">
        <TopBar tab={tab} onTabChange={handleTabChange} onSearch={goWorkspace} />
        <IndexStrip />
      </header>

      {tab === "workspace" && (
        <WorkspacePage
          code={code}
          onSelect={handleSelectCode}
        />
      )}

      {tab === "screener" && <ScreenerPage onPickStock={goWorkspace} onShowRecommend={goRecommend} />}
      {tab === "aiscan" && <AIScanPage defaultCode={code} />}
      <div style={{ display: tab === "llmpick" ? "flex" : "none", flex: 1, flexDirection: "column" }}>
        <LLMPickPage onPickStock={goWorkspace} />
      </div>
      {tab === "backtest" && <BacktestPage defaultCode={code} />}
      {tab === "strategy" && <StrategyPage defaultCode={code} />}
      {tab === "monitor" && <MonitorPage onPickStock={goWorkspace} />}
      {tab === "sync" && <SyncPage />}
      {tab === "agent" && <AgentPage />}
      {tab === "recommend" && (
        <RecommendPage
          onPickStock={goWorkspace}
          initialCode={recommendInitCode}
          onClearInitial={() => setRecommendInitCode("")}
        />
      )}

      <footer className="h-8 border-t border-ink-800 grad-head flex items-center px-4 text-[11px] text-ink-500 gap-4">
        <span className="flex items-center gap-1.5">
          <span className="dot bg-cn-dn" /> akshare 在线
        </span>
        <span className="flex items-center gap-1.5">
          <span className="dot bg-cn-dn" /> 算法引擎 v0.2.0
        </span>
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
