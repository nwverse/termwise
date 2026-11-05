import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { Turn, Word } from '../types'

// Each Claude reply goes through `tw agent` (fenced code stripped, words
// judged by Relingo with the local dictionary as fallback); the result is kept
// per turn and drawn in a pane and a one-line band above the prompt.

const PANE = 'termwise'
const TITLE = '生词'
const KEEP_TURNS = 20

// Bump the tag when Turn's fields change: older stored values then read as [].
const turns = atom({ plugin: 'termwise', key: 'turns' } as const, [], {
  shape: 'turns-1',
})
const known = atom({ plugin: 'termwise', key: 'known' } as const, [])
const isBandHidden = atom(
  { plugin: 'termwise', key: 'isBandHidden' } as const,
  false,
)
const isInlineHidden = atom(
  { plugin: 'termwise', key: 'isInlineHidden' } as const,
  false,
)

// Short gloss for inline use: the first sense after the part-of-speech or
// field tag ("adj. 细致，一丝不苟" → "细致", "[计] 幂等" → "幂等").
function gloss(translation: string): string {
  const s = translation.replace(/^\s*(\[[^\]]*\]|[a-z]+\.)\s*/i, '')
  return (s.split(/[，,；;]/)[0] ?? '').trim()
}

// Markdown spans never annotated: fenced and inline code, link targets, URLs.
const SKIP = /```[\s\S]*?(?:```|$)|`[^`\n]*`|\]\([^)]*\)|https?:\/\/\S+/g
const WORD = /[A-Za-z][A-Za-z'-]*[A-Za-z]/g

// Like the Relingo extension: "meticulous" → "meticulous（细致）", first
// occurrence of each word only, prose only.
export function annotate(text: string, dict: Map<string, string>): string {
  if (dict.size === 0) return text
  const seen = new Set<string>()
  const mark = (prose: string) =>
    prose.replace(WORD, w => {
      const key = w.toLowerCase()
      const g = dict.get(key)
      if (!g || seen.has(key)) return w
      seen.add(key)
      return `${w}（${g}）`
    })
  let out = ''
  let last = 0
  for (const m of text.matchAll(SKIP)) {
    out += mark(text.slice(last, m.index)) + m[0]
    last = (m.index ?? 0) + m[0].length
  }
  return out + mark(text.slice(last))
}

// Terminal cells a string takes: CJK and full-width characters count two.
function cells(s: string): number {
  let n = 0
  for (const ch of s) n += /[ᄀ-ᅟ⺀-꓏가-힣豈-﫿︰-﹏＀-｠￠-￦]/.test(ch) ? 2 : 1
  return n
}

function fit(parts: string[], columns: number): string {
  let line = ''
  for (const p of parts) {
    const next = line ? `${line} · ${p}` : p
    if (cells(next) > columns - 2) return line ? `${line} …` : p
    line = next
  }
  return line
}

// /usr/bin/env resolves tw on PATH (no shell involved). A session the
// desktop app starts has a short PATH, so ~/.local/bin and Homebrew are added.
async function runRlc($: EngineInterface, tw: string, args: string[], stdin?: string) {
  const home = (await $.env.get('HOME')) ?? ''
  const path = (await $.env.get('PATH')) ?? '/usr/bin:/bin'
  return $.process.run(['/usr/bin/env', tw, ...args], {
    stdin,
    timeoutMs: 30_000,
    env: { PATH: `${home}/.local/bin:/opt/homebrew/bin:${path}` },
  })
}

// `tw star` (word book, always marked) or `tw known` (ignored from then on);
// both also sync to the Relingo account when logged in. Says how it went.
async function mark($: EngineInterface, tw: string, picked: string[], kind: 'star' | 'known') {
  const out = await runRlc($, tw, [kind, ...picked])
  const said = (out.stdout.trim() || out.stderr.trim()).slice(0, 300)
  if (out.exitCode === 0 || out.exitCode === 3) { // 3: kept locally, Relingo sync failed
    const set = new Set(picked)
    if (kind === 'known') await update($, known, ks => [...ks, ...picked])
    else await update($, turns, list => list.map(t => ({
      ...t,
      words: t.words.map(w => (set.has(w.lemma.toLowerCase()) || set.has(w.word.toLowerCase()) ? { ...w, starred: true } : w)),
    })))
  }
  return said || `tw ${kind} exit ${out.exitCode}`
}

function parseWords(text: string): string[] {
  return [...new Set(text.split(/[\s,，]+/).filter(w => /^[A-Za-z][A-Za-z'-]*$/.test(w)).map(w => w.toLowerCase()))]
}

// AskUserQuestion with the latest reply's words (the dialog takes four at
// most; "Other" types any word), then mark the choices. Callers do not wait
// on it: a hook has 10s and the person may take longer.
async function askAndMark($: EngineInterface, tw: string, kind: 'star' | 'known') {
  const hide = new Set(await read($, known))
  const words = ((await read($, turns)).at(-1)?.words ?? []).filter(w => !hide.has(w.lemma))
  const options = words.filter(w => kind === 'known' || !w.starred).slice(0, 4).map(w => w.lemma)
  const question = kind === 'star'
    ? 'Add which words to your word book?'
    : 'Which words do you know? They will be ignored from now on.'
  let answer: string
  try {
    answer = await $.ui.ask(question, { options, header: kind === 'star' ? '生词本' : '已掌握', multiSelect: true })
  } catch {
    return // dismissed
  }
  const picked = parseWords(answer)
  if (picked.length > 0) $.ui.toast(await mark($, tw, picked, kind))
}

export const register: Register = (on, options) => {
  const tw = String(options.rlcPath || 'tw')
  const autoOpen = options.autoOpenPane !== false

  on('session.start', async ($, e, next) => {
    await $.command.register({
      name: 'termwise',
      description: '生词 pane：/termwise [open|close|band|inline|clear|star [词…]|known [词…]]',
    })
    if (autoOpen) void $.ui.open({ id: PANE, title: TITLE })

    return next(e)
  })

  on('command.run', { command: 'termwise' }, async ($, e) => {
    const arg = e.args.trim()
    if (arg === 'close') {
      await $.ui.close({ id: PANE })
      return { text: '生词 pane 已关闭。' }
    }
    if (arg === 'band') {
      const hidden = await update($, isBandHidden, v => !v)
      return { text: hidden ? '已隐藏提示栏上方的生词行。' : '已显示提示栏上方的生词行。' }
    }
    if (arg === 'inline') {
      const hidden = await update($, isInlineHidden, v => !v)
      return { text: hidden ? '已关闭回复里的括号释义。' : '已打开回复里的括号释义。' }
    }
    const [sub = '', ...rest] = arg.split(/\s+/)
    if (sub === 'star' || sub === 'known') {
      const picked = parseWords(rest.join(' '))
      if (picked.length > 0) return { text: await mark($, tw, picked, sub) }
      void askAndMark($, tw, sub)
      return { text: sub === 'star' ? 'Pick words for your word book in the dialog.' : 'Pick the words you know in the dialog.' }
    }
    if (arg === 'clear') {
      await update($, turns, () => [])
      return { text: '已清空本会话的生词记录。' }
    }
    const opened = await $.ui.open({ id: PANE, title: TITLE })
    return {
      text: opened.isPlaced ? '生词 pane 已打开。' : `生词 pane 暂未显示：${opened.reason}`,
    }
  })

  on('turn.complete', async ($, e, next) => {
    const result = await next(e)
    if (e.agentId || e.reason !== 'answer' || !e.answer.trim()) return result

    const id = e.turnId
    void (async () => {
      let turn: Turn
      try {
        const out = await runRlc($, tw, ['agent', '--name', 'claude-code', '--format', 'json'], e.answer)
        if (out.exitCode !== 0) throw new Error(out.stderr.trim() || `tw exit ${out.exitCode}`)
        const words: Word[] = JSON.parse(out.stdout).words ?? []
        turn = { id, at: await $.clock.now(), words }
      } catch (err) {
        turn = { id, at: await $.clock.now(), words: [], error: String(err).slice(0, 300) }
      }
      await update($, turns, list => [...list.filter(t => t.id !== id), turn].slice(-KEEP_TURNS))
    })()

    return result
  })

  // Rewrites only the drawing; the stored reply (and what the model reads) is
  // unchanged. Words come from the stored turns, so a reply is annotated once
  // its tw run lands, and the read redraws it then.
  on('ui.render', { component: 'AssistantMessage' }, async ($, e, next) => {
    if (e.props.isSummary || (await read($, isInlineHidden))) return next(e)
    const hide = new Set(await read($, known))
    const dict = new Map<string, string>()
    for (const t of await read($, turns)) {
      for (const w of t.words) {
        const g = gloss(w.translation)
        if (!g || hide.has(w.lemma)) continue
        dict.set(w.word.toLowerCase(), g)
        dict.set(w.lemma.toLowerCase(), g)
      }
    }
    const text = annotate(e.props.text, dict)
    return next(text === e.props.text ? e : { ...e, props: { ...e.props, text } })
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    if (e.props.hasSurvey || e.props.isWorking || (await read($, isBandHidden))) return next(e)
    const last = (await read($, turns)).at(-1)
    const hide = new Set(await read($, known))
    const words = (last?.words ?? []).filter(w => !hide.has(w.lemma))
    if (!last || words.length === 0) return next(e)

    const { Box, Button, Text } = $.ui.resolve(e)
    const line = fit(
      words.map(w => `${w.starred ? '★' : ''}${w.lemma} ${w.translation}`),
      Math.max(20, e.props.bodyColumns - 10),
    )
    return (
      <Box>
        <Text color="cyan">生词 </Text>
        <Text dimColor wrap="truncate-end">{line} </Text>
        <Button key="star-band" label="+" onPress={() => void askAndMark($, tw, 'star')} />
        <Button key="known-band" label="-" onPress={() => void askAndMark($, tw, 'known')} />
        <Button key="hide-band" label="x" onPress={() => update($, isBandHidden, () => true)} />
      </Box>
    )
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Button, Text } = $.ui.resolve(e)
    const list = await read($, turns)
    const hide = new Set(await read($, known))
    const shown = list.slice(-5).reverse()

    if (shown.length === 0) {
      return (
        <Box flexDirection="column">
          <Text dimColor>Claude 每次回复后，这里列出回复里的生词。</Text>
          <Text dimColor>/termwise band 切换提示栏上方的一行摘要。</Text>
        </Box>
      )
    }

    return (
      <Box flexDirection="column">
        {shown.map((turn, i) => {
          const words = turn.words.filter(w => !hide.has(w.lemma))
          return (
            <Box flexDirection="column" key={turn.id}>
              <Text dimColor>
                {i === 0 ? '最新回复' : `之前第 ${i} 条`} · {words.length} 个生词
              </Text>
              {turn.error && <Text color="red" wrap="truncate-end">tw：{turn.error}</Text>}
              {words.map(w => (
                <Box key={`${turn.id}-${w.lemma}`}>
                  <Button
                    key={`known-${turn.id}-${w.lemma}`}
                    label="✓"
                    onPress={async () => {
                      await runRlc($, tw, ['known', w.lemma])
                      await update($, known, ks => [...ks, w.lemma])
                    }}
                  />
                  <Text color={w.starred ? 'yellow' : 'cyan'}> {w.starred ? '★' : ''}{w.lemma}</Text>
                  <Text dimColor={i > 0} wrap="truncate-end"> {w.translation}</Text>
                </Box>
              ))}
              <Text> </Text>
            </Box>
          )
        })}
        <Text dimColor>✓ = 标为已掌握（tw 本地词库）</Text>
      </Box>
    )
  })
}
