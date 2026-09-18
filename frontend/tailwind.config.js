/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        its: {
          bg: '#0B0F19',
          card: '#111827',
          border: '#1F2937',
          accent: '#3B82F6',
          danger: '#EF4444',
          warning: '#F59E0B',
          success: '#10B981',
        }
      },
      animation: {
        'pulse-fast': 'pulse 1s cubic-bezier(0.4, 0, 0.6, 1) infinite',
        'siren': 'siren 0.8s ease-in-out infinite alternate',
      },
      keyframes: {
        siren: {
          '0%': { boxShadow: '0 0 15px rgba(239, 68, 68, 0.4)' },
          '100%': { boxShadow: '0 0 35px rgba(239, 68, 68, 0.9)' },
        }
      }
    },
  },
  plugins: [],
}
