/** 异步四态 hook：loading / 空(null) / 错误 / 成功。 */
import { useCallback, useEffect, useRef, useState } from "react";

export interface AsyncState<T> {
  data: T | null;
  loading: boolean;
  error: unknown;
  reload: () => void;
  setData: (t: T | null) => void;
}

export function useAsync<T>(fn: () => Promise<T>, deps: unknown[] = [], opts?: { pollMs?: number }): AsyncState<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [tick, setTick] = useState(0);
  const aliveRef = useRef(true);
  const fnRef = useRef(fn);
  fnRef.current = fn;

  const reload = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fnRef
      .current()
      .then((d) => {
        if (!cancelled && aliveRef.current) {
          setData(d);
          setError(null);
        }
      })
      .catch((e) => {
        if (!cancelled && aliveRef.current) setError(e);
      })
      .finally(() => {
        if (!cancelled && aliveRef.current) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);

  useEffect(() => {
    if (!opts?.pollMs) return;
    const id = setInterval(reload, opts.pollMs);
    return () => clearInterval(id);
  }, [opts?.pollMs, reload]);

  return { data, loading, error, reload, setData };
}
