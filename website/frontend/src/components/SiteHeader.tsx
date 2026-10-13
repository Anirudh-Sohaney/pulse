import { useEffect, useState } from 'react'
import { Link, NavLink, useLocation, useNavigate } from 'react-router-dom'
import { Activity, BookOpen, LayoutDashboard, LogOut, Radio, ShieldOff, UserRound } from 'lucide-react'
import { isSignedIn, logout, logoutAll } from '../api'
import { lockBrowserPlan } from '../browserForecast'

const tabs = [
  { to: '/dashboard', label: 'Dashboard', Icon: LayoutDashboard },
  { to: '/signals', label: 'Signals', Icon: Radio },
  { to: '/api-guide', label: 'API guide', Icon: BookOpen },
] as const

export default function SiteHeader({ onLogin }: { onLogin?: () => void }) {
  const location = useLocation()
  const navigate = useNavigate()
  const [signedIn, setSignedIn] = useState<boolean | null>(null)
  const [logoutError, setLogoutError] = useState('')

  useEffect(() => {
    let active = true
    const refresh = () => {
      isSignedIn().then(value => { if (active) setSignedIn(value) })
    }
    refresh()
    window.addEventListener('focus', refresh)
    return () => { active = false; window.removeEventListener('focus', refresh) }
  }, [location.pathname])

  async function handleLogout(allSessions: boolean) {
    lockBrowserPlan()
    setLogoutError('')
    try {
      if (allSessions) await logoutAll()
      else await logout()
    } catch (error) {
      if (!(error instanceof Error) || error.message !== 'Unauthorized') {
        setLogoutError(`Could not end ${allSessions ? 'all server sessions' : 'the server session'}. Your local plan is locked; retry when connected.`)
        return
      }
    }
    setSignedIn(false)
    navigate('/')
  }

  return <header className="border-b border-slate-200/80">
    <div className="page-rail mx-auto max-w-[1060px] px-4 py-4 sm:px-8 sm:py-5">
      <nav className="flex min-h-14 flex-wrap items-center justify-between gap-x-3 gap-y-2 rounded-2xl border border-white bg-paper/90 px-4 py-2 shadow-[0_0_0_2px_white] backdrop-blur-sm sm:rounded-full sm:px-6" aria-label="Main navigation">
        <Link to="/" className="flex shrink-0 items-center gap-2.5 text-lg font-medium tracking-tight text-ink" aria-label="PULSE home"><span className="flex h-8 w-8 items-center justify-center rounded-full bg-ink text-white"><Activity className="h-4 w-4" /></span>PULSE</Link>
        <div className="order-3 flex w-full items-center justify-between gap-1 border-t border-slate-200 pt-2 sm:order-none sm:w-auto sm:justify-center sm:border-0 sm:pt-0">
          {tabs.map(({ to, label, Icon }) => <NavLink key={to} to={to} className={({ isActive }) => `inline-flex items-center justify-center gap-1.5 rounded-full px-2 py-2 text-xs font-medium transition sm:px-3 sm:text-sm ${isActive ? 'bg-white text-ink shadow-sm' : 'text-slate-600 hover:bg-white hover:text-ink'}`}><Icon className="h-4 w-4 shrink-0" /><span>{label}</span></NavLink>)}
        </div>
        <div className="flex min-w-20 justify-end">
          {signedIn === null ? <span className="h-9 w-20" aria-hidden="true" /> : signedIn ? <details className="group relative"><summary className="flex cursor-pointer list-none items-center gap-1.5 rounded-full bg-white px-3 py-2 text-sm font-medium text-ink shadow-sm [&::-webkit-details-marker]:hidden"><UserRound className="h-4 w-4" />Account</summary><div className="absolute right-0 z-20 mt-2 w-52 rounded-xl border border-slate-200 bg-white p-1 shadow-lg"><button onClick={() => handleLogout(false)} className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm text-slate-700 hover:bg-slate-50"><LogOut className="h-4 w-4" />Log out</button><button onClick={() => handleLogout(true)} className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm text-slate-700 hover:bg-slate-50"><ShieldOff className="h-4 w-4" />Sign out everywhere</button></div></details> : onLogin ? <button onClick={onLogin} className="rounded-full bg-white px-4 py-2 text-sm font-medium text-ink shadow-sm hover:bg-slate-100">Log in</button> : <Link to="/#access" className="rounded-full bg-white px-4 py-2 text-sm font-medium text-ink shadow-sm hover:bg-slate-100">Log in</Link>}
        </div>
      </nav>
    </div>
    {logoutError && <p role="alert" className="mx-auto max-w-[1060px] px-4 pb-3 text-sm text-risk-high sm:px-8">{logoutError}</p>}
  </header>
}
