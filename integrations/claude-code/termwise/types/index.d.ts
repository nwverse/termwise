// What `tw agent --format json` prints per word.
export type Word = {
  word: string
  lemma: string
  phonetic: string
  translation: string
  starred: boolean
}

// One Claude reply's hard words.
export type Turn = { id: string; at: number; words: Word[]; error?: string }

declare module 'claude-code' {
  interface PluginState {
    'termwise': {
      turns: Shaped<Turn[]>
      // Words marked known from the pane this session (hidden from then on).
      known: string[]
      isBandHidden: boolean
      // `/termwise inline` turns off the （释义） drawn inside replies.
      isInlineHidden: boolean
    }
  }
}
