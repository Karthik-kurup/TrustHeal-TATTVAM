/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // TrustHeal colour language (dark ops-centre palette)
        healthy:    '#22c55e',   // green
        degraded:   '#f59e0b',   // amber
        critical:   '#ef4444',   // red
        acting:     '#3b82f6',   // blue
        recovering: '#14b8a6',   // teal
        escalated:  '#a855f7',   // purple
        unknown:    '#6b7280',   // grey
        // Surface tokens
        surface:    '#111827',   // dark background
        panel:      '#1f2937',   // card/panel
        border:     '#374151',   // borders
        muted:      '#9ca3af',   // secondary text
      },
      fontFamily: {
        sans: ['-apple-system', 'Segoe UI', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'Fira Code', 'monospace'],
      },
    },
  },
  plugins: [],
}
