import { useEffect, useState, type FormEvent } from 'react'
import { api, type ResearchDetail, type StyleChoice, type StyleProfile } from '../api'
import { Collapse, ErrorNote, FilePicker, Loading, NumberField, OptionalNumberField, Tabs, UsageMeters, WorkProgressNote, useLoad } from '../components/common'
import { EffortSlider, effortLimits, type EffortLevel } from '../components/Effort'
import { PauseIcon, PlayIcon } from '../components/Icons'
import { Markdown } from '../components/Markdown'
import { IMAGE_SUFFIXES, isImagePath } from '../components/ImageAttachments'
import { researchLook, researchResumable, Square, type Variant } from '../components/Square'
import { capacityFull, taskForJob } from '../concurrency'
import { useDropTarget } from '../drop'
import { ago } from '../format'
import { go } from '../router'
import { useApp, useTick } from '../store'
import { Files } from './ProofView'
import { ActivityLog, ArtifactViewer, BusyNote, JobAside, ROLES, StreamBody, queuedMessage, useJobClass, useLiveStream } from './shared'

const NAME = /^[\w][\w .-]{0,59}$/u
const TEMPLATE = ['.tex', '.sty', '.cls', '.bib']
const STEPS = ['scan', 'classify', 'observe', 'condense', 'save'] as const
const STEP_NAMES: Record<string, string> = { scan: 'Read', classify: 'Domains', observe: 'Articles', condense: 'Merge', save: 'Save', done: 'Done' }

