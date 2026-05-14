import { useCallback, useEffect, useRef, useState, type MouseEvent as RME } from "react";
import { TradeChart } from "../components/TradeChart";
// ── Types ──
interface Provider {
  provider: string;
  available: boolean;
  default_model: string;
  models: string[];
}

interface Session {
  id: string;
  title: string;
  llm_provider: string;
  llm_model: string;
  created_at: string;
}

interface Msg {
  role: string;
  content: string;
  tool_calls?: { id: string; name: string; arguments: Record<string, unknown> }[];
  tool_call_id?: string;
  name?: string;
}

interface PlanStep {
  id: number;
  title: string;
  status: "not-started" | "in-progress" | "completed";
}

// stream event types
interface ToolCardState {
  call_id: string;
  name: string;
  arguments: Record<string, unknown>;
  status: "running" | "ok" | "err";
  result?: unknown;
}

interface ApprovalState {
  call_id: string;
  tool_name: string;
  arguments: Record<string, unknown>;
  summary: string;
  permission: string;
  resolved?: string;
}

const QUICK_PROMPTS = [
  { t: "今日涨幅榜", d: "查一下数据库里今天涨幅前10的股票", q: "今天涨幅最大的10只股票是哪些？带名称、涨幅、换手率，并按涨幅排序。" },
  { t: "连板情况", d: "统计当前涨停池的连板分布", q: "今天涨停池里2连板及以上的股票有哪些？按连板数排序。" },
  { t: "形态筛选", d: "运行突破回踩筛选器", q: "帮我用突破回踩模型筛选一下，列出得分最高的10只股票。" },
  { t: "支撑压力", d: "计算个股关键价位", q: "帮我算一下600519贵州茅台的支撑位和压力位。" },
];

