import { render } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import SettingsReturnTracker, { SETTINGS_RETURN_KEY } from '@/components/settings/SettingsReturnTracker'
import { browserStorage } from '@/shared/storage'

vi.mock('next/navigation', () => ({ usePathname: () => window.location.pathname }))

it('remembers application navigation while retaining query and fragment but not workspace identity', () => {
  window.history.replaceState({}, '', '/learning/books/book-1?tab=notes&dt_workspace=course&workspace=old#page-3')
  render(<SettingsReturnTracker />)
  expect(browserStorage.readRaw('session', SETTINGS_RETURN_KEY)).toBe('/learning/books/book-1?tab=notes#page-3')
})

it.each([
  '/hybridaction/zybTrackerStatisticsAction?data=%7B%7D&__callback__=test',
  '/unknown-page',
  '/learning-unrelated',
  '/api/settings',
  '/settings/general',
  '/login',
])('does not replace the last application page with %s', path => {
  window.history.replaceState({}, '', '/chat/session-1')
  const view = render(<SettingsReturnTracker />)
  window.history.replaceState({}, '', path)
  view.rerender(<SettingsReturnTracker />)
  expect(browserStorage.readRaw('session', SETTINGS_RETURN_KEY)).toBe('/chat/session-1')
})
