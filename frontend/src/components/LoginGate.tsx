import { useState, useCallback, type ReactNode, type KeyboardEvent } from "react";

const STORAGE_KEY = "pivotlab_auth";

function simpleHash(s: string): string {
  let h = 0;
  for (let i = 0; i < s.length; i++) {
    h = ((h << 5) - h + s.charCodeAt(i)) | 0;
  }
  return Math.abs(h).toString(36);
}

const EXPECTED_USER = "admin";
const EXPECTED_HASH = simpleHash("admin");

export function LoginGate({ children }: { children: ReactNode }) {
  const [authed, setAuthed] = useState(
    () => localStorage.getItem(STORAGE_KEY) === EXPECTED_HASH,
  );
  const [user, setUser] = useState("admin");
  const [pw, setPw] = useState("admin");
  const [error, setError] = useState(false);

  const handleLogin = useCallback(() => {
    if (user === EXPECTED_USER && simpleHash(pw) === EXPECTED_HASH) {
      localStorage.setItem(STORAGE_KEY, EXPECTED_HASH);
      setAuthed(true);
    } else {
      setError(true);
      setTimeout(() => setError(false), 1500);
    }
  }, [user, pw]);

  const handleKey = useCallback(
    (e: KeyboardEvent) => {
      if (e.key === "Enter") handleLogin();
    },
    [handleLogin],
  );

  if (authed) return <>{children}</>;

  return (
    <div className="fixed inset-0 flex items-center justify-center bg-ink-950 px-4">
      {/* subtle gold glow behind the card */}
      <div
        className="pointer-events-none absolute h-72 w-72 rounded-full opacity-[0.07] blur-3xl"
        style={{ background: "radial-gradient(circle, #d4a857 0%, transparent 70%)" }}
      />

      <div className="relative w-[360px] rounded-2xl border border-edge grad-card p-8 shadow-2xl shadow-black/40">
        <div className="mb-7 flex flex-col items-center text-center">
          <div className="grad-gold mb-4 flex h-11 w-11 items-center justify-center rounded-xl shadow-lg shadow-black/30">
            <span className="text-lg font-bold text-ink-950">P</span>
          </div>
          <div className="text-xl font-semibold tracking-wide text-ink-100">
            PivotLab <span className="text-[#d4a857]">智线</span>
          </div>
          <div className="mt-1.5 text-xs text-ink-500">演示版 · 默认账号 admin / admin</div>
        </div>

        <div className="space-y-3">
          <div>
            <label className="mb-1.5 block text-[11px] font-medium uppercase tracking-wider text-ink-500">
              用户名
            </label>
            <input
              type="text"
              value={user}
              onChange={(e) => setUser(e.target.value)}
              onKeyDown={handleKey}
              placeholder="用户名"
              className={`w-full rounded-lg border bg-[#0e1320] px-3.5 py-2.5 text-sm text-ink-200
                placeholder:text-ink-600 outline-none transition
                ${error ? "border-cn-up" : "border-[#1f2535] focus:border-[#d4a857]"}`}
            />
          </div>
          <div>
            <label className="mb-1.5 block text-[11px] font-medium uppercase tracking-wider text-ink-500">
              密码
            </label>
            <input
              type="password"
              value={pw}
              onChange={(e) => setPw(e.target.value)}
              onKeyDown={handleKey}
              placeholder="密码"
              className={`w-full rounded-lg border bg-[#0e1320] px-3.5 py-2.5 text-sm text-ink-200
                placeholder:text-ink-600 outline-none transition
                ${error ? "border-cn-up animate-shake" : "border-[#1f2535] focus:border-[#d4a857]"}`}
            />
          </div>
        </div>

        <button
          onClick={handleLogin}
          className="grad-gold mt-6 w-full rounded-lg py-2.5 text-sm font-semibold text-ink-950
            transition hover:brightness-110 active:brightness-95"
        >
          登录
        </button>

        <div className="mt-3 h-4 text-center text-xs text-cn-up">
          {error && "用户名或密码错误"}
        </div>
      </div>
    </div>
  );
}
