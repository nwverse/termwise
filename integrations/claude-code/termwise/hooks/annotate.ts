const LINK_OR_URL = /\]\([^)]*\)|https?:\/\/\S+/g
const WORD = /[A-Za-z][A-Za-z'-]*[A-Za-z]/g

type Range = [start: number, end: number]

function mergeRanges(ranges: Range[]): Range[] {
  const sorted = [...ranges].sort((a, b) => a[0] - b[0] || a[1] - b[1])
  const merged: Range[] = []
  for (const range of sorted) {
    const last = merged.at(-1)
    if (!last || range[0] > last[1]) merged.push([...range])
    else last[1] = Math.max(last[1], range[1])
  }
  return merged
}

/** Fenced code blocks opened by at least three backticks or tildes. */
function fencedCodeRanges(text: string): Range[] {
  const lines = text.match(/[^\n]*\n|[^\n]+$/g) ?? []
  const ranges: Range[] = []
  let offset = 0
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]!
    const content = line.replace(/\n$/, '')
    const open = /^ {0,3}(`{3,}|~{3,})/.exec(content)
    if (!open || (open[1]![0] === '`' && content.slice(open[0].length).includes('`'))) {
      offset += line.length
      continue
    }
    const marker = open[1]![0]!
    const width = open[1]!.length
    const start = offset
    let end = text.length
    let next = offset + line.length
    for (let j = i + 1; j < lines.length; j++) {
      const close = /^ {0,3}(`{3,}|~{3,})[ \t]*$/.exec(lines[j]!.replace(/\n$/, ''))
      next += lines[j]!.length
      if (close && close[1]![0] === marker && close[1]!.length >= width) {
        end = next
        i = j
        break
      }
    }
    ranges.push([start, end])
    offset = end
    if (end === text.length) break
  }
  return ranges
}

function inside(ranges: Range[], at: number): boolean {
  return ranges.some(([start, end]) => at >= start && at < end)
}

/** Code spans delimited by equal-length runs of backticks, outside fenced blocks. */
function inlineCodeRanges(text: string, fenced: Range[]): Range[] {
  const ranges: Range[] = []
  for (let i = 0; i < text.length;) {
    if (text[i] !== '`' || inside(fenced, i)) {
      i++
      continue
    }
    let openEnd = i + 1
    while (text[openEnd] === '`') openEnd++
    const width = openEnd - i
    let close = -1
    for (let j = openEnd; j < text.length && text[j] !== '\n';) {
      if (text[j] !== '`') {
        j++
        continue
      }
      let runEnd = j + 1
      while (text[runEnd] === '`') runEnd++
      if (runEnd - j === width) {
        close = runEnd
        break
      }
      j = runEnd
    }
    if (close > 0) {
      ranges.push([i, close])
      i = close
    } else {
      i = openEnd
    }
  }
  return ranges
}

function skipRanges(text: string): Range[] {
  const fenced = fencedCodeRanges(text)
  const inline = inlineCodeRanges(text, fenced)
  const links = [...text.matchAll(LINK_OR_URL)].map((m): Range => [m.index!, m.index! + m[0].length])
  return mergeRanges([...fenced, ...inline, ...links])
}

/** Like the Relingo extension: first prose occurrence only; code, link targets and URLs stay untouched. */
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
  for (const [start, end] of skipRanges(text)) {
    out += mark(text.slice(last, start)) + text.slice(start, end)
    last = end
  }
  return out + mark(text.slice(last))
}
