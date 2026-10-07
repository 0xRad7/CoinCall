/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_GATEWAY_PUBLIC_URL?: string;
  /** 链常量覆盖（src/chain/constants.ts；不设 = 测试网缺省，清单见 .env.example）。 */
  readonly VITE_CHAIN_ID?: string;
  readonly VITE_CHAIN_NAME?: string;
  readonly VITE_RPC_URL?: string;
  readonly VITE_EXPLORER_URL?: string;
  readonly VITE_PAY_VAULT?: string;
  readonly VITE_USDT?: string;
  readonly VITE_ADMIN_ADDRESS?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
