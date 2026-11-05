import { expect, mock, test } from 'claude-code/testing'

const WORDS = {
  words: [
    { word: 'ephemeral', lemma: 'ephemeral', phonetic: 'ɪˈfemərəl', translation: 'adj. 短命，短暂', starred: false },
    { word: 'latency', lemma: 'latency', phonetic: '', translation: 'n. 潜伏期，延迟', starred: true },
  ],
}

const RAN = { exitCode: 0, stdout: '', stderr: '', isStdoutTruncated: false, isStderrTruncated: false }

const PANE = {
  plugin: 'termwise',
  component: 'Pane',
  requestId: 'termwise',
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
  plugin: 'termwise',
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

test('a reply runs tw and its words show in the pane and the band', async ($, on) => {
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
  // The tw call runs after the hook returns; let it settle.
  await clock.advance(10)

  expect(calls[0]).toEqual(['/usr/bin/env', 'tw', 'agent', '--name', 'claude-code', '--format', 'json'])
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

test('reply text gets inline （释义） once its words land; code is left alone', async ($, on) => {
  const clock = mock.clock(on)
  mock.env(on, { HOME: '/Users/me', PATH: '/usr/bin' })
  on('process.run', () => ({ value: { ...RAN, stdout: JSON.stringify(WORDS) } }))
  on('turn.complete', (_, e) => ({ text: e.answer }))
  on('command.run', () => ({ text: 'unhandled' }))
  const drawn: string[] = []
  on('ui.render', { component: 'AssistantMessage' }, ($$, e) => {
    drawn.push(e.props.text)
    const { Text } = $$.ui.resolve(e)
    return <Text>{e.props.text}</Text>
  })

  const text = 'Ephemeral data, `ephemeral` code; ephemeral again. Latency too.'
  const MSG = { plugin: 'termwise', component: 'AssistantMessage', props: { text, isFirstOfReply: true } } as const

  await $.turn.complete({ answer: text, reason: 'answer', turnId: 't1', durationMs: 1, isAborted: false })
  await clock.advance(10)

  for (const surface of ['terminal', 'desktop'] as const) {
    const m = await $.ui.mount({ ...MSG, surface })
    await m.unmount()
  }
  expect(drawn.at(-1)).toBe('Ephemeral（短命） data, `ephemeral` code; ephemeral again. Latency（潜伏期） too.')

  await $.command.run({ command: 'termwise', args: 'inline', origin: { kind: 'composer' }, presentation: { isFullscreen: false, columns: 80 } })
  const m = await $.ui.mount({ ...MSG, surface: 'terminal' })
  await m.unmount()
  expect(drawn.at(-1)).toBe(text)
})

test('a reply drawn before its words land is redrawn with （释义）', async ($, on) => {
  const clock = mock.clock(on)
  mock.env(on, { HOME: '/Users/me', PATH: '/usr/bin' })
  on('process.run', () => ({ value: { ...RAN, stdout: JSON.stringify(WORDS) } }))
  on('turn.complete', (_, e) => ({ text: e.answer }))
  const drawn: string[] = []
  on('ui.render', { component: 'AssistantMessage' }, ($$, e) => {
    drawn.push(e.props.text)
    const { Text } = $$.ui.resolve(e)
    return <Text>{e.props.text}</Text>
  })
  const text = 'Latency too.'
  const m = await $.ui.mount({ plugin: 'termwise', component: 'AssistantMessage', props: { text, isFirstOfReply: true }, surface: 'terminal' })
  expect(drawn.at(-1)).toBe(text)
  await $.turn.complete({ answer: text, reason: 'answer', turnId: 't1', durationMs: 1, isAborted: false })
  await clock.advance(10)
  expect(drawn.at(-1)).toBe('Latency（潜伏期） too.')
  await m.unmount()
})

test('+ on the band asks which words to star and runs tw star', async ($, on) => {
  const clock = mock.clock(on)
  mock.env(on, { HOME: '/Users/me', PATH: '/usr/bin' })
  const calls: string[][] = []
  on('process.run', (_, e) => {
    calls.push([...e.argv])
    return { value: { ...RAN, stdout: JSON.stringify(WORDS) } }
  })
  on('turn.complete', (_, e) => ({ text: e.answer }))
  const asked: unknown[] = []
  on('tool.call', { tool: 'AskUserQuestion' }, (_, e) => {
    asked.push(e.questions)
    const q = e.questions[0]!.question
    return { result: { questions: e.questions, answers: { [q]: 'ephemeral, serendipity' } } } as never
  })

  await $.turn.complete({ answer: 'The cache is ephemeral.', reason: 'answer', turnId: 't1', durationMs: 1, isAborted: false })
  await clock.advance(10)

  const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  await band.press({ key: 'star-band' })
  await clock.advance(10)
  expect(JSON.stringify(asked[0])).toContain('ephemeral')
  expect(calls.at(-1)).toEqual(['/usr/bin/env', 'tw', 'star', 'ephemeral', 'serendipity'])
  expect((await band.find({ type: 'Text', text: /ephemeral/ }))?.text).toContain('★ephemeral')
  await band.unmount()
})

test('- on the band asks which words are known, runs tw known and hides them', async ($, on) => {
  const clock = mock.clock(on)
  mock.env(on, { HOME: '/Users/me', PATH: '/usr/bin' })
  const calls: string[][] = []
  on('process.run', (_, e) => {
    calls.push([...e.argv])
    return { value: { ...RAN, stdout: JSON.stringify(WORDS) } }
  })
  on('turn.complete', (_, e) => ({ text: e.answer }))
  on('tool.call', { tool: 'AskUserQuestion' }, (_, e) => {
    const q = e.questions[0]!.question
    return { result: { questions: e.questions, answers: { [q]: 'ephemeral' } } } as never
  })

  await $.turn.complete({ answer: 'The cache is ephemeral.', reason: 'answer', turnId: 't1', durationMs: 1, isAborted: false })
  await clock.advance(10)

  const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  await band.press({ key: 'known-band' })
  await clock.advance(10)
  expect(calls.at(-1)).toEqual(['/usr/bin/env', 'tw', 'known', 'ephemeral'])
  const line = (await band.find({ type: 'Text', text: /latency/ }))?.text ?? ''
  expect(line).not.toContain('ephemeral')
  await band.unmount()
})

test('/termwise star|known take words, or ask for them in the dialog', async ($, on) => {
  const clock = mock.clock(on)
  mock.env(on, { HOME: '/Users/me', PATH: '/usr/bin' })
  const calls: string[][] = []
  on('process.run', (_, e) => {
    calls.push([...e.argv])
    const said = e.argv[2] === 'agent' ? JSON.stringify(WORDS) : `${e.argv[2]}: ok`
    return { value: { ...RAN, stdout: said } }
  })
  on('turn.complete', (_, e) => ({ text: e.answer }))
  on('command.run', () => ({ text: 'unhandled' }))
  const asked: string[] = []
  on('tool.call', { tool: 'AskUserQuestion' }, (_, e) => {
    const q = e.questions[0]!.question
    asked.push(q)
    return { result: { questions: e.questions, answers: { [q]: 'ephemeral' } } } as never
  })
  const run = (args: string) => $.command.run({
    command: 'termwise', args, origin: { kind: 'composer' }, presentation: { isFullscreen: false, columns: 80 },
  })

  await $.turn.complete({ answer: 'The cache is ephemeral.', reason: 'answer', turnId: 't1', durationMs: 1, isAborted: false })
  await clock.advance(10)

  expect((await run('known Serendipity, gist'))?.text).toBe('known: ok')
  expect(calls.at(-1)).toEqual(['/usr/bin/env', 'tw', 'known', 'serendipity', 'gist'])
  expect(asked).toEqual([])

  expect((await run('star'))?.text).toContain('dialog')
  await clock.advance(10)
  expect(asked[0]).toContain('word book')
  expect(calls.at(-1)).toEqual(['/usr/bin/env', 'tw', 'star', 'ephemeral'])
})
