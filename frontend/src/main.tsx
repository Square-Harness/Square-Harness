// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Copyright (c) 2026 SquareHarness Team

import { Component, StrictMode, type ReactNode } from 'react'
import { createRoot } from 'react-dom/client'
import '@fontsource-variable/atkinson-hyperlegible-next'
import './theme.css'
import './shell.css'
import './content.css'
import { App } from './App'
import { applyTheme, readTheme, start } from './store'

class ErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false }
  static getDerivedStateFromError() { return { failed: true } }
  render() {
    return this.state.failed ? <div className="sheet paper"><p role="alert">This page could not be displayed. <a href="#/free" onClick={() => this.setState({ failed: false })}>Open conversations</a> or reload the page.</p></div> : this.props.children
  }
}

applyTheme(readTheme())
start()
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary><App /></ErrorBoundary>
  </StrictMode>,
)
