import { Outlet } from 'react-router-dom'
import SiteHeader from './SiteHeader'

export default function Layout() {
  return <div className="min-h-screen bg-paper font-sans text-ink">
    <SiteHeader />
    <main className="page-rail mx-auto min-h-[calc(100vh-96px)] max-w-[1060px] px-4 py-8 sm:px-8 sm:py-12 lg:px-12"><Outlet /></main>
  </div>
}
