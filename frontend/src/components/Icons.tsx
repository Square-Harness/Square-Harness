// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

// A small stroke icon set; everything else in the interface is typographic.
import type { ReactNode } from 'react'

type IconProps = { size?: number | string; title?: string }

function Icon({ size = 18, title, children }: IconProps & { children: ReactNode }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden={title ? undefined : true} role={title ? 'img' : undefined}>
      {title && <title>{title}</title>}
      {children}
    </svg>
  )
}

/** One mathematical question branching into two lines of inquiry. */
export const InvestigationIcon = (p: IconProps) => <Icon {...p}>
  <path d="M8 12h4V5.5h4m-4 6.5v6.5h4" />
  <rect x="2.5" y="9.5" width="5" height="5" fill="currentColor" />
  <rect x="16" y="3" width="5" height="5" /><rect x="16" y="16" width="5" height="5" />
</Icon>
export const NotebookIcon = (p: IconProps) => <Icon {...p}>
  <rect x="4" y="3" width="16" height="18" rx="1.5" /><path d="M8 3v18m3.5-13h5m-5 3.5h4" />
</Icon>
export const MenuIcon = (p: IconProps) => <Icon {...p}><path d="M4 7h16M4 12h16M4 17h16" /></Icon>
export const PlusIcon = (p: IconProps) => <Icon {...p}><path d="M12 5v14M5 12h14" /></Icon>
export const PauseIcon = (p: IconProps) => <Icon {...p}><path d="M9 6v12M15 6v12" /></Icon>
// A filled square, the notebook's end-of-proof mark: stop for good.
export const StopIcon = (p: IconProps) => <Icon {...p}><rect x="6.5" y="6.5" width="11" height="11" rx="1" fill="currentColor" /></Icon>
export const PlayIcon = (p: IconProps) => <Icon {...p}><path d="M8 5.5v13l10-6.5z" /></Icon>
export const CloseIcon = (p: IconProps) => <Icon {...p}><path d="M6 6l12 12M18 6L6 18" /></Icon>
export const TrashIcon = (p: IconProps) => <Icon {...p}><path d="M4 7h16M9 7V4h6v3M7 7l1 14h8l1-14M10 11v6M14 11v6" /></Icon>
export const ChevronIcon = (p: IconProps) => <Icon {...p}><path d="M9 6l6 6-6 6" /></Icon>
export const SendIcon = (p: IconProps) => <Icon {...p}><path d="M5 12h13M13 6l6 6-6 6" /></Icon>
export const SunIcon = (p: IconProps) => <Icon {...p}><circle cx="12" cy="12" r="4" /><path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M5.3 18.7l1.4-1.4M17.3 6.7l1.4-1.4" /></Icon>
export const MoonIcon = (p: IconProps) => <Icon {...p}><path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z" /></Icon>
export const AutoIcon = (p: IconProps) => <Icon {...p}><circle cx="12" cy="12" r="8" /><path d="M12 4v16" /><path d="M12 4a8 8 0 0 1 0 16z" fill="currentColor" /></Icon>
export const FileIcon = (p: IconProps) => <Icon {...p}><path d="M7 3h7l4 4v14H7z" /><path d="M14 3v4h4" /></Icon>
export const PinIcon = (p: IconProps) => <Icon {...p}><path d="M9 4h6l-1 6 3 3H7l3-3z" /><path d="M12 13v7" /></Icon>
export const CheckIcon = (p: IconProps) => <Icon {...p}><path d="M5 12.5l4.5 4.5L19 7" /></Icon>
/** Two overlapping sheets: copy to the clipboard. */
export const CopyIcon = (p: IconProps) => <Icon {...p}><rect x="8" y="8" width="12" height="12" rx="1.5" /><path d="M16 8V5.5A1.5 1.5 0 0 0 14.5 4h-9A1.5 1.5 0 0 0 4 5.5v9A1.5 1.5 0 0 0 5.5 16H8" /></Icon>
export const PhoneIcon = (p: IconProps) => <Icon {...p}><rect x="7" y="2.5" width="10" height="19" rx="2" /><path d="M11 18.5h2" /></Icon>
// An Erlenmeyer flask with a liquid line and a bubble: the Python Lab's mark.
export const FlaskIcon = (p: IconProps) => <Icon {...p}><path d="M8.5 3h7" /><path d="M10 3v6.2L4.6 18.4A1.7 1.7 0 0 0 6.1 21h11.8a1.7 1.7 0 0 0 1.5-2.6L14 9.2V3" /><path d="M7.3 14.5h9.4" /><circle cx="11.2" cy="17.7" r="0.9" /></Icon>
// A magnifying glass: the Literature notebook's cover.
export const SearchIcon = (p: IconProps) => <Icon {...p}><circle cx="10.5" cy="10.5" r="6" /><path d="M15 15l5.5 5.5" /></Icon>
export const BackIcon = (p: IconProps) => <Icon {...p}><path d="M15 6l-6 6 6 6" /></Icon>
// A panel with its edge: the side a panel slides to (left list, right overview).
export const PanelLeftIcon = (p: IconProps) => <Icon {...p}><rect x="3.5" y="4.5" width="17" height="15" rx="2" /><path d="M9.5 4.5v15" /><path d="M16 10l-2 2 2 2" /></Icon>
export const PanelRightIcon = (p: IconProps) => <Icon {...p}><rect x="3.5" y="4.5" width="17" height="15" rx="2" /><path d="M14.5 4.5v15" /><path d="M8 10l2 2-2 2" /></Icon>
export const FolderIcon = (p: IconProps) => <Icon {...p}><path d="M3.5 7.5v10.5a1.5 1.5 0 0 0 1.5 1.5h14a1.5 1.5 0 0 0 1.5-1.5V9.5A1.5 1.5 0 0 0 19 8h-7L10 5.5H5A1.5 1.5 0 0 0 3.5 7z" /></Icon>
// Three squares: more actions, in the notebook's own marks.
export const MoreIcon = (p: IconProps) => <Icon {...p}><rect x="4.4" y="10.9" width="2.2" height="2.2" rx="0.4" fill="currentColor" /><rect x="10.9" y="10.9" width="2.2" height="2.2" rx="0.4" fill="currentColor" /><rect x="17.4" y="10.9" width="2.2" height="2.2" rx="0.4" fill="currentColor" /></Icon>
export const PencilIcon = (p: IconProps) => <Icon {...p}><path d="M4 20l1-4.5L16.5 4a1.8 1.8 0 0 1 2.5 0l1 1a1.8 1.8 0 0 1 0 2.5L8.5 19z" /><path d="M14.5 6l3.5 3.5" /></Icon>
// Sliders with square stops, like the effort scale.
export const SlidersIcon = (p: IconProps) => <Icon {...p}><path d="M4 7h7M16 7h4M4 17h3M12 17h8" /><rect x="11" y="4.5" width="5" height="5" rx="1" /><rect x="7" y="14.5" width="5" height="5" rx="1" /></Icon>
// Stacked sheets: several pieces of work at once.
export const StackIcon = (p: IconProps) => <Icon {...p}><rect x="4" y="9" width="12" height="11" rx="1.5" /><path d="M8 9V5.5A1.5 1.5 0 0 1 9.5 4h9A1.5 1.5 0 0 1 20 5.5v8a1.5 1.5 0 0 1-1.5 1.5H16" /></Icon>
export const InfoIcon = (p: IconProps) => <Icon {...p}><rect x="4" y="4" width="16" height="16" rx="2.5" /><path d="M12 11v5.5M12 7.6v.4" /></Icon>
export const NotesIcon = (p: IconProps) => <Icon {...p}><rect x="5" y="3.5" width="14" height="17" rx="2" /><path d="M9 8.5h6M9 12h6M9 15.5h3.5" /></Icon>
export const CommentIcon = (p: IconProps) => <Icon {...p}><path d="M4.5 5.5h15a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H12l-4.5 3.5v-3.5h-3a1 1 0 0 1-1-1v-9a1 1 0 0 1 1-1z" /></Icon>
// A lightbulb: the Explain skill's mark.
/** Proof Check: the end-of-proof square, ticked. */
export const ProofCheckIcon = (p: IconProps) => <Icon {...p}><rect x="4" y="4" width="16" height="16" rx="1.5" /><path d="M8 12.5l3 3 5.2-6.5" /></Icon>
/** Typo: a letter with the proofreader's wavy underline. */
export const TypoIcon = (p: IconProps) => <Icon {...p}><path d="M6.5 15L11.5 3.5h1L17.5 15M8.6 11h6.8" /><path d="M4 20c1.3-1.6 2.7-1.6 4 0s2.7 1.6 4 0 2.7-1.6 4 0 2.7 1.6 4 0" /></Icon>
export const BulbIcon = (p: IconProps) => <Icon {...p}><path d="M9.2 17.5h5.6M10.2 20.5h3.6" /><path d="M12 3.5a6 6 0 0 0-3.9 10.6c.7.6 1.1 1.4 1.1 2.4v1h5.6v-1c0-1 .4-1.8 1.1-2.4A6 6 0 0 0 12 3.5z" /></Icon>
