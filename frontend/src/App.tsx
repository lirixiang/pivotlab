import { useCallback, useEffect, useState } from "react";
import { TopBar, IndexStrip, type TabKey } from "./components/TopBar";
import { WorkspacePage } from "./pages/WorkspacePage";
import { SystemPage } from "./pages/SystemPage";
import { JournalPage } from "./pages/JournalPage";
import { SyncPage } from "./pages/SyncPage";
import { AgentPage } from "./pages/AgentPage";
import { ToastContainer } from "./components/Toast";

// ── URL ↔ state helpers ──
const TAB_PATHS: Record<TabKey, string> = {
  workspace: "/",
  system: "/system",
  journal: "/journal",
  sync: "/sync",
  agent: "/agent",
};
const PATH_TO_TAB: Record<string, TabKey> = Object.fromEntries(
  Object.entries(TAB_PATHS).map(([k, v]) => [v, k as TabKey]),
) as Record<string, TabKey>;

// 旧路径重定向（M0）：保证书签 / 历史 URL 不 404
const LEGACY_REDIRECT: Record<string, TabKey> = {
  "/recommend": "system",
  "/screener": "system",
  "/aiscan": "system",
  "/llmpick": "system",
  "/backtest": "system",
  "/strategy": "system",
  "/algo": "system",
  "/monitor": "journal",
};

function parseLocation(): { tab: TabKey; code: string; strategyId?: number } {
  const p = window.location.pathname;
  const params = new URLSearchParams(window.location.search);
  const strategyId = params.get("strategy") ? Number(params.get("strategy")) : undefined;
  // /stock/600519 → workspace with code
  const stockMatch = p.match(/^\/stock\/(\d{6})$/);
  if (stockMatch) return { tab: "workspace", code: stockMatch[1], strategyId };
  if (LEGACY_REDIRECT[p]) return { tab: LEGACY_REDIRECT[p], code: "", strategyId };
  return { tab: PATH_TO_TAB[p] ?? "workspace", code: "", strategyId };
}

export default function App() {
  const [tab, setTab] = useState<TabKey>(() => parseLocation().tab);
  const [code, setCode] = useState(() => parseLocation().code || "600519");
  const [strategyId, setStrategyId] = useState<number | undefined>(() => parseLocation().strategyId);
  const [agentInitPrompt, setAgentInitPrompt] = useState<string>("");
  const [agentInitImages, setAgentInitImages] = useState<string[]>([]);

  // Push URL on tab/code change
  const pushUrl = useCallback((t: TabKey, c?: string) => {
    let path: string;
    if (t === "workspace") {
      path = `/stock/${c || code}`;
    } else if (t === "agent") {
      // Preserve existing query params (provider/model) on agent tab
      const curSearch = window.location.pathname === "/agent" ? window.location.search : "";
      path = TAB_PATHS[t] + curSearch;
    } else {
      path = TAB_PATHS[t];
    }
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
  const goWorkspace = useCallback((c: string, sid?: number) => {
    setCode(c);
    setStrategyId(sid);
    setTab("workspace");
    const path = `/stock/${c}` + (sid ? `?strategy=${sid}` : "");
    if (window.location.pathname + window.location.search !== path) {
      window.history.pushState(null, "", path);
    }
  }, []);

  // Handle code selection within workspace (no tab change) — preserves current strategy from URL
  const handleSelectCode = useCallback((c: string) => {
    // Read current strategy from URL to preserve it across stock switches
    const params = new URLSearchParams(window.location.search);
    const sid = params.get("strategy");
    const path = `/stock/${c}` + (sid ? `?strategy=${sid}` : "");
    setCode(c);
    setStrategyId(sid ? Number(sid) : undefined);
    if (window.location.pathname + window.location.search !== path) {
      window.history.pushState(null, "", path);
    }
  }, []);

  // Listen to browser back/forward
  useEffect(() => {
    const onPop = () => {
      const { tab: t, code: c, strategyId: sid } = parseLocation();
      setTab(t);
      if (c) setCode(c);
      setStrategyId(sid);
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
          strategyId={strategyId}
          onStrategyConsumed={() => setStrategyId(undefined)}
          onAIAnalyze={(prompt, images) => {
            setAgentInitPrompt(prompt);
            setAgentInitImages(images || []);
            handleTabChange("agent");
          }}
        />
      )}

      {tab === "system" && <SystemPage />}
      {tab === "journal" && <JournalPage />}
      {tab === "sync" && <SyncPage />}
      {tab === "agent" && <AgentPage initialPrompt={agentInitPrompt} initialImages={agentInitImages} onConsumedPrompt={() => { setAgentInitPrompt(""); setAgentInitImages([]); }} />}

      <ToastContainer />
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
