import { expect, mock, test } from 'claude-code/testing'

const WORDS = {
  words: [
    { word: 'ephemeral', lemma: 'ephemeral', phonetic: 'ɪˈfemərəl', translation: 'adj. 短命，短暂', starred: false },
    { word: 'latency', lemma: 'latency', phonetic: '', translation: 'n. 潜伏期，延迟', starred: true },
  ],
}

const RAN = { exitCode: 0, stdout: '', stderr: '', isStdoutTruncated: false, isStderrTruncated: false }

const PANE = {
  plugin: 'relingo-words',
  component: 'Pane',
  requestId: 'relingo-words',
  props: {
    title: '生词',
    isFocused: false,
    bodyColumns: 50,
    placement: 'dock',
    scroll: { offset: 0, bodyRows: 20 },
    view: {},
  },
} as const

const BAND = {
  plugin: 'relingo-words',
  component: 'AbovePrompt',
  props: {
    hasSurvey: false,
    isWorking: false,
    maxRows: 3,
    bodyColumns: 80,
    scroll: { offset: 0, bodyRows: 3 },
    view: {},
  },
} as const

test('a reply runs rlc and its words show in the pane and the band', async ($, on) => {
  const clock = mock.clock(on)
  mock.env(on, { HOME: '/Users/me', PATH: '/usr/bin:/bin' })
  const calls: string[][] = []
  const paths: (string | undefined)[] = []
  on('process.run', (_, e) => {
    calls.push([...e.argv])
    paths.push(e.init?.env?.PATH)
    return { value: { ...RAN, stdout: JSON.stringify(WORDS) } }
  })
  on('turn.complete', (_, e) => ({ text: e.answer }))

  await $.turn.complete({
    answer: 'The cache is ephemeral; latency matters.',
    reason: 'answer',
    turnId: 't1',
    durationMs: 10,
    isAborted: false,
  })
  // The rlc call runs after the hook returns; let it settle.
  await clock.advance(10)

  expect(calls[0]).toEqual(['/usr/bin/env', 'rlc', 'agent', '--name', 'claude-code', '--format', 'json'])
  expect(paths[0]).toBe('/Users/me/.local/bin:/opt/homebrew/bin:/usr/bin:/bin')

  for (const surface of ['terminal', 'desktop'] as const) {
    const pane = await $.ui.mount({ ...PANE, surface })
    expect((await pane.find({ type: 'Text', text: /ephemeral/ }))?.text).toContain('ephemeral')
    expect((await pane.find({ type: 'Text', text: /延迟/ }))?.text).toContain('延迟')
    await pane.unmount()

    const band = await $.ui.mount({ ...BAND, surface })
    expect((await band.find({ type: 'Text', text: /ephemeral/ }))?.text).toContain('★latency')
    await band.unmount()
  }
})

test('subagent turns and empty answers are ignored', async ($, on) => {
  const clock = mock.clock(on)
  mock.env(on, { HOME: '/Users/me', PATH: '/usr/bin' })
  let ran = 0
  on('process.run', () => {
    ran += 1
    return { value: { ...RAN, stdout: '{"words":[]}' } }
  })
  on('turn.complete', (_, e) => ({ text: e.answer }))

  await $.turn.complete({ answer: 'ephemeral', reason: 'answer', turnId: 'a', durationMs: 1, isAborted: false, agentId: 'sub' })
  await $.turn.complete({ answer: '  ', reason: 'answer', turnId: 'b', durationMs: 1, isAborted: false })
  await $.turn.complete({ answer: 'ephemeral', reason: 'aborted', turnId: 'c', durationMs: 1, isAborted: true })
  await clock.advance(50)

  expect(ran).toBe(0)
})
