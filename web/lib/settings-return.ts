import { normalizeInternalReturnPath } from '@/shared/auth/return-url'

export const SETTINGS_RETURN_KEY = 'deeptutor:settings-return'

// Application page namespaces, excluding settings, authentication and API routes.
// Unknown paths (including third-party bridge callbacks) must not become returns.
const APP_PAGE_ROOTS = new Set([
  'admin',
  'agents',
  'avatar-preview',
  'calendar',
  'chat',
  'co-writer',
  'handoff',
  'kanban',
  'knowledge-bases',
  'learning',
  'memory',
  'notebooks',
  'partners',
  'personalization',
  'profile',
  'session-handoff',
  'space',
  'whisper',
])

/** Keep the application page and its details, staying in the tab's current workspace. */
export function settingsReturnPath(raw: string | null | undefined): string | null {
  const safe = normalizeInternalReturnPath(raw, '')
  if (!safe) return null
  const url = new URL(safe, 'https://deeptutor.invalid')
  if (url.pathname !== '/' && !APP_PAGE_ROOTS.has(url.pathname.split('/')[1])) return null
  url.searchParams.delete('dt_workspace')
  url.searchParams.delete('workspace')
  return `${url.pathname}${url.search}${url.hash}`
}
