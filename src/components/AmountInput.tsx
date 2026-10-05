/** 金额输入：amount（人类可读）↔ amount_raw（6 位精度）联动换算并解释。 */
import { fromRaw, toRaw } from "../chain/constants";

export function amountToRawChecked(human: string): { ok: true; raw: bigint } | { ok: false; error: string } {
  try {
    return { ok: true, raw: toRaw(human) };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : String(e) };
  }
}

export function AmountInput({
  human,
  onHumanChange,
  fieldError,
}: {
  human: string;
  onHumanChange: (h: string) => void;
  fieldError?: string;
}) {
  const conv = amountToRawChecked(human);
  return (
    <div className="field">
      <label>定价（USDT，人类可读）</label>
      <input
        type="text"
        className={!conv.ok || fieldError ? "invalid" : ""}
        value={human}
        placeholder="例如 0.01"
        onChange={(e) => onHumanChange(e.target.value.trim())}
      />
      {conv.ok ? (
        <div className="help">
          自动换算 <b>amount_raw = {conv.raw.toString()}</b>（最小单位，6 位精度，服务端以此为准）；当前回填 {fromRaw(conv.raw)} USDT
        </div>
      ) : (
        <div className="err" style={{ color: "var(--danger)", fontSize: 12 }}>
          {conv.error}
        </div>
      )}
      {fieldError && (
        <div className="err" style={{ color: "var(--danger)", fontSize: 12 }}>
          服务端：{fieldError}
        </div>
      )}
    </div>
  );
}
