import { href, type Route } from '../router'
import { useApp } from '../store'
import { BackIcon } from './Icons'

export function JobNavigation({ route }: { route: Route }) {
  const app = useApp()
  if (!['prove', 'literature', 'referee', 'writeup', 'style'].includes(route.mode)) return null
  const origin = route.id && route.from && (!app.chats || app.chats.some((chat) => chat.id === route.from))
    ? route.from : null
  return (
    <nav className="job-navigation" aria-label="Return to conversation mode">
      <a className="btn btn-quiet back-link" href={href('free', origin)}>
        <BackIcon size={18} /> {origin ? 'Back to conversation' : 'Back to conversation mode'}
      </a>
    </nav>
  )
}
