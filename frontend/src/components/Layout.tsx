import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { TranscribePage } from '../pages/TranscribePage'

const NAV = [
  { to: '/library',   label: 'Knihovna' },
  { to: '/benchmark', label: 'Benchmark' },
  { to: '/results',   label: 'Výsledky' },
  { to: '/tuning',    label: 'Tuning' },
  { to: '/prepis',    label: 'Přepis' },
  { to: '/models',    label: 'Modely' },
  { to: '/hwflow',    label: 'HW Flow' },
  { to: '/dashboard', label: 'Dashboard' },
]

export function Layout() {
  const { pathname } = useLocation()
  const isPrepis = pathname === '/prepis'

  return (
    <div className="min-h-screen bg-gray-50">
      <nav className="sticky top-0 z-50 bg-white border-b border-gray-200 px-6 py-3 flex items-center gap-6">
        <span className="font-bold text-gray-800 mr-4">aSTT-comp</span>
        {NAV.map(({ to, label }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) =>
              `text-sm font-medium ${isActive ? 'text-blue-600 border-b-2 border-blue-600 pb-0.5' : 'text-gray-600 hover:text-gray-900'}`
            }
          >
            {label}
          </NavLink>
        ))}
      </nav>
      {/* TranscribePage — vždy v DOM, jen CSS skrytá; zachovává stav přepisu při navigaci */}
      <div className={isPrepis ? '' : 'hidden'}>
        <TranscribePage />
      </div>
      {!isPrepis && (
        <main className="p-6 max-w-7xl mx-auto">
          <Outlet />
        </main>
      )}
    </div>
  )
}
