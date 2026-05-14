import { useState, useCallback, type ReactNode, type KeyboardEvent } from "react";

const PASS_HASH = "a3f5b8c2d1e9"; // derived token stored in localStorage
const STORAGE_KEY = "pivotlab_auth";

/** Simple SHA-like hash — NOT cryptographic, just enough to avoid storing
 *  the password in plain text in source / localStorage. */
function simpleHash(s: string): string {
  let h = 0;
  for (let i = 0; i < s.length; i++) {
    h = ((h << 5) - h + s.charCodeAt(i)) | 0;
  }
  return Math.abs(h).toString(36);
}

// Pre-computed hash of the real password
const EXPECTED = simpleHash("lrx243473");

export function LoginGate({ children }: { children: ReactNode }) {
  const [authed, setAuthed] = useState(
    () => localStorage.getItem(STORAGE_KEY) === EXPECTED,
  );
  const [pw, setPw] = useState("");
  const [error, setError] = useState(false);

  const handleLogin = useCallback(() => {
    if (simpleHash(pw) === EXPECTED) {
      localStorage.setItem(STORAGE_KEY, EXPECTED);
      setAuthed(true);
    } else {
      setError(true);
      setTimeout(() => setError(false), 1500);
    }
  }, [pw]);

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
        {/* Logo / title */}
        <div>
          <div className="text-2xl font-medium text-zinc-400 tracking-wide">
            Internal Tools
          </div>
          <div className="mt-1 text-xs text-zinc-600">Authorized access only</div>
        </div>

        {/* Password input */}
        <input
          type="password"
          autoFocus
          value={pw}
          onChange={(e) => setPw(e.target.value)}
          onKeyDown={handleKey}
          placeholder="Password"
          className={`w-full rounded-lg border bg-zinc-900 px-4 py-2.5 text-sm text-zinc-100
            placeholder:text-zinc-600 outline-none transition
            ${error ? "border-red-500 animate-shake" : "border-zinc-700 focus:border-zinc-500"}`}
        />

        {/* Login button */}
        <button
          onClick={handleLogin}
          className="w-full rounded-lg bg-zinc-700 py-2.5 text-sm font-medium text-zinc-200
            hover:bg-zinc-600 active:bg-zinc-800 transition"
        >
          Sign in
        </button>

        {error && (
          <div className="text-xs text-red-400">Access denied</div>
        )}
      </div>
    </div>
  );
}
