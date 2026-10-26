import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// With no VITE_API_BASE the app calls its own origin, so `npm run dev` and
// `npm run preview` forward the API paths to the local backend (`make serve`).
// A hosted build (Vercel) sets VITE_API_BASE and never goes through this.
const localApi = {
  target: process.env.DEV_API_TARGET || 'http://127.0.0.1:8000',
  changeOrigin: true,
}
const proxy = Object.fromEntries(
  ['/auth', '/fishdex', '/static', '/photos'].map((path) => [path, localApi]),
)

export default defineConfig({
  plugins: [react()],
  server: { proxy },
  preview: { proxy },
})
