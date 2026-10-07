/**
 * 主题态（亮/暗双形态，等权重设计）：localStorage 持久化（coincall.theme），
 * 挂载即把 data-theme 写到 <html>；Provider 缺席时退化为直读写（组件仍可用）。
 */
import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

export type Theme = "light" | "dark";
const THEME_KEY = "coincall.theme";

export function readTheme(): Theme {
  try {
    const v = localStorage.getItem(THEME_KEY);
    if (v === "dark" || v === "light") return v;
  } catch {
    /* 私有模式等场景取不到 localStorage → 默认亮色 */
  }
  return "light";
}

function applyTheme(t: Theme) {
  try {
    document.documentElement.dataset.theme = t;
    if (t === "dark") localStorage.setItem(THEME_KEY, "dark");
    else localStorage.setItem(THEME_KEY, "light");
  } catch {
    /* ignore */
  }
}

export interface ThemeState {
  theme: Theme;
  toggle: () => void;
  setTheme: (t: Theme) => void;
}

/** 无 Provider 时的兜底实现：直接读写 DOM/localStorage（不驱动重渲染，仅供孤儿组件展示）。 */
const fallback: ThemeState = {
  theme: readTheme(),
  toggle: () => applyTheme(readTheme() === "dark" ? "light" : "dark"),
  setTheme: applyTheme,
};

const Ctx = createContext<ThemeState>(fallback);

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<Theme>(readTheme);
  useEffect(() => {
    applyTheme(theme);
  }, [theme]);
  const value = useMemo<ThemeState>(
    () => ({
      theme,
      setTheme: (t: Theme) => setThemeState(t),
      toggle: () => setThemeState((t) => (t === "dark" ? "light" : "dark")),
    }),
    [theme]
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useTheme(): ThemeState {
  return useContext(Ctx);
}
