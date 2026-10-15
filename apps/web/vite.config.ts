import { sites } from '@openai/sites-vite-plugin';
import tailwindcss from '@tailwindcss/postcss';
import vinext from 'vinext';
import { defineConfig } from 'vite';
import hostingConfig from './.openai/hosting.json';

const SITE_CREATOR_PLACEHOLDER_DATABASE_ID =
  '00000000-0000-4000-8000-000000000000';

const { d1, r2 } = hostingConfig;

// Defaults keep the documented local setup; env overrides let another
// service occupy 5173/8765 without editing files (scripts/dev.py passes API_PORT through).
const apiPort = Number(process.env.API_PORT) || 8765;
const webPort = Number(process.env.WEB_PORT) || 5173;

// macOS Seatbelt blocks FSEvents, so Codex previews need polling for HMR.
const isCodexSeatbeltSandbox = process.env.CODEX_SANDBOX === 'seatbelt';

const localBindingConfig = {
  main: 'vinext/server/fetch-handler',
  compatibility_flags: ['nodejs_compat'],
  d1_databases: d1
    ? [
        {
          binding: d1,
          database_name: 'site-creator-d1',
          database_id: SITE_CREATOR_PLACEHOLDER_DATABASE_ID,
        },
      ]
    : [],
  r2_buckets: r2
    ? [
        {
          binding: r2,
          bucket_name: 'site-creator-r2',
        },
      ]
    : [],
};

export default defineConfig(async () => {
  // Keep Wrangler and Miniflare state project-local. These are non-secret tool
  // settings; application environment belongs in ignored `.env*` files.
  process.env.WRANGLER_WRITE_LOGS ??= 'false';
  process.env.WRANGLER_LOG_PATH ??= '.wrangler/logs';
  process.env.MINIFLARE_REGISTRY_PATH ??= '.wrangler/registry';

  // The Node release keeps the existing local preview profile independent.
  const nodeRelease = process.env.WEB_PLATFORM === 'node';
  const platformPlugins = [];
  if (!nodeRelease) {
    const { cloudflare } = await import('@cloudflare/vite-plugin');
    platformPlugins.push(sites(), cloudflare({
      viteEnvironment: { name: 'rsc', childEnvironments: ['ssr'] },
      config: localBindingConfig,
    }));
  }

  return {
    css: { postcss: { plugins: [tailwindcss()] } },
    envPrefix: 'VITE_PUBLIC_',
    build: { sourcemap: false },
    server: {
      host: '127.0.0.1',
      port: webPort,
      strictPort: true,
      proxy: { '/api': `http://127.0.0.1:${apiPort}` },
      ...(isCodexSeatbeltSandbox
        ? { watch: { useFsEvents: false, usePolling: true } }
        : {}),
    },
    plugins: [
      vinext(),
      ...platformPlugins,
    ],
  };
});
