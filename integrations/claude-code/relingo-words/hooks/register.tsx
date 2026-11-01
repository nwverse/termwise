import { atom, read, update } from 'claude-code'
import type { Register } from 'claude-code'

import type { Turn, Word } from '../types'

// Each Claude reply goes through `rlc agent` (fenced code stripped, words
// judged by Relingo with the local dictionary as fallback); the result is kept
// per turn and drawn in a pane and a one-line band above the prompt.

const PANE = 'relingo-words'
const TITLE = '生词'
const KEEP_TURNS = 20

// Bump the tag when Turn's fields change: older stored values then read as [].
const turns = atom({ plugin: 'relingo-words', key: 'turns' } as const, [], {
  shape: 'turns-1',
})
const known = atom({ plugin: 'relingo-words', key: 'known' } as const, [])
const isBandHidden = atom(
  { plugin: 'relingo-words', key: 'isBandHidden' } as const,
  false,
)

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

export const register: Register = (on, options) => {
  const rlc = String(options.rlcPath || 'rlc')
  const autoOpen = options.autoOpenPane !== false
  // /usr/bin/env resolves rlc on the session's PATH (no shell involved).
  const argv = (...args: string[]) => ['/usr/bin/env', rlc, ...args]

  on('session.start', async ($, e, next) => {
    await $.command.register({
      name: 'relingo',
      description: '生词 pane：/relingo [open|close|band|clear]',
    })
    if (autoOpen) void $.ui.open({ id: PANE, title: TITLE })

    return next(e)
  })

  on('command.run', { command: 'relingo' }, async ($, e) => {
    const arg = e.args.trim()
    if (arg === 'close') {
      await $.ui.close({ id: PANE })
      return { text: '生词 pane 已关闭。' }
    }
    if (arg === 'band') {
      const hidden = await update($, isBandHidden, v => !v)
      return { text: hidden ? '已隐藏提示栏上方的生词行。' : '已显示提示栏上方的生词行。' }
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
        const out = await $.process.run(
          argv('agent', '--name', 'claude-code', '--format', 'json'),
          { stdin: e.answer, timeoutMs: 30_000 },
        )
        if (out.exitCode !== 0) throw new Error(out.stderr.trim() || `rlc exit ${out.exitCode}`)
        const words: Word[] = JSON.parse(out.stdout).words ?? []
        turn = { id, at: await $.clock.now(), words }
      } catch (err) {
        turn = { id, at: await $.clock.now(), words: [], error: String(err).slice(0, 300) }
      }
      await update($, turns, list => [...list.filter(t => t.id !== id), turn].slice(-KEEP_TURNS))
    })()

    return result
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
        <Button key="hide-band" label="×" onPress={() => update($, isBandHidden, () => true)} />
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
          <Text dimColor>/relingo band 切换提示栏上方的一行摘要。</Text>
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
              {turn.error && <Text color="red" wrap="truncate-end">rlc：{turn.error}</Text>}
              {words.map(w => (
                <Box key={`${turn.id}-${w.lemma}`}>
                  <Button
                    key={`known-${turn.id}-${w.lemma}`}
                    label="✓"
                    onPress={async () => {
                      await $.process.run(argv('known', w.lemma))
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
        <Text dimColor>✓ = 标为已掌握（rlc 本地词库）</Text>
      </Box>
    )
  })
}
