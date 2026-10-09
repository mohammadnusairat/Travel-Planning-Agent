import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api, type Health } from "./api";

interface LlmStatusState {
  health: Health | null;
  offline: boolean;
  /** True when the server's last Claude API call failed for lack of credits (or a usage limit). */
  creditsExhausted: boolean;
  /** True when the server's API key was rejected. */
  keyRejected: boolean;
  refresh: () => void;
}

const LlmStatusContext = createContext<LlmStatusState | null>(null);
const POLL_MS = 30_000;

export function LlmStatusProvider({ children }: { children: ReactNode }) {
  const [health, setHealth] = useState<Health | null>(null);
  const [offline, setOffline] = useState(false);

  const refresh = useCallback(() => {
    api
      .health()
      .then((next) => {
        setHealth(next);
        setOffline(false);
      })
      .catch(() => setOffline(true));
  }, []);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, POLL_MS);
    return () => clearInterval(timer);
  }, [refresh]);

  const status = health?.llm.status;
  return (
    <LlmStatusContext.Provider
      value={{ health, offline, creditsExhausted: status === "credits_exhausted", keyRejected: status === "auth_error", refresh }}
    >
      {children}
    </LlmStatusContext.Provider>
  );
}

export function useLlmStatus(): LlmStatusState {
  const context = useContext(LlmStatusContext);
  if (!context) throw new Error("useLlmStatus must be used inside LlmStatusProvider");
  return context;
}
