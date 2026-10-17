import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// The admin panel (admin.niadra.com): its own entry and its own output, dist-admin/, which the site
// image serves from outside the site's folder, so niadra.com can never serve it. Only the favicon is
// copied from public/. In development: `npx vite --config vite.admin.config.ts` on :5174, with /api
// going to the local API (server/api.ts, ADMIN_HOST=localhost:5174).
export default defineConfig({
  plugins: [react(), tailwindcss()],
  publicDir: false,
  server: { port: 5174, open: false, proxy: { '/api': { target: 'http://127.0.0.1:8787', changeOrigin: false } } },
  build: {
    outDir: 'dist-admin',
    emptyOutDir: true,
    rollupOptions: { input: 'admin.html' },
  },
})
