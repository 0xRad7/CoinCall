/** 调用历史（localStorage）与预算（localStorage）。 */
import { EXPLORER_TX } from "../chain/constants";

export interface CallHistoryEntry {
  ts: number;
  serviceId: string;
  serviceName: string;
  amountHuman: string;
  amountRaw: string;
  receiptId: string | null;
  chargedRaw: string | null;
  ok: boolean;
  errorDetail?: string;
  resultPreview?: string;
}

const HISTORY_KEY = "coincall.callHistory";
const HISTORY_MAX = 100;
const BUDGET_KEY = "coincall.budgetRaw"; // 最小单位；空 = 不限

export function loadHistory(): CallHistoryEntry[] {
  try {
    const raw = localStorage.getItem(HISTORY_KEY);
    if (!raw) return [];
    const arr = JSON.parse(raw);
    return Array.isArray(arr) ? (arr as CallHistoryEntry[]) : [];
  } catch {
    return [];
  }
}

export function appendHistory(e: CallHistoryEntry): void {
  const list = [e, ...loadHistory()].slice(0, HISTORY_MAX);
  localStorage.setItem(HISTORY_KEY, JSON.stringify(list));
  window.dispatchEvent(new CustomEvent("coincall:history"));
}

export function clearHistory(): void {
  localStorage.removeItem(HISTORY_KEY);
  window.dispatchEvent(new CustomEvent("coincall:history"));
}

export function loadBudgetRaw(): bigint | null {
  const v = localStorage.getItem(BUDGET_KEY);
  if (!v) return null;
  try {
    return BigInt(v);
  } catch {
    return null;
  }
}

export function saveBudgetRaw(v: bigint | null): void {
  if (v === null) localStorage.removeItem(BUDGET_KEY);
  else localStorage.setItem(BUDGET_KEY, v.toString());
  window.dispatchEvent(new CustomEvent("coincall:budget"));
}

/** 本会话累计已花费（仅成功计费，402/aborted 从不扣减）。 */
const SPENT_KEY = "coincall.spentRaw";
export function loadSpentRaw(): bigint {
  const v = sessionStorage.getItem(SPENT_KEY);
  return v ? BigInt(v) : 0n;
}
export function addSpentRaw(v: bigint): void {
  sessionStorage.setItem(SPENT_KEY, (loadSpentRaw() + v).toString());
  window.dispatchEvent(new CustomEvent("coincall:spent"));
}

export { EXPLORER_TX };
