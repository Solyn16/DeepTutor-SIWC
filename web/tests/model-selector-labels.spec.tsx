import { fireEvent, render, screen } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import ModelSelector from '@/components/chat/home/ModelSelector'
import type { LLMOption } from '@/lib/llm-options'

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }))
vi.mock('@/hooks/use-linger-expand', () => ({
  useLingerExpand: () => ({ expanded: true, linger: vi.fn(), triggerProps: {} }),
}))

function option(overrides: Partial<LLMOption> = {}): LLMOption {
  return {
    profile_id: 'chatgpt-account-one', model_id: 'model-sol-one',
    profile_name: 'ChatGPT (SIWC)', model_name: 'GPT-6.1-Sol',
    model: 'openai_chatgpt/long-account-registration-one/gpt-6.1-sol',
    provider: 'openai_chatgpt', provider_label: 'ChatGPT (SIWC)',
    is_active_default: true, ...overrides,
  }
}

function picker(options: LLMOption[]) {
  const change = vi.fn()
  render(<ModelSelector options={options} activeDefault={options[0]} value={null}
    loading={false} error={false} pinned onChange={change} />)
  const trigger = screen.getByRole('button', { name: 'Select model' })
  return { change, trigger }
}

it('shows SIWC model names in the trigger and menu instead of internal account routes', () => {
  const options = [option(), option({ model_id: 'astra-one', model_name: 'GPT-6-Astra',
    model: 'openai_chatgpt/long-account-registration-one/gpt-6-astra', is_active_default: false })]
  const { trigger, change } = picker(options)
  expect(trigger).toHaveTextContent('GPT-6.1-Sol')
  expect(trigger).not.toHaveTextContent('openai_chatgpt/')
  fireEvent.click(trigger)
  const row = screen.getByRole('button', { name: 'GPT-6-Astra | ChatGPT' })
  expect(row).toHaveTextContent('GPT-6-Astra')
  expect(document.body).not.toHaveTextContent('long-account-registration-one')
  fireEvent.click(row)
  expect(change).toHaveBeenCalledWith({ profile_id: options[1].profile_id, model_id: 'astra-one' })
})

it('distinguishes the same model on two accounts without changing its selection IDs', () => {
  const first = option()
  const second = option({ profile_id: 'chatgpt-account-two', model_id: 'model-sol-two',
    model: 'openai_chatgpt/long-account-registration-two/gpt-6.1-sol', is_active_default: false })
  const { trigger, change } = picker([first, second])
  fireEvent.click(trigger)
  expect(screen.getByRole('button', { name: 'GPT-6.1-Sol | ChatGPT 1' })).toBeVisible()
  fireEvent.click(screen.getByRole('button', { name: 'GPT-6.1-Sol | ChatGPT 2' }))
  expect(change).toHaveBeenCalledWith({ profile_id: second.profile_id, model_id: second.model_id })
})

it.each(['', 'openai_chatgpt/long-account-registration-one/gpt-6.1-sol'])(
  'falls back to the model slug when the SIWC display name is %s', model_name => {
    const { trigger } = picker([option({ model_name })])
    expect(trigger).toHaveTextContent('gpt-6.1-sol')
    expect(trigger).not.toHaveTextContent('openai_chatgpt/')
    expect(trigger).not.toHaveTextContent('long-account-registration-one')
  },
)

it('keeps a user-named ChatGPT profile as the secondary label', () => {
  const { trigger } = picker([option({ profile_name: 'Work account' })])
  fireEvent.click(trigger)
  expect(screen.getByRole('button', { name: 'GPT-6.1-Sol | Work account' })).toHaveTextContent('Work account')
})

it('retains the original model-ID presentation for other providers', () => {
  const { trigger } = picker([option({ provider: 'openrouter', model: 'vendor/model-slug',
    model_name: 'My nickname', profile_name: 'Router account' })])
  expect(trigger).toHaveTextContent('vendor/model-slug')
  fireEvent.click(trigger)
  expect(screen.getByRole('button', { name: 'My nickname | Router account' })).toHaveTextContent('vendor/model-slug')
})