/** Learn from earlier articles or screenshots; exact macros require LaTeX sources. */
export function StyleNew() {
  const { status, snapshot } = useApp()
  const d = status?.defaults
  const filesChanged = useTick('files')
  const [articles, setArticles] = useState<string[] | null>(null)
  const [selected, setSelected] = useState<string[]>([])
  const [templateFiles, setTemplateFiles] = useState<string[]>([])
  const [saved, setSaved] = useState<StyleProfile[]>([])
  const [choice, setChoice] = useState<StyleChoice>({ mode: 'auto' })
  const [name, setName] = useState('')
  const [goal, setGoal] = useState('')
  const [level, setLevel] = useState<EffortLevel>('medium')
  const [tokens, setTokens] = useState<number | null>(null)
  const [minutes, setMinutes] = useState(15)
  const [inputTokens, setInputTokens] = useState<number | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [queuedNote, setQueuedNote] = useState('')

  useEffect(() => {
    api.files().then((result) => setArticles(result.files.filter((f) => f.path.toLowerCase().endsWith('.tex') || isImagePath(f.path)).map((f) => f.path)),
      () => setArticles([]))
  }, [filesChanged])
  useEffect(() => { api.styles().then((result) => { setSaved(result.styles); setChoice(result.choice) }, () => undefined) }, [])
  const applyLevel = (next: EffortLevel) => {
    if (!d) return
    const limits = effortLimits('style', d, next)
    setTokens(limits.tokens)
    setMinutes(Math.max(1, Math.round(limits.seconds / 60)))
    setInputTokens(limits.input_tokens!)
  }
  useEffect(() => {
    if (d) applyLevel(level)
    // Only when the launch defaults arrive.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [d])
  const preset = d ? effortLimits('style', d, level) : null
  const custom = !!preset && (tokens !== preset.tokens || minutes !== Math.max(1, Math.round(preset.seconds / 60)) || inputTokens !== preset.input_tokens)
  const uploading = useDropTarget('Pin .tex articles or screenshots as style examples', (paths) => {
    setSelected((items) => [...new Set([...items, ...paths.filter((path) => path.toLowerCase().endsWith('.tex') || isImagePath(path))])])
  })

  const running = capacityFull(snapshot, status)
  const validName = NAME.test(name.trim())
  const replaces = saved.some((item) => item.name === name.trim())
  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (uploading) return
    setBusy(true)
    setError('')
    setQueuedNote('')
    try {
      const result = await api.startStyle({ name: name.trim(), goal: goal.trim(), source_files: selected, template_files: templateFiles,
        tokens, seconds: minutes * 60, input_tokens: inputTokens, queue: true })
      if (result.task.state === 'queued') {
        setQueuedNote(queuedMessage(result.task.title))
        setSelected([])
      } else if (result.id) go('style', result.id)
      else setError(result.task.error || 'The style job did not start.')
    } catch (reason) {
      setError((reason as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="sheet paper">
      <form className="sheet-inner compose" onSubmit={submit} noValidate>
        <div className="entry">
          <span className="in-margin margin-glyph" aria-hidden="true">≅</span>
          <div>
            <h1 className="page-title">Learn your writing style</h1>
            <p className="lede">
              Give .tex articles or screenshots of your writing. Screenshots provide visible wording and layout; exact macros can only be learned from .tex sources. The harness sorts the articles
              by domain (a numerics paper is not written like a geometry paper), and describes for each domain what your
              papers set out to do, how you structure them and how you explain. Quotations are kept only when they occur
              word for word in your articles. The result is a small file a write-up can follow.
            </p>
          </div>
        </div>

        <div className="entry">
          <span className="in-margin" />
          <div className="field">
            <span className="field-label">Your articles</span>
            {articles === null ? <Loading>Looking for articles and screenshots…</Loading> : articles.length === 0 ? (
              <div className="style-ask" role="status">
                <p><strong>Add your articles or screenshots.</strong> To learn your style, the harness needs examples of your writing,
                  ideally two or more for each kind of paper you write.</p>
                <p className="field-hint">Drop .tex files or images anywhere on this page, paste an image, or open the folder that holds them from the
                  folder name at the bottom of the sidebar. Files with \input parts: drop the parts too.</p>
              </div>
            ) : (
              <>
                <FilePicker selected={selected} onChange={setSelected} allowImages accept={['.tex', ...IMAGE_SUFFIXES]} label="Choose .tex articles or screenshots" />
                <span className="field-hint">
                  {articles.length} article source{articles.length === 1 ? '' : 's'} here. Choose main .tex files or screenshots;
                  two or more per domain give a better style. At most 20 files, including up to 8 images.
                </span>
              </>
            )}
          </div>
        </div>

        <div className="entry">
          <span className="in-margin" />
          <div className="field">
            <span className="field-label">LaTeX template (optional)</span>
            <FilePicker selected={templateFiles} onChange={setTemplateFiles} accept={TEMPLATE} label="Choose template files" />
            <span className="field-hint">
              A .tex whose preamble write-ups in this style should use (packages, macros, theorem environments; its title
              is replaced), and/or .sty, .cls or .bib files. One of your articles works: only its preamble is used.
              A write-up given a template of its own uses that one instead.
            </span>
          </div>
        </div>

        <div className="entry">
          <span className="in-margin" />
          <label className="field">
            <span className="field-label">Name</span>
            <input type="text" value={name} onChange={(event) => setName(event.target.value)} placeholder="My papers" maxLength={60} />
            <span className="field-hint">
              {name && !validName ? 'Use letters, digits, spaces, dots and dashes.'
                : replaces ? 'A saved style has this name: it will be replaced.'
                : 'Saved for every folder you open in this interface.'}
            </span>
          </label>
        </div>

        <div className="entry">
          <span className="in-margin" />
          <label className="field">
            <span className="field-label">About these articles (optional)</span>
            <textarea className="goal-input" rows={2} value={goal} onChange={(event) => setGoal(event.target.value)} />
            <span className="field-hint">Helps the sorting by domain; never copied into the style.</span>
          </label>
        </div>

        <div className="entry">
          <span className="in-margin" />
          <div>
            {d && <EffortSlider kind="style" defaults={d} level={level} onLevel={(next) => { setLevel(next); applyLevel(next) }} custom={custom} />}
            <Collapse summary="Advanced limits">
              <div className="field-grid">
                <OptionalNumberField label="Total output token limit" value={tokens} onChange={setTokens} hint="Optional. Leave blank for no cumulative limit." />
                <NumberField label="Time" value={minutes} onChange={setMinutes} min={1} suffix="min" />
                <OptionalNumberField label="Total input token limit" value={inputTokens} onChange={setInputTokens} step={10000} hint="Each article is read once, then each domain is merged." />
              </div>
            </Collapse>
          </div>
        </div>

        <div className="entry">
          <span className="in-margin" />
          <div className="form-actions">
            <ErrorNote>{error}</ErrorNote>
            {queuedNote && <p className="queued-note" role="status">{queuedNote}</p>}
            {running && <BusyNote queue />}
            <button type="submit" className="btn btn-primary btn-large" disabled={busy || !!uploading || !selected.length || !validName}>
              {uploading ? 'Uploading…' : busy ? 'Starting…' : running ? 'Add to the queue' : 'Learn the style'}
            </button>
            {!selected.length && articles !== null && articles.length > 0 && <p className="field-hint">Choose at least one article.</p>}
            {selected.length > 0 && !validName && <p className="field-hint">Name the style.</p>}
          </div>
        </div>

        {saved.length > 0 && (
          <div className="entry">
            <span className="in-margin" />
            <WriteupStyle styles={saved} choice={choice} onChange={setChoice} />
          </div>
        )}

        {saved.length > 0 && (
          <div className="entry">
            <span className="in-margin" />
            <div className="field">
              <span className="field-label">Saved styles</span>
              <ul className="style-saved">
                {saved.map((profile) => (
                  <li key={profile.name}>
                    <Collapse summary={<><strong>{profile.name}</strong> <span className="muted small">
                      {profile.domains.map((item) => item.name).join(', ')} · {profile.articles.length} article{profile.articles.length === 1 ? '' : 's'} · {profile.macros.length} macros · {ago(profile.updated_at)}</span></>}>
                      <StyleTemplate profile={profile} onSaved={(next) => setSaved((items) => items.map((item) => item.name === next.name ? next : item))} />
                      <Profile profile={profile} />
                    </Collapse>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        )}
      </form>
    </div>
  )
}

const choiceValue = (choice: StyleChoice) =>
  choice.mode === 'style' ? `style:${choice.style}` : choice.mode === 'domain' ? `domain:${choice.style}\u0000${choice.domain}` : choice.mode

function parseChoice(value: string): StyleChoice {
  if (value.startsWith('style:')) return { mode: 'style', style: value.slice(6) }
  if (value.startsWith('domain:')) {
    const [style, domain] = value.slice(7).split('\u0000')
    return { mode: 'domain', style, domain }
  }
  return { mode: value as 'auto' | 'none' }
}

/** Which saved style @writeup, @summarize and the Write-up form use; saved as soon as it changes. */
function WriteupStyle({ styles, choice, onChange }: { styles: StyleProfile[]; choice: StyleChoice; onChange: (choice: StyleChoice) => void }) {
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const choose = async (value: string) => {
    setBusy(true)
    setError('')
    try {
      onChange((await api.setStyleChoice(parseChoice(value))).choice)
    } catch (reason) {
      setError((reason as Error).message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <label className="field">
      <span className="field-label">Style used by write-ups</span>
      <select value={choiceValue(choice)} disabled={busy} onChange={(event) => choose(event.target.value)}>
        <option value="auto">Choose automatically: the saved style that fits the material, or none</option>
        <option value="none">None: the usual style of the material's field</option>
        {styles.map((profile) => (
          <optgroup key={profile.name} label={profile.name}>
            <option value={`style:${profile.name}`}>{profile.name}{profile.domains.length > 1 ? ': the domain that fits best' : ''}</option>
            {profile.domains.length > 1 && profile.domains.map((domain) => (
              <option key={domain.name} value={`domain:${profile.name}\u0000${domain.name}`}>{profile.name}: {domain.name}</option>
            ))}
          </optgroup>
        ))}
      </select>
      <span className="field-hint">Used by @writeup, @summarize and the Write-up form. Saved for every folder you open in this interface.</span>
      <ErrorNote>{error}</ErrorNote>
    </label>
  )
}

/** The LaTeX template of a saved style: write-ups in the style build on it unless given their own. */
function StyleTemplate({ profile, onSaved }: { profile: StyleProfile; onSaved: (profile: StyleProfile) => void }) {
  const [files, setFiles] = useState<string[]>([])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const current = profile.template || []
  const save = async (paths: string[]) => {
    setBusy(true)
    setError('')
    try {
      onSaved((await api.setStyleTemplate(profile.name, paths)).style)
      setFiles([])
    } catch (reason) {
      setError((reason as Error).message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <section className="style-template">
      <p className="detail-label">LaTeX template</p>
      <p className="small">{current.length
        ? <>Write-ups in this style build on {current.map((file, index) => <span key={file.name}>{index ? ', ' : ''}<code>{file.name}</code></span>)} when you give them no template.</>
        : 'None: write-ups in this style use a standard amsart preamble unless you give them a template.'}</p>
      <FilePicker selected={files} onChange={setFiles} accept={TEMPLATE} label={current.length ? 'Choose other template files' : 'Choose template files'} />
      <div className="style-template-actions">
        {files.length > 0 && <button type="button" className="btn btn-small btn-primary" disabled={busy} onClick={() => save(files)}>Use as this style's template</button>}
        {current.length > 0 && <button type="button" className="btn btn-small btn-quiet" disabled={busy} onClick={() => save([])}>Remove the template</button>}
      </div>
      <ErrorNote>{error}</ErrorNote>
    </section>
  )
}

/** A saved style as a write-up will read it: per domain, then the macros. */
export function Profile({ profile }: { profile: StyleProfile }) {
  return (
    <div className="style-profile">
      {profile.domains.map((domain) => (
        <section key={domain.name} className="style-domain">
          <h3>{domain.name}</h3>
          {domain.description && <p className="muted small">{domain.description}</p>}
          <p className="muted small">From {domain.articles.join(', ')}.</p>
          {domain.aim && <><p className="detail-label">Aim</p><p>{domain.aim}</p></>}
          {domain.structure.length > 0 && <><p className="detail-label">Typical structure</p><ol>{domain.structure.map((item, i) => <li key={i}>{item}</li>)}</ol></>}
          {domain.habits.length > 0 && <><p className="detail-label">How you explain</p><ul>{domain.habits.map((item, i) => <li key={i}>{item}</li>)}</ul></>}
          {domain.phrases.length > 0 && <><p className="detail-label">Your phrasings</p><ul className="style-phrases">{domain.phrases.map((item, i) => <li key={i}>“{item}”</li>)}</ul></>}
        </section>
      ))}
      {profile.template && profile.template.length > 0 && <p className="muted small">LaTeX template: {profile.template.map((file) => file.name).join(', ')}.</p>}
      <Collapse summary={`${profile.macros.length} macros you use${profile.environments.length ? `, ${profile.environments.length} theorem environments` : ''}`}>
        <table className="style-macros">
          <tbody>
            {profile.macros.map((macro) => (
              <tr key={macro.name}>
                <td><code>{macro.definition}</code></td>
                <td className="muted">{macro.uses}×</td>
                <td className="muted">{macro.articles.length} article{macro.articles.length === 1 ? '' : 's'}</td>
              </tr>
            ))}
            {profile.environments.map((env) => (
              <tr key={'env-' + env.name}><td><code>{env.definition}</code></td><td className="muted">{env.uses}×</td><td /></tr>
            ))}
          </tbody>
        </table>
      </Collapse>
    </div>
  )
}

export function StyleView({ id, tab }: { id: string; tab: string | null }) {
  const tick = useTick(id)
  const app = useApp()
  const { data, error } = useLoad(() => api.research(id), [id, tick])
  const [artifact, setArtifact] = useState<string | null>(null)
  const active = tab || 'style'
  const jobClass = useJobClass()
  const task = taskForJob(app.snapshot, 'research', id)?.task
  const ours = !!task && task.kind === 'research' && task.target === id && ['starting', 'running', 'pausing'].includes(task.state)
  if (!data) {
    return <div className="sheet paper"><div className="sheet-inner">{error ? <ErrorNote>{error}</ErrorNote> : <Loading />}</div></div>
  }
  const aside = (
    <div className="ledger">
      <section className="aside-section">
        <h3 className="aside-title">Checks</h3>
        {data.warnings.length ? <ul className="issue-list">{data.warnings.map((w) => <li key={w}>{w}</li>)}</ul>
          : <p className="muted small">No warnings.</p>}
      </section>
      <section className="aside-section">
        <h3 className="aside-title">Usage</h3>
        <UsageMeters budget={data.budget} />
      </section>
    </div>
  )
  return (
    <div className={jobClass}>
      <div className="job-main sheet paper">
        <div className="sheet-inner">
          <Header data={data} ours={ours} pausing={task?.state === 'pausing'} />
          <Tabs active={active} onChange={(next) => go('style', id, next === 'style' ? null : next)} tabs={[
            { id: 'style', label: 'Style' },
            { id: 'articles', label: 'Articles', badge: data.articles?.length || undefined },
            { id: 'checks', label: 'Checks', badge: data.warnings.length || undefined, narrowOnly: true },
            { id: 'files', label: 'Files', badge: data.artifacts.length || undefined },
          ]} />
          {active === 'style' && (
            <div className="document">
              {data.running && data.live && <LiveStyle id={data.id} role={data.live.role} file={data.live.file} ours={ours} />}
              {data.profile ? (
                <>
                  <p className="caveat">A model description of how you write. Read it and correct what does not sound like you:
                    the saved file is <code>{data.saved_to}</code>.</p>
                  <Profile profile={data.profile} />
                </>
              ) : !data.running && <Markdown>{data.report}</Markdown>}
            </div>
          )}
          {active === 'articles' && <Articles data={data} />}
          {active === 'checks' && <div className="ledger-tab">{aside}</div>}
          {active === 'files' && <Files names={data.artifacts} onOpen={setArtifact} />}
        </div>
      </div>
      <JobAside label="Checks">{aside}</JobAside>
      {artifact && <ArtifactViewer job="research" id={id} name={artifact} onClose={() => setArtifact(null)} />}
    </div>
  )
}

function Header({ data, ours, pausing }: { data: ResearchDetail; ours: boolean; pausing: boolean }) {
  const app = useApp()
  const look = researchLook(data.status, data.running)
  const [error, setError] = useState('')
  const busy = capacityFull(app.snapshot, app.status)
  const ownTask = taskForJob(app.snapshot, 'research', data.id)?.task
  const queued = app.snapshot.queue.some((item) => item.target === data.id)
  const resumable = data.progress?.resumable ?? researchResumable(data.status)
  const act = (action: Promise<unknown>) => { setError(''); action.catch((reason: Error) => setError(reason.message)) }
  const current = STEPS.indexOf(data.phase as typeof STEPS[number])
  return (
    <header className="job-head">
      <div className="job-status">
        <Square variant={look.variant} size={18} label={look.label} />
        <span className="job-status-label">{look.label}</span>
        <span className="job-stop">Writing style “{data.style_name}”</span>
      </div>
      <div className="job-goal"><Markdown>{data.goal}</Markdown></div>
      <ol className="phases phases-wide" aria-label="Workflow">
        {STEPS.map((step, index) => {
          const variant: Variant = index < current || data.phase === 'done' ? 'complete' : index === current ? (data.running ? 'running' : 'paused') : 'ready'
          return <li key={step} className={'phase phase-' + variant}><Square variant={variant} size={10} />{STEP_NAMES[step]}</li>
        })}
      </ol>
      <WorkProgressNote progress={data.progress} status={data.status} running={data.running} />
      <div className="job-facts">
        {data.sources.map((source) => <span key={source.id} className="fact-file">{source.id} {source.path}</span>)}
        <span>{String(data.settings.model)}</span>
        <span>Started {ago(data.created_at)}</span>
      </div>
      <div className="job-actions">
        {ours ? (
          <button type="button" className="btn" disabled={pausing} onClick={() => act(api.pause(ownTask!.id))}><PauseIcon size={16} /> {pausing ? 'Pausing…' : 'Pause'}</button>
        ) : resumable ? (
          <button type="button" className="btn btn-primary" disabled={queued} onClick={() => act(api.resumeResearch(data.id, true))}>
            <PlayIcon size={16} /> {queued ? 'Waiting in the queue' : busy ? 'Queue resume' : 'Resume saved work'}
          </button>
        ) : null}
      </div>
      {!ours && busy && resumable && !queued && <BusyNote queue />}
      <ErrorNote>{error}</ErrorNote>
    </header>
  )
}

function LiveStyle({ id, role, file, ours }: { id: string; role: string; file: string; ours: boolean }) {
  const stream = useLiveStream('research', id, file)
  const app = useApp()
  return (
    <div className="live" id="live">
      <p className="live-head"><Square variant="running" size={14} /> {ROLES[role]?.active || role}</p>
      {ours && <ActivityLog items={taskForJob(app.snapshot, 'research', id)?.activity || []} />}
      <StreamBody stream={stream} role={role} live />
    </div>
  )
}

function Articles({ data }: { data: ResearchDetail }) {
  if (!data.articles?.length) return <p className="empty-note">The articles are read first.</p>
  const domainOf = new Map((data.domains || []).flatMap((domain) => domain.articles.map((id) => [id, domain.name] as const)))
  return (
    <ol className="outline-list">
      {data.articles.map((article) => {
        const seen = data.observations?.[article.id]
        return (
          <li key={article.id}>
            <strong>{article.title || article.path}</strong>{domainOf.has(article.id) && <span className="muted"> · {domainOf.get(article.id)}</span>}
            <p className="muted small">{article.path}, {article.lines} lines, {article.headings.filter((h) => !h.startsWith(' ')).length} sections.
              {article.inputs.length > 0 && ` Includes ${article.inputs.join(', ')}, which was not read.`}</p>
            {seen ? (
              <Collapse summary="What the model noticed">
                {seen.aim && <p>{seen.aim}</p>}
                {seen.structure.length > 0 && <ol>{seen.structure.map((item, i) => <li key={i}>{item}</li>)}</ol>}
                {seen.habits.length > 0 && <ul>{seen.habits.map((item, i) => <li key={i}>{item}</li>)}</ul>}
              </Collapse>
            ) : <p className="muted small">Not read yet.</p>}
          </li>
        )
      })}
    </ol>
  )
}
