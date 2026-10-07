/**
 * 双模式身份态（Provider / Consumer）：localStorage 粘滞（coincall.mode），
 * 挂载即把 data-mode 写到 <html>（工作台身份色微差由此驱动）。
 * Provider 缺席时退化为直读写（AppShell 可独立渲染）。
 */
import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

export type Mode = "provider" | "consumer";
const MODE_KEY = "coincall.mode";

export function readMode(): Mode | null {
  try {
    const v = localStorage.getItem(MODE_KEY);
    if (v === "provider" || v === "consumer") return v;
  } catch {
    /* ignore */
  }
  return null;
}

function applyMode(m: Mode | null) {
  try {
    if (m) {
      document.documentElement.dataset.mode = m;
      localStorage.setItem(MODE_KEY, m);
    } else {
      delete document.documentElement.dataset.mode;
      localStorage.removeItem(MODE_KEY);
    }
  } catch {
    /* ignore */
  }
}

export interface ModeState {
  mode: Mode | null;
  setMode: (m: Mode) => void;
  clearMode: () => void;
}

const fallback: ModeState = {
  // getter 动态读：无 Provider 的消费方（测试/独立渲染）不会吃到模块加载时刻的陈旧值
  get mode() {
    return readMode();
  },
  setMode: (m: Mode) => applyMode(m),
  clearMode: () => applyMode(null),
};

const Ctx = createContext<ModeState>(fallback);

export function ModeProvider({ children }: { children: ReactNode }) {
  const [mode, setModeState] = useState<Mode | null>(readMode);
  useEffect(() => {
    applyMode(mode);
  }, [mode]);
  const value = useMemo<ModeState>(
    () => ({
      mode,
      setMode: (m: Mode) => setModeState(m),
      clearMode: () => setModeState(null),
    }),
    [mode]
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useMode(): ModeState {
  return useContext(Ctx);
}
