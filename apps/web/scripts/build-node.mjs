import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../', import.meta.url));
const cli = fileURLToPath(new URL('../node_modules/vinext/dist/cli.js', import.meta.url));
const build = spawnSync(process.execPath, [cli, 'build'], {
  cwd: root,
  stdio: 'inherit',
  env: { ...process.env, WEB_PLATFORM: 'node', NODE_ENV: 'production' },
});
if (build.error) {
  process.stderr.write(`Node production build failed: ${build.error.message}\n`);
}
process.exit(build.status ?? 1);
