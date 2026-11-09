// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Copyright (c) 2026 ENPC / SquareHarness

// The browser's version of the terminal's "Approve this action? [y/N]".
import { useState } from 'react'
import { api } from '../api'
import { useApp } from '../store'
import { href, type Mode } from '../router'
import { activeTasks } from '../concurrency'
import { ErrorNote, Modal } from './common'

export function Diff({ text }: { text: string }) {
  return (
    <pre className="diff">
      {text.split('\n').map((line, index) => {
        const kind = line.startsWith('+++') || line.startsWith('---') ? 'file'
          : line.startsWith('@@') ? 'hunk' : line.startsWith('+') ? 'add' : line.startsWith('-') ? 'del' : 'same'
        return <div key={index} className={'diff-' + kind}>{line || ' '}</div>
      })}
    </pre>
  )
}

function relative(path: string, workspace?: string) {
  return workspace && path.startsWith(workspace + '/') ? path.slice(workspace.length + 1) : path
}

export function ApprovalDialog() {
  const { snapshot, status, chats, proofs, research } = useApp()
  const approval = snapshot.approvals[0]
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  if (!approval) return null
  const task = activeTasks(snapshot).find((entry) => entry.task.id === approval.task)?.task
  const target = approval.target || task?.target
  const mode: Mode = approval.task_kind === 'chat' ? 'free' : approval.task_kind === 'proof' ? 'prove' : research?.find((job) => job.id === target)?.kind || 'literature'
  const titleText = chats?.find((chat) => chat.id === target)?.title || proofs?.find((job) => job.id === target)?.title || research?.find((job) => job.id === target)?.title || task?.title || target || approval.task
  const [head, ...rest] = approval.preview.split('\n')
  const body = rest.join('\n')
  const decide = async (approve: boolean) => {
    setBusy(true)
    setError('')
    try {
      await api.decide(approval.id, approve)
    } catch (reason) {
      setError((reason as Error).message)
    } finally {
      setBusy(false)
    }
  }
  const title = approval.kind === 'write' ? 'Approve this file change?' : approval.kind === 'python' ? 'Run this Python code?' : 'Confirm this action?'
  return (
    <Modal key={approval.id} title={title} onClose={busy ? undefined : () => { void decide(false) }} wide>
      <p>Requested by {target ? <a href={href(mode, target)}>{titleText}</a> : titleText}.</p>
      {approval.kind === 'write' && (
        <>
          <p>The model asks to write <code>{relative(head.replace(/^WRITE /, ''), status?.workspace)}</code> in your workspace. Nothing changes unless you approve.</p>
          <Diff text={body} />
        </>
      )}
      {approval.kind === 'python' && (
        <>
          <p className="warning-note">Python is not sandboxed. It runs with your account's permissions, files and network. Run only code you have read.</p>
          <pre className="code-block">{body}</pre>
        </>
      )}
      {approval.kind === 'confirm' && <pre className="code-block">{approval.preview}</pre>}
      <ErrorNote>{error}</ErrorNote>
      <div className="modal-actions">
        <button type="button" className="btn" disabled={busy} onClick={() => decide(false)}>Deny</button>
        <button type="button" className="btn btn-primary" disabled={busy} onClick={() => decide(true)}>
          {approval.kind === 'write' ? 'Write the file' : approval.kind === 'python' ? 'Run the code' : 'Approve'}
        </button>
      </div>
    </Modal>
  )
}
