/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_ENABLE_JOBS?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
