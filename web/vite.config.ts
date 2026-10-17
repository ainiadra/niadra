import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { markdown } from './build/markdown'

export default defineConfig(({ isSsrBuild }) => ({
  plugins: [react(), tailwindcss(), markdown()],
  // In development, /api goes to the local API (server/api.ts, port 8787).
  server: { port: 5173, open: false, proxy: { '/api': 'http://127.0.0.1:8787' } },
  build: {
    rollupOptions: {
      // In the prerender the text of the posts is bundled on purpose (src/blog/posts.ts);
      // the "dynamic import that does not become a separate file" warning only matters for the browser.
      onwarn(warning, warn) {
        if (isSsrBuild && warning.message.includes('dynamic import will not move module')) return
        warn(warning)
      },
    },
  },
}))
