/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: ['"Montserrat"', 'sans-serif'],
        display: ['"Montserrat"', 'sans-serif'],
        body: ['"Montserrat"', 'sans-serif'],
      },
      colors: {
        brand: {
          navy: '#19454b',
          dark: '#0e282c',
          soft: '#eef5f6',
          tint: '#2b6a72',
          blue: '#19454b',
          'blue-dark': '#0e282c',
          orange: '#964c14',
          'orange-hover': '#783b0e',
          'orange-soft': '#faf3ee',
          success: '#1f8a4d',
          'success-soft': '#eef8f2',
          warning: '#b47610',
          'warning-soft': '#fff6e4',
          slate: '#112629',
          muted: '#586b6e',
          line: '#d1dde0',
        }
      },
      animation: {
        'pulse-subtle': 'pulseSubtle 2.5s cubic-bezier(0.4, 0, 0.6, 1) infinite',
        'fade-in': 'fadeIn 0.35s cubic-bezier(0.16, 1, 0.3, 1) forwards',
        'slide-up': 'slideUp 0.4s cubic-bezier(0.16, 1, 0.3, 1) forwards',
        'pop': 'pop 0.3s cubic-bezier(0.175, 0.885, 0.32, 1.275) forwards',
        'shimmer': 'shimmer 2s linear infinite',
      },
      keyframes: {
        pulseSubtle: {
          '0%, 100%': { opacity: '1', transform: 'scale(1)' },
          '50%': { opacity: '0.85', transform: 'scale(1.02)' },
        },
        fadeIn: {
          '0%': { opacity: '0', transform: 'translateY(6px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        slideUp: {
          '0%': { opacity: '0', transform: 'translateY(16px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        pop: {
          '0%': { transform: 'scale(0.92)' },
          '50%': { transform: 'scale(1.04)' },
          '100%': { transform: 'scale(1)' },
        },
        shimmer: {
          '0%': { backgroundPosition: '-200% 0' },
          '100%': { backgroundPosition: '200% 0' },
        },
      },
    },
  },
  plugins: [],
}