// ── Markdown rendering (lightweight) ──
function md(s: string): string {
  // Basic: bold, code blocks, inline code, tables, links, lists
  let out = s
    .replace(/```(\w*)\n([\s\S]*?)```/g, (_m, lang, code) =>
      `<pre class="bg-ink-950 border border-ink-700 rounded-lg p-3 my-2 overflow-x-auto text-xs font-mono"><code class="language-${lang}">${esc(code.trim())}</code></pre>`
    )
    .replace(/`([^`]+)`/g, '<code class="bg-ink-700 px-1.5 py-0.5 rounded text-sky2 text-xs font-mono">$1</code>')
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/^#### (.+)$/gm, '<h4 class="text-xs font-semibold text-ink-100 mt-2 mb-1">$1</h4>')
    .replace(/^### (.+)$/gm, '<h3 class="text-sm font-semibold text-ink-100 mt-3 mb-1">$1</h3>')
    .replace(/^## (.+)$/gm, '<h2 class="text-base font-semibold text-ink-100 mt-4 mb-1">$1</h2>')
    .replace(/^# (.+)$/gm, '<h1 class="text-lg font-bold text-ink-50 mt-4 mb-2">$1</h1>')
    .replace(/^> (.+)$/gm, '<blockquote class="border-l-2 border-sky2 pl-3 text-ink-300 my-1">$1</blockquote>')
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" class="text-sky2 hover:underline">$1</a>');

  // Tables
  out = out.replace(/^(\|.+\|)\n(\|[-| :]+\|)\n((?:\|.+\|\n?)+)/gm, (_m, header, _sep, body) => {
    const ths = header.split("|").filter(Boolean).map((h: string) => `<th class="px-3 py-1.5 text-left text-xs text-ink-300 font-semibold bg-ink-800">${h.trim()}</th>`).join("");
    const rows = body.trim().split("\n").map((row: string) => {
      const tds = row.split("|").filter(Boolean).map((c: string) => `<td class="px-3 py-1.5 border-t border-ink-700 text-sm">${c.trim()}</td>`).join("");
      return `<tr class="hover:bg-ink-850">${tds}</tr>`;
    }).join("");
    return `<div class="overflow-x-auto my-2"><table class="w-full border-collapse border border-ink-700 rounded-lg text-sm"><thead><tr>${ths}</tr></thead><tbody>${rows}</tbody></table></div>`;
  });

  // Stock code links: match 6-digit codes (000xxx, 00xxxx, 3xxxxx, 6xxxxx, 688xxx)
  // Only match when not already inside an HTML tag or href
  out = out.replace(/(?<![\/\w"=])\b([036]\d{5})\b/g,
    '<a href="/stock/$1" target="_blank" class="text-gold hover:underline cursor-pointer" title="查看 $1 K线">$1</a>'
  );

  // Paragraphs
  out = out.replace(/\n{2,}/g, "</p><p>");
  if (!out.startsWith("<")) out = "<p>" + out + "</p>";

  return out;
}

function esc(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function jsonPreview(obj: unknown): string {
  const s = JSON.stringify(obj);
  return s.length > 80 ? s.slice(0, 77) + "..." : s;
}

// ── Main Component ──
// ── URL param helpers ──
function getUrlParam(key: string): string {
  return new URLSearchParams(window.location.search).get(key) || "";
}
function setUrlParams(params: Record<string, string>) {
  const u = new URL(window.location.href);
  for (const [k, v] of Object.entries(params)) {
    if (v) u.searchParams.set(k, v); else u.searchParams.delete(k);
  }
  const target = u.pathname + u.search;
  if (window.location.pathname + window.location.search !== target) {
    window.history.replaceState(null, "", target);
  }
}

export function AgentPage({ initialPrompt, initialImages, onConsumedPrompt }: { initialPrompt?: string; initialImages?: string[]; onConsumedPrompt?: () => void }) {
  const [providers, setProviders] = useState<Provider[]>([]);
  const [provider, setProvider] = useState(() => getUrlParam("provider"));
  const [model, setModel] = useState(() => getUrlParam("model"));
  const [sessions, setSessions] = useState<Session[]>([]);
  const [searchQ, setSearchQ] = useState("");
  const [sid, setSid] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [running, setRunning] = useState(false);
  const [connected, setConnected] = useState(false);

  // Chat items: union of user msg, assistant text, tool cards, approval cards, system notes
  const [items, setItems] = useState<any[]>([]);
  const [streamBuf, setStreamBuf] = useState("");
  const [thinkBuf, setThinkBuf] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [totalTokens, setTotalTokens] = useState(0);

  const [pickerOpen, setPickerOpen] = useState(false);

  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const pickerRef = useRef<HTMLDivElement>(null);

  // Close picker on outside click
  useEffect(() => {
    if (!pickerOpen) return;
    const handler = (e: globalThis.MouseEvent) => {
      if (pickerRef.current && !pickerRef.current.contains(e.target as Node)) setPickerOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [pickerOpen]);

  // Auto-scroll
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [items, streamBuf]);

  // Load providers
  useEffect(() => {
    fetch("/api/agent/providers").then(r => r.json()).then(d => {
      setProviders(d.providers);
      const avail = d.providers.filter((p: Provider) => p.available);
      if (avail.length) {
        // Restore from URL params, fallback to server default
        const urlP = getUrlParam("provider");
        const urlM = getUrlParam("model");
        const matchP = urlP && avail.find((a: Provider) => a.provider === urlP);
        let finalP: string, finalM: string;
        if (matchP) {
          finalP = urlP;
          finalM = matchP.models.includes(urlM) ? urlM : matchP.default_model;
        } else {
          finalP = d.default_provider || avail[0].provider;
          finalM = d.default_model || avail[0].default_model;
        }
        setProvider(finalP);
        setModel(finalM);
        setUrlParams({ provider: finalP, model: finalM });
      }
      setConnected(true);
    }).catch(() => setConnected(false));
  }, []);

  // Load sessions
  const loadSessions = useCallback(async (q?: string) => {
    try {
      const url = q ? `/api/agent/sessions?q=${encodeURIComponent(q)}` : "/api/agent/sessions";
      const d = await fetch(url).then(r => r.json());
      setSessions(d.sessions || []);
    } catch { /* ignore */ }
  }, []);

  useEffect(() => { loadSessions(); }, [loadSessions]);

  // Debounced search
  useEffect(() => {
    const t = setTimeout(() => loadSessions(searchQ || undefined), 300);
    return () => clearTimeout(t);
  }, [searchQ, loadSessions]);

  // Provider change → update model list + sync to active session
  const curProvider = providers.find(p => p.provider === provider);
  useEffect(() => {
    if (curProvider && !curProvider.models.includes(model)) {
      setModel(curProvider.default_model || curProvider.models[0] || "");
    }
  }, [provider, curProvider]);

  // Sync provider/model to active session when changed
  useEffect(() => {
    if (sid && provider) {
      fetch(`/api/agent/sessions/${sid}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ provider, model }),
      }).catch(() => {});
    }
  }, [sid, provider, model]);

  // Open session → load history and restore provider/model
  const openSession = useCallback(async (id: string) => {
    setSid(id);
    setTotalTokens(0);
    // Restore provider/model from session metadata
    const meta = sessions.find(s => s.id === id);
    if (meta) {
      if (meta.llm_provider) setProvider(meta.llm_provider);
      if (meta.llm_model) setModel(meta.llm_model);
    }
    try {
      const d = await fetch(`/api/agent/sessions/${id}/messages`).then(r => r.json());
      const msgs: Msg[] = (d.messages || []).filter((m: Msg) => m.role !== "system");
      const newItems: any[] = [];
      msgs.forEach(m => {
        if (m.role === "user") {
          newItems.push({ type: "user", text: m.content });
        } else if (m.role === "assistant") {
          if (m.tool_calls?.length) {
            m.tool_calls.forEach(tc => {
              newItems.push({ type: "tool", call_id: tc.id, name: tc.name, arguments: tc.arguments, status: "ok" });
            });
          }
          if (m.content) {
            newItems.push({ type: "assistant", text: m.content });
          }
        }
      });
      setItems(newItems);
    } catch { setItems([]); }
  }, []);

  // Auto-open most recent session
  useEffect(() => {
    if (sessions.length && !sid) {
      openSession(sessions[0].id);
    }
  }, [sessions, sid, openSession]);

  // New session
  const newSession = useCallback(async () => {
    if (!provider) return;
    const d = await fetch("/api/agent/sessions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider, model }),
    }).then(r => r.json());
    await loadSessions();
    setSid(d.session_id);
    setItems([]);
  }, [provider, model, loadSessions]);

  // Send message via SSE
  const [pendingImages, setPendingImages] = useState<string[]>([]);
  const send = useCallback(async (text?: string, images?: string[]) => {
    const msg = (text || input).trim();
    if (!msg || running) return;

    // Consume pending images if any
    const imgs = images || (pendingImages.length ? pendingImages : undefined);
    setPendingImages([]);

    // Auto-create session if none
    let currentSid = sid;
    if (!currentSid) {
      const d = await fetch("/api/agent/sessions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ provider, model }),
      }).then(r => r.json());
      currentSid = d.session_id;
      setSid(currentSid);
      await loadSessions();
    }

    setInput("");
    // Reset textarea height after clearing
    if (inputRef.current) {
      inputRef.current.style.height = "auto";
    }
    setRunning(true);
    setStreaming(false);
    setStreamBuf("");
    setThinkBuf("");
    setItems(prev => [...prev, { type: "user", text: msg, images: imgs }]);

    const abort = new AbortController();
    abortRef.current = abort;

    try {
      const body: Record<string, unknown> = { text: msg };
      if (imgs?.length) body.images = imgs;
      const resp = await fetch(`/api/agent/chat/${currentSid}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        signal: abort.signal,
      });

      if (!resp.ok || !resp.body) {
        setItems(prev => [...prev, { type: "system", text: `Error: ${resp.status}` }]);
        setRunning(false);
        return;
      }

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      let accDelta = "";
      let accThink = "";

      const flushDelta = () => {
        if (accThink) {
          setItems(prev => [...prev, { type: "thinking", text: accThink }]);
          setThinkBuf("");
          accThink = "";
        }
        if (accDelta.trim()) {
          setItems(prev => [...prev, { type: "assistant", text: accDelta }]);
        }
        setStreamBuf("");
        setStreaming(false);
        accDelta = "";
      };

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });

        const parts = buf.split("\n\n");
        buf = parts.pop() || "";

        for (const part of parts) {
          const eventMatch = part.match(/^event:\s*(\S+)/m);
          const dataMatch = part.match(/^data:\s*(.*)/m);
          if (!eventMatch || !dataMatch) continue;
          const etype = eventMatch[1];
          let data: any;
          try { data = JSON.parse(dataMatch[1]); } catch { continue; }

          switch (etype) {
            case "step_start":
              break;
            case "thinking_delta":
              accThink += data.delta || "";
              setThinkBuf(accThink);
              setStreaming(true);
              break;
            case "assistant_delta":
              accDelta += data.delta || "";
              setStreamBuf(accDelta);
              setStreaming(true);
              break;
            case "assistant_text":
              flushDelta();
              if (data.text) setItems(prev => [...prev, { type: "assistant", text: data.text }]);
              break;
            case "tool_call":
              flushDelta();
              setItems(prev => [...prev, { type: "tool", ...data, status: "running" }]);
              break;
            case "tool_result":
              setItems(prev => prev.map(it =>
                it.type === "tool" && it.call_id === data.call_id
                  ? { ...it, status: data.ok ? "ok" : "err", result: data.result }
                  : it
              ));
              break;
            case "plan_update": {
              const steps: PlanStep[] = data.steps || [];
              // Replace the most recent plan item; otherwise append
              setItems(prev => {
                const lastPlanIdx = [...prev].reverse().findIndex(it => it.type === "plan");
                if (lastPlanIdx >= 0) {
                  const realIdx = prev.length - 1 - lastPlanIdx;
                  // Only replace if it's the most recent non-tool item (i.e. still being updated this turn)
                  // Heuristic: if any non-plan item came after it, append a new one instead
                  const hasLaterNonPlan = prev.slice(realIdx + 1).some(it => it.type !== "tool");
                  if (!hasLaterNonPlan) {
                    const next = [...prev];
                    next[realIdx] = { type: "plan", steps };
                    return next;
                  }
                }
                return [...prev, { type: "plan", steps }];
              });
              break;
            }
            case "approval_request":
              flushDelta();
              setItems(prev => [...prev, { type: "approval", ...data }]);
              break;
            case "usage":
              setTotalTokens(prev => prev + (data.total_tokens || 0));
              break;
            case "done":
              flushDelta();
              break;
            case "final": {
              // Flush any pending streaming content first
              if (accThink) {
                setItems(prev => [...prev, { type: "thinking", text: accThink }]);
                setThinkBuf("");
                accThink = "";
              }
              const finalText = accDelta.trim() ? accDelta : (data.text || "");
              if (finalText.trim()) {
                setItems(prev => [...prev, { type: "assistant", text: finalText }]);
              }
              accDelta = "";
              setStreamBuf("");
              setStreaming(false);
              break;
            }
            case "cancelled":
              flushDelta();
              setItems(prev => [...prev, { type: "system", text: "已停止" }]);
              break;
            case "error":
              flushDelta();
              setItems(prev => [...prev, { type: "system", text: `⚠️ ${data.error}` }]);
              break;
          }
        }
      }
      // Process any remaining data in buffer (edge case: last event without trailing \n\n)
      buf += decoder.decode(); // flush TextDecoder
      if (buf.trim()) {
        const eventMatch = buf.match(/^event:\s*(\S+)/m);
        const dataMatch = buf.match(/^data:\s*(.*)/m);
        if (eventMatch && dataMatch) {
          const etype = eventMatch[1];
          try {
            const data = JSON.parse(dataMatch[1]);
            if (etype === "final" && data.text?.trim()) {
              if (!accDelta.trim()) {
                accDelta = data.text;
              }
            }
          } catch { /* ignore parse error */ }
        }
      }
      flushDelta();
    } catch (e: any) {
      if (e.name !== "AbortError") {
        setItems(prev => [...prev, { type: "system", text: `Error: ${e.message}` }]);
      }
    } finally {
      setRunning(false);
      setStreaming(false);
      abortRef.current = null;
    }
  }, [input, running, sid, provider, model, loadSessions, pendingImages]);

  // Pre-fill input from K-line page "AI分析" button (don't auto-send)
  const initialFilled = useRef(false);
  useEffect(() => {
    if (initialPrompt && !initialFilled.current) {
      initialFilled.current = true;
      setInput(initialPrompt);
      // Store images for when user hits send
      if (initialImages?.length) {
        setPendingImages(initialImages);
      }
      onConsumedPrompt?.();
      // Auto-resize textarea after React flushes the new value
      setTimeout(() => {
        if (inputRef.current) {
          inputRef.current.style.height = "auto";
          inputRef.current.style.height = Math.min(inputRef.current.scrollHeight, 400) + "px";
          inputRef.current.focus();
          inputRef.current.scrollTop = 0;
        }
      }, 50);
    }
  }, [initialPrompt]);

  // Cancel
  const cancel = useCallback(async () => {
    abortRef.current?.abort();
    if (sid) {
      fetch(`/api/agent/cancel/${sid}`, { method: "POST" }).catch(() => {});
    }
    setRunning(false);
  }, [sid]);

  // Approve
  const approve = useCallback(async (callId: string, decision: string) => {
    if (!sid) return;
    await fetch("/api/agent/approve", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sid, call_id: callId, decision }),
    }).catch(() => {});
    setItems(prev => prev.map(it =>
      it.type === "approval" && it.call_id === callId
        ? { ...it, resolved: decision }
        : it
    ));
  }, [sid]);

  // Keyboard
  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (running) cancel(); else send();
    }
  };

  return (
    <div className="flex-1 flex overflow-hidden bg-ink-950">
      {/* Sidebar */}
      <aside className="w-56 flex-shrink-0 border-r border-ink-800 grad-head flex flex-col">
        <div className="px-3 py-3 border-b border-ink-800 space-y-2">
          <button
            onClick={newSession}
            className="w-full px-3 py-2 bg-gold text-ink-950 rounded-md text-sm font-medium hover:opacity-90 transition"
          >
            ＋ 新建会话
          </button>
          <div className="relative">
            <input
              type="text"
              value={searchQ}
              onChange={e => setSearchQ(e.target.value)}
              placeholder="搜索会话…"
              className="w-full bg-ink-800 border border-ink-700 rounded-md pl-7 pr-2 py-1.5 text-xs text-ink-200 placeholder:text-ink-500 focus:border-sky2 outline-none"
            />
            <span className="absolute left-2 top-1/2 -translate-y-1/2 text-ink-500 text-xs">🔍</span>
            {searchQ && (
              <button
                onClick={() => setSearchQ("")}
                className="absolute right-1.5 top-1/2 -translate-y-1/2 text-ink-500 hover:text-ink-300 text-xs leading-none"
              >✕</button>
            )}
          </div>
        </div>
        <div className="flex-1 overflow-y-auto px-2 py-1 space-y-0.5">
          {sessions.map(s => (
            <button
              key={s.id}
              onClick={() => openSession(s.id)}
              className={`w-full text-left px-3 py-2 rounded-md text-sm transition ${
                s.id === sid ? "bg-ink-800 text-white" : "text-ink-300 hover:bg-ink-800/60"
              }`}
            >
              <div className="truncate">{s.title || "新会话"}</div>
              <div className="text-[10px] text-ink-500 mt-0.5 truncate">
                <span className="bg-ink-700/60 px-1.5 py-0.5 rounded text-[9px]">{s.llm_provider}</span>
                {" "}{s.llm_model}
              </div>
            </button>
          ))}
        </div>
      </aside>

      {/* Main chat area */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Top bar */}
        <div className="flex items-center justify-between px-5 py-2.5 border-b border-ink-800 grad-head flex-shrink-0">
          {/* Model picker button */}
          <div className="relative" ref={pickerRef}>
            <button
              onClick={() => setPickerOpen(o => !o)}
              className="flex items-center gap-2 px-3 py-1.5 rounded-lg hover:bg-ink-800 transition text-sm"
            >
              <span className="w-5 h-5 rounded-md bg-gradient-to-br from-sky2 to-purple-400 flex items-center justify-center text-[10px] text-white font-bold">AI</span>
              <span className="text-ink-200 font-medium max-w-[200px] truncate">{model || "选择模型"}</span>
              <svg className={`w-3.5 h-3.5 text-ink-500 transition ${pickerOpen ? "rotate-180" : ""}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" /></svg>
            </button>
            {pickerOpen && (
              <ModelPicker
                providers={providers}
                provider={provider}
                model={model}
                onSelect={(p, m) => { setProvider(p); setModel(m); setPickerOpen(false); setUrlParams({ provider: p, model: m }); }}
              />
            )}
          </div>
          <div className="flex items-center gap-3 text-xs text-ink-500">
            <span className="flex items-center gap-1.5">
              <span className={`dot ${connected ? "bg-cn-dn" : "bg-ink-500"}`} />
              {connected ? "已连接" : "未连接"}
            </span>
            {totalTokens > 0 && <span className="num">{totalTokens.toLocaleString()} tokens</span>}
          </div>
        </div>

        {/* Messages */}
        <div ref={scrollRef} className="flex-1 overflow-y-auto overflow-x-hidden min-h-0">
          <div className="max-w-4xl mx-auto px-4 py-6 space-y-4">
            {items.length === 0 && !streaming && (
              <div className="flex flex-col items-center justify-center py-20 text-center">
                <div className="text-3xl mb-3 opacity-50">💬</div>
                <h2 className="text-lg font-semibold text-ink-200 mb-1">开始对话</h2>
                <p className="text-sm text-ink-500 max-w-md mb-6">
                  问我任何关于 A 股的事 — 数据查询、技术分析、形态筛选、AI 推荐...
                </p>
                <div className="grid grid-cols-2 gap-2 w-full max-w-lg">
                  {QUICK_PROMPTS.map(p => (
                    <button
                      key={p.t}
                      onClick={() => send(p.q)}
                      className="text-left px-3 py-2.5 bg-ink-900 border border-ink-800 rounded-lg hover:border-gold/40 transition"
                    >
                      <div className="text-sm font-medium text-ink-200">{p.t}</div>
                      <div className="text-xs text-ink-500 mt-0.5">{p.d}</div>
                    </button>
                  ))}
                </div>
              </div>
            )}

            {items.map((item, i) => {
              if (item.type === "user") return <UserBubble key={i} text={item.text} images={item.images} />;
              if (item.type === "thinking") return <ThinkingBlock key={i} text={item.text} />;
              if (item.type === "assistant") {
                // Find the last user message before this assistant message for retry
                let lastUser: any = null;
                for (let j = i - 1; j >= 0; j--) { if (items[j].type === "user") { lastUser = items[j]; break; } }
                const isLast = items.slice(i + 1).every((x: any) => x.type !== "assistant");
                return <AssistantBubble key={i} text={item.text}
                  onRetry={isLast && lastUser ? () => send(lastUser.text, lastUser.images) : undefined} />;
              }
              // update_plan calls are surfaced via the sticky TodoPanel; hide the noisy tool cards.
              if (item.type === "tool" && item.name === "update_plan") return null;
              if (item.type === "tool") return <ToolCard key={i} {...item} />;
              if (item.type === "approval") return <ApprovalCard key={i} {...item} onApprove={approve} />;
              if (item.type === "system") return <SystemNote key={i} text={item.text} />;
              // type === "plan" intentionally not rendered inline; shown as sticky TodoPanel above composer
              return null;
            })}

            {streaming && thinkBuf && !streamBuf && (
              <ThinkingBlock text={thinkBuf} live />
            )}

            {streaming && streamBuf && (
              <div className="space-y-1">
                <div className="flex items-center gap-2 text-[10px] text-ink-500 uppercase tracking-wider">
                  <span className="w-4 h-4 rounded bg-gradient-to-br from-sky2 to-purple-400 flex items-center justify-center text-[8px] text-white font-bold">A</span>
                  Assistant
                </div>
                <div className="bg-ink-900 border border-ink-700 rounded-lg px-4 py-3 text-sm text-ink-200">
                  <div dangerouslySetInnerHTML={{ __html: md(streamBuf) }} />
                  <span className="inline-block w-2 h-4 bg-sky2 animate-pulse ml-0.5" />
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Sticky Todo panel (latest plan_update) — sits right above the composer, like VS Code Todos */}
        <TodoPanel items={items} />

        {/* Composer */}
        <div className="border-t border-ink-800 grad-head px-4 py-3 flex-shrink-0">
          <div className="max-w-4xl mx-auto">
            <div className="bg-ink-800 border border-ink-700 rounded-lg px-3 py-2 focus-within:border-gold/60 transition">
              {pendingImages.length > 0 && (
                <div className="flex gap-2 mb-2 flex-wrap">
                  {pendingImages.map((img, j) => (
                    <div key={j} className="relative group">
                      <img src={img} alt="K线截图" className="h-16 rounded border border-ink-600 object-contain" />
                      <button
                        className="absolute -top-1.5 -right-1.5 w-4 h-4 rounded-full bg-red-500 text-white text-[10px] flex items-center justify-center opacity-0 group-hover:opacity-100 transition"
                        onClick={() => setPendingImages(prev => prev.filter((_, i) => i !== j))}
                      >×</button>
                    </div>
                  ))}
                </div>
              )}
              <textarea
                ref={inputRef}
                value={input}
                onChange={e => {
                  setInput(e.target.value);
                  const ta = e.target;
                  ta.style.height = "auto";
                  ta.style.height = Math.min(ta.scrollHeight, 400) + "px";
                }}
                onKeyDown={onKeyDown}
                placeholder="问点什么..."
                rows={1}
                className="w-full bg-transparent text-sm text-ink-200 placeholder:text-ink-500 outline-none resize-none scrollbar"
                style={{ minHeight: 24, maxHeight: 400 }}
              />
              <div className="flex items-center justify-between mt-1.5">
                <span className="text-[11px] text-ink-600">
                  <span className="kbd">Enter</span> 发送 · <span className="kbd">Shift+Enter</span> 换行
                </span>
                <button
                  onClick={() => running ? cancel() : send()}
                  disabled={!running && !input.trim()}
                  className={`px-4 py-1.5 rounded-md text-sm font-medium transition ${
                    running
                      ? "bg-red-500/20 text-red-400 hover:bg-red-500/30"
                      : "bg-gold text-ink-950 hover:opacity-90 disabled:opacity-30 disabled:cursor-not-allowed"
                  }`}
                >
                  {running ? "停止" : "发送"}
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Sub-components ──

function ThinkingBlock({ text, live }: { text: string; live?: boolean }) {
  const [open, setOpen] = useState(!!live);
  return (
    <div className="space-y-1">
      <button
        onClick={() => setOpen(o => !o)}
        className="flex items-center gap-2 text-[10px] text-ink-500 uppercase tracking-wider hover:text-ink-300 transition"
      >
        <span className="w-4 h-4 rounded bg-purple-500/20 flex items-center justify-center text-[8px]">
          {live ? <span className="animate-pulse">🧠</span> : "🧠"}
        </span>
        {live ? "思考中…" : "思考过程"}
        <span className="text-[9px]">{open ? "▾" : "▸"}</span>
      </button>
      {open && (
        <div className="bg-ink-950 border border-ink-800 rounded-lg px-4 py-3 text-xs text-ink-400 max-h-60 overflow-y-auto whitespace-pre-wrap">
          {text}
          {live && <span className="inline-block w-1.5 h-3 bg-purple-400 animate-pulse ml-0.5" />}
        </div>
      )}
    </div>
  );
}

function UserBubble({ text, images }: { text: string; images?: string[] }) {
  return (
    <div className="space-y-1">
      <div className="flex items-center gap-2 text-[10px] text-ink-500 uppercase tracking-wider">
        <span className="w-4 h-4 rounded bg-sky2 flex items-center justify-center text-[8px] text-white font-bold">U</span>
        You
      </div>
      <div className="bg-sky2/5 border border-sky2/20 rounded-lg px-4 py-3 text-sm text-ink-200 whitespace-pre-wrap">
        {images?.length ? (
          <div className="flex gap-2 mb-2 flex-wrap">
            {images.map((img, j) => (
              <img key={j} src={img} alt="K线截图" className="max-w-[320px] max-h-[200px] rounded border border-ink-700 object-contain" />
            ))}
          </div>
        ) : null}
        {text}
      </div>
    </div>
  );
}

function AssistantBubble({ text, onRetry }: { text: string; onRetry?: () => void }) {
  const [copied, setCopied] = useState(false);
  const [vote, setVote] = useState<"up" | "down" | null>(null);

  const copyText = () => {
    // Strip HTML, copy plain text
    const tmp = document.createElement("div");
    tmp.innerHTML = md(text);
    navigator.clipboard.writeText(tmp.textContent || text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };

  return (
    <div className="space-y-1 group/ab">
      <div className="flex items-center gap-2 text-[10px] text-ink-500 uppercase tracking-wider">
        <span className="w-4 h-4 rounded bg-gradient-to-br from-sky2 to-purple-400 flex items-center justify-center text-[8px] text-white font-bold">A</span>
        Assistant
      </div>
      <div className="bg-ink-900 border border-ink-700 rounded-lg px-4 py-3 text-sm text-ink-200 prose-agent" dangerouslySetInnerHTML={{ __html: md(text) }} />
      <div className="flex items-center gap-1 opacity-0 group-hover/ab:opacity-100 transition-opacity pt-0.5">
        <BubbleBtn title="复制" active={copied} onClick={copyText}>
          {copied ? "✓" : "📋"}
        </BubbleBtn>
        <BubbleBtn title="有用" active={vote === "up"} onClick={() => setVote(v => v === "up" ? null : "up")}>
          👍
        </BubbleBtn>
        <BubbleBtn title="无用" active={vote === "down"} onClick={() => setVote(v => v === "down" ? null : "down")}>
          👎
        </BubbleBtn>
        {onRetry && (
          <BubbleBtn title="重新生成" onClick={onRetry}>🔄</BubbleBtn>
        )}
        <span className="ml-2 text-[10px] text-ink-600">本回答由AI生成，仅供参考，请仔细甄别，谨慎投资。</span>
      </div>
    </div>
  );
}

function BubbleBtn({ title, active, onClick, children }: { title: string; active?: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      title={title}
      onClick={onClick}
      className={`w-7 h-7 rounded-md flex items-center justify-center text-sm transition
        ${active ? "bg-sky2/20 text-sky2" : "bg-ink-800 text-ink-400 hover:text-ink-200 hover:bg-ink-700"}`}
    >
      {children}
    </button>
  );
}

function ToolCard({ name, arguments: args, status, result, call_id }: ToolCardState) {
  const [open, setOpen] = useState(status === "err");

  const iconClass = status === "running"
    ? "bg-yellow-500/15 text-yellow-400"
    : status === "ok"
    ? "bg-cn-dn/15 text-cn-dn"
    : "bg-red-500/15 text-red-400";

  const icon = status === "running" ? "⟳" : status === "ok" ? "✓" : "✕";
  const statusText = status === "running" ? "运行中" : status === "ok" ? "完成" : "失败";

  // Check if result is a chart payload
  const chartData = result && typeof result === "object" && (result as any)._chart ? result as any : null;

  return (
    <div className="border border-ink-700 rounded-lg bg-ink-900 overflow-hidden text-xs">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center gap-2.5 px-3 py-2 hover:bg-ink-800 transition text-left"
      >
        <span className={`w-5 h-5 rounded flex items-center justify-center text-[10px] font-bold ${iconClass}`}>
          {status === "running" ? <span className="animate-spin">⟳</span> : icon}
        </span>
        <span className="font-mono font-semibold text-ink-200">{name}</span>
        <span className="flex-1 font-mono text-ink-500 truncate">{jsonPreview(args)}</span>
        <span className={`text-[10px] ${status === "ok" ? "text-cn-dn" : status === "err" ? "text-red-400" : "text-yellow-400"}`}>
          {statusText}
        </span>
        <span className={`text-ink-500 text-[8px] transition ${open ? "rotate-90" : ""}`}>▶</span>
      </button>
      {/* Inline chart rendering */}
      {chartData && (
        <div className="border-t border-ink-700 px-2 py-2">
          <TradeChart
            candles={chartData.candles || []}
            markers={[]}
            title={chartData.title || ""}
            hlines={chartData.hlines || []}
          />
        </div>
      )}
      {open && (
        <div className="border-t border-ink-700 px-3 py-2.5 space-y-2">
          <div>
            <div className="text-[9px] text-ink-500 uppercase tracking-wider mb-1">参数</div>
            <pre className="bg-ink-950 rounded-md p-2 overflow-x-auto text-ink-300 font-mono">{JSON.stringify(args, null, 2)}</pre>
          </div>
          {result !== undefined && !chartData && (
            <div>
              <div className="text-[9px] text-ink-500 uppercase tracking-wider mb-1">结果</div>
              <pre className="bg-ink-950 rounded-md p-2 overflow-x-auto text-ink-300 font-mono max-h-80 overflow-y-auto">
                {typeof result === "string" ? result : JSON.stringify(result, null, 2)}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function ApprovalCard({
  call_id, tool_name, arguments: args, summary, permission, resolved, onApprove,
}: ApprovalState & { onApprove: (id: string, d: string) => void }) {
  const isDanger = permission === "dangerous";
  return (
    <div className={`border rounded-lg overflow-hidden ${isDanger ? "border-red-500/50" : "border-yellow-500/50"}`}>
      <div className={`px-3 py-2 flex items-center gap-2 font-semibold text-sm ${isDanger ? "bg-red-500/10 text-red-400" : "bg-yellow-500/10 text-yellow-400"}`}>
        {isDanger ? "⚠️ 危险操作" : "🔐 权限请求"}
        <span className="text-ink-500 font-normal">— <code className="text-inherit bg-transparent">{tool_name}</code></span>
      </div>
      <div className="px-3 py-2 bg-ink-900">
        {summary && <div className="text-sm text-ink-300 mb-2">{summary}</div>}
        <pre className="bg-ink-950 rounded-md p-2 text-xs overflow-x-auto font-mono text-ink-300 max-h-48 overflow-y-auto">
          {JSON.stringify(args, null, 2)}
        </pre>
      </div>
      <div className="px-3 py-2 bg-ink-800 border-t border-ink-700 flex gap-2">
        {resolved ? (
          <span className="text-sm text-ink-500">
            {{ allow: "✓ 已允许", always_allow_tool: "✓ 始终允许", deny: "✕ 已拒绝" }[resolved] || resolved}
          </span>
        ) : (
          <>
            <button onClick={() => onApprove(call_id, "allow")} className="px-3 py-1 rounded-md bg-cn-dn text-ink-950 text-sm font-semibold">允许本次</button>
            {!isDanger && <button onClick={() => onApprove(call_id, "always_allow_tool")} className="px-3 py-1 rounded-md bg-sky2 text-ink-950 text-sm font-semibold">始终允许</button>}
            <button onClick={() => onApprove(call_id, "deny")} className="px-3 py-1 rounded-md bg-ink-700 text-ink-200 text-sm font-semibold border border-ink-600">拒绝</button>
          </>
        )}
      </div>
    </div>
  );
}

function SystemNote({ text }: { text: string }) {
  return <div className="text-center text-xs text-ink-500 py-2">{text}</div>;
}

// Sticky Todos panel — VS Code "Todos" style. Picks the most recent plan
// from the items stream and renders it just above the composer. Collapsible.
// Auto-hides if no plan or if all steps are completed AND the latest item
// after the plan is an assistant final message (i.e. the task is fully done).
function TodoPanel({ items }: { items: any[] }) {
  // Find latest plan
  let plan: PlanStep[] | null = null;
  let planIdx = -1;
  for (let i = items.length - 1; i >= 0; i--) {
    if (items[i].type === "plan") {
      plan = items[i].steps as PlanStep[];
      planIdx = i;
      break;
    }
  }
  // Default-collapsed state when fully done; user can re-expand
  const completed = plan ? plan.filter(s => s.status === "completed").length : 0;
  const total = plan ? plan.length : 0;
  const allDone = total > 0 && completed === total;
  // Hide entirely if a new user message arrived after the plan (= new task started)
  const newerUserAfter = plan && items.slice(planIdx + 1).some(it => it.type === "user");

  const [collapsed, setCollapsed] = useState(false);
  // Auto-collapse when fully done
  useEffect(() => {
    if (allDone) setCollapsed(true);
    else setCollapsed(false);
  }, [allDone, planIdx]);

  if (!plan || newerUserAfter) return null;

  const pct = total > 0 ? Math.round((completed / total) * 100) : 0;

  return (
    <div className="border-t border-ink-800 px-4 py-2 flex-shrink-0 bg-ink-950/60 backdrop-blur">
      <div className="max-w-4xl mx-auto">
        <div className="bg-ink-900 border border-ink-800 rounded-lg overflow-hidden">
          {/* Header */}
          <button
            onClick={() => setCollapsed(c => !c)}
            className="w-full flex items-center gap-2 px-3 py-1.5 hover:bg-ink-850 transition text-left"
          >
            <span className={`text-ink-500 text-xs font-mono transition-transform ${collapsed ? "" : "rotate-90"}`}>▸</span>
            <span className="text-xs font-semibold text-ink-200">Todos</span>
            <span className="text-xs text-ink-500">({completed}/{total})</span>
            {!allDone && (
              <span className="ml-1 inline-flex items-center gap-1 text-[10px] text-amber-300 uppercase tracking-wider">
                <span className="w-1.5 h-1.5 rounded-full bg-amber-300 animate-pulse" />
                进行中
              </span>
            )}
            {allDone && (
              <span className="ml-1 text-[10px] text-emerald-400 uppercase tracking-wider">已完成</span>
            )}
            {/* Mini progress bar in the header (always visible) */}
            <div className="ml-auto w-24 h-1 bg-ink-800 rounded-full overflow-hidden">
              <div
                className={`h-full transition-all duration-500 ${allDone ? "bg-emerald-500" : "bg-amber-400"}`}
                style={{ width: `${pct}%` }}
              />
            </div>
          </button>

          {/* Body */}
          {!collapsed && (
            <ol className="px-3 pb-2 pt-1 space-y-1 max-h-48 overflow-y-auto">
              {plan.map(s => {
                const icon = s.status === "completed" ? "✓" : s.status === "in-progress" ? "◐" : "○";
                const iconCls =
                  s.status === "completed" ? "text-emerald-400" :
                  s.status === "in-progress" ? "text-amber-300" :
                  "text-ink-600";
                const titleCls =
                  s.status === "completed" ? "text-ink-500 line-through decoration-emerald-500/40" :
                  s.status === "in-progress" ? "text-ink-100" :
                  "text-ink-400";
                return (
                  <li key={s.id} className="flex items-start gap-2 text-sm leading-tight">
                    <span className={`mt-0.5 w-4 text-center font-mono text-xs ${iconCls}`}>{icon}</span>
                    <span className={titleCls}>{s.title}</span>
                  </li>
                );
              })}
            </ol>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Model Picker Popover ──
function ModelPicker({
  providers, provider, model, onSelect,
}: {
  providers: Provider[];
  provider: string;
  model: string;
  onSelect: (provider: string, model: string) => void;
}) {
  const available = providers.filter(p => p.available);
  return (
    <div className="absolute top-full left-0 mt-1.5 w-72 bg-ink-900 border border-ink-700 rounded-xl shadow-2xl shadow-black/50 z-50 overflow-hidden">
      <div className="px-3 py-2.5 border-b border-ink-800">
        <span className="text-xs text-ink-400 font-medium">选择模型</span>
      </div>
      <div className="max-h-80 overflow-y-auto py-1">
        {available.map(p => (
          <div key={p.provider}>
            <div className="px-3 py-1.5 text-[10px] text-ink-500 uppercase tracking-wider font-semibold flex items-center gap-2">
              <span className={`w-1.5 h-1.5 rounded-full ${p.provider === provider ? "bg-sky2" : "bg-ink-600"}`} />
              {p.provider}
            </div>
            {p.models.map(m => {
              const active = p.provider === provider && m === model;
              return (
                <button
                  key={m}
                  onClick={() => onSelect(p.provider, m)}
                  className={`w-full text-left px-3 py-2 text-sm flex items-center justify-between transition ${
                    active
                      ? "bg-sky2/10 text-sky2"
                      : "text-ink-300 hover:bg-ink-800"
                  }`}
                >
                  <span className="pl-4 truncate">{m}</span>
                  {active && <span className="text-sky2 text-xs flex-shrink-0">✓</span>}
                </button>
              );
            })}
          </div>
        ))}
        {available.length === 0 && (
          <div className="px-3 py-4 text-center text-sm text-ink-500">暂无可用模型</div>
        )}
      </div>
    </div>
  );
}
