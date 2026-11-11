// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

import { useEffect, useRef, useState } from 'react'
import { ApprovalDialog } from './components/Approval'
import { DropZone } from './components/DropZone'
import { FeedbackButton } from './components/Feedback'
import { Login } from './components/Login'
import { JobNavigation } from './components/JobNavigation'
import { BrandMark, SideSpine, Sidebar, TopBar } from './components/Shell'
import { MODES } from './modes'
import { usePanelHidden } from './panels'
import { useRoute, type Route } from './router'
import { useApp } from './store'
import { ChatView } from './views/ChatView'
import { ProofNew } from './views/ProofNew'
import { ProofView } from './views/ProofView'
import { ResearchNew } from './views/ResearchNew'
import { ResearchView } from './views/ResearchView'
import { WriteupNew } from './views/WriteupNew'
import { WriteupView } from './views/WriteupView'
import { StyleNew, StyleView } from './views/Style'

function View({ route }: { route: Route }) {
  const info = MODES[route.mode]
  if (info.kind === 'proof') return route.id ? <ProofView key={route.id} id={route.id} tab={route.tab} /> : <ProofNew />
  if (info.kind === 'chat' || info.kind === 'free') {
    // Older critique and exploration chats open as they were; new ones start in Default.
    const mode = route.id ? (route.mode as 'critic' | 'explore' | 'free') : 'free'
    return <ChatView key={mode + (route.id || '')} mode={mode} id={route.id} />
  }
  if (info.kind === 'writeup') return route.id ? <WriteupView key={route.id} id={route.id} tab={route.tab} /> : <WriteupNew />
  if (info.kind === 'style') return route.id ? <StyleView key={route.id} id={route.id} tab={route.tab} /> : <StyleNew />
  return route.id
    ? <ResearchView key={route.id} id={route.id} tab={route.tab} />
    : <ResearchNew key={route.mode} kind={route.mode as 'literature' | 'referee'} />
}

export function App() {
  const app = useApp()
  const route = useRoute()
  const [drawer, setDrawer] = useState(false)
  const navigation = useRef<HTMLDivElement>(null)
  const listHidden = usePanelHidden('sidebar')
  const feedbackChatId = ['free', 'critic', 'explore'].includes(route.mode) ? route.id
    : app.chats?.some((chat) => chat.id === route.from) ? route.from : null
  useEffect(() => setDrawer(false), [route.mode, route.id])
  useEffect(() => {
    if (!drawer) return
    const previous = document.activeElement as HTMLElement | null
    const controls = () => Array.from(navigation.current?.querySelectorAll<HTMLElement>(
      'a[href], button:not([disabled]), input:not([disabled]), [tabindex="0"]') || [])
      .filter((element) => element.getClientRects().length > 0)
    controls()[0]?.focus()
    const keydown = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); setDrawer(false) }
      if (event.key !== 'Tab') return
      const items = controls(), first = items[0], last = items[items.length - 1]
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus() }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus() }
    }
    document.addEventListener('keydown', keydown)
    return () => { document.removeEventListener('keydown', keydown); if (previous?.isConnected) previous.focus() }
  }, [drawer])
  if (app.auth === 'checking') {
    return <div className="splash paper"><BrandMark size={56} /></div>
  }
  if (app.auth === 'needed') return <Login />
  return (
    <div className={'app' + (listHidden ? ' list-hidden' : '')}>
      <div ref={navigation} className={'nav' + (drawer ? ' nav-open' : '')}
        role={drawer ? 'dialog' : undefined} aria-modal={drawer || undefined} aria-label={drawer ? 'Navigation' : undefined}>
        <Sidebar route={route} />
      </div>
      {listHidden && <SideSpine route={route} />}
      {drawer && <div className="scrim" onClick={() => setDrawer(false)} />}
      <main className="main">
        <TopBar route={route} onMenu={() => setDrawer(true)} />
        <FeedbackButton chatId={feedbackChatId} chatTitle={app.chats?.find((chat) => chat.id === feedbackChatId)?.title} />
        {app.connection === 'reconnecting' && (
          <div className="connection" role="status">Reconnecting to the harness. Saved work is safe; live updates resume when it answers.</div>
        )}
        <JobNavigation route={route} />
        <View route={route} />
      </main>
      <ApprovalDialog />
      <DropZone />
    </div>
  )
}
