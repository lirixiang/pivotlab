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
  const [user, setUser] = useState("");
  const [pw, setPw] = useState("");
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
    <div className="fixed inset-0 flex items-center justify-center bg-[#0b0e11]">
      <div className="w-80 space-y-5 text-center">
        <div>
          <div className="text-2xl font-medium text-zinc-300 tracking-wide">
            PivotLab 智线
          </div>
          <div className="mt-1 text-xs text-zinc-500">演示版 · 默认账号 admin / admin</div>
        </div>

        <div className="space-y-2">
          <input
            type="text"
            autoFocus
            value={user}
            onChange={(e) => setUser(e.target.value)}
            onKeyDown={handleKey}
            placeholder="用户名"
            className={`w-full rounded-lg border bg-zinc-900 px-4 py-2.5 text-sm text-zinc-100
              placeholder:text-zinc-600 outline-none transition
              ${error ? "border-red-500" : "border-zinc-700 focus:border-zinc-500"}`}
          />
          <input
            type="password"
            value={pw}
            onChange={(e) => setPw(e.target.value)}
            onKeyDown={handleKey}
            placeholder="密码"
            className={`w-full rounded-lg border bg-zinc-900 px-4 py-2.5 text-sm text-zinc-100
              placeholder:text-zinc-600 outline-none transition
              ${error ? "border-red-500 animate-shake" : "border-zinc-700 focus:border-zinc-500"}`}
          />
        </div>

        <button
          onClick={handleLogin}
          className="w-full rounded-lg bg-zinc-700 py-2.5 text-sm font-medium text-zinc-200
            hover:bg-zinc-600 active:bg-zinc-800 transition"
        >
          登录
        </button>

        {error && (
          <div className="text-xs text-red-400">用户名或密码错误</div>
        )}
      </div>
    </div>
  );
}
