import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import { markdown } from './build/markdown'

// Tests of the site and of the Console (src/console). The markdown plugin is the
// build's, so tests can load the blog through src/seo.ts. Dates render in a fixed
// zone so assertions do not depend on the machine running them.
process.env.TZ = 'America/Sao_Paulo'

export default defineConfig({
  plugins: [react(), markdown()],
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.{ts,tsx}', 'server/**/*.test.ts'],
    setupFiles: ['./src/console/test/setup.ts'],
    css: false,
    pool: 'threads',
    maxWorkers: 2,
    testTimeout: 15000,
  },
})
