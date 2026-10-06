/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_GATEWAY_PUBLIC_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
