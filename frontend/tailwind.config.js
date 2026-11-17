/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Calibri', 'Inter', 'system-ui', 'sans-serif'],
      },
      colors: {
        bank: {
          50: '#eff6ff',
          500: '#3b82f6',
          700: '#1d4ed8',
        },
        books: {
          50: '#f0fdf4',
          500: '#22c55e',
          700: '#15803d',
        },
        warn: '#f59e0b',
        danger: '#ef4444',
      },
    },
  },
  plugins: [],
}

