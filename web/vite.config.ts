import { defineConfig } from 'vite';

function pagesBase(): string {
  if (process.env.VITE_BASE_PATH) return process.env.VITE_BASE_PATH;
  if (process.env.GITHUB_ACTIONS !== 'true') return '/';

  const repository = process.env.GITHUB_REPOSITORY?.split('/')[1];
  return repository ? `/${repository}/` : '/';
}

export default defineConfig({
  base: pagesBase(),
  // The refresh pipeline points this at staged, validated data for its smoke build.
  publicDir: process.env.VITE_PUBLIC_DIR || 'public',
});
