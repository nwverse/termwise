---
name: relingo
description: Look up English words, list the hard words from recent replies, or mark words as known, using the local rlc tool (Relingo levels and the user's Relingo wordbook). Use when the user asks about 生词, word meanings, vocabulary from replies, or wants to mark a word as known / starred.
---

# Relingo 生词

The user runs `rlc` (at `~/.local/bin/rlc`) to see hard English words with Chinese meanings. A Stop hook of this plugin already shows the hard words of each reply; this skill answers follow-up requests.

Run these commands with the shell tool and show the output to the user as is (it is already Chinese):

- Words from the last replies: `~/.local/bin/rlc history -n 3 --color never`
- Look up words: `~/.local/bin/rlc lookup WORD [WORD...] --color never`
- Hard words in some text: `printf '%s' "TEXT" | ~/.local/bin/rlc agent --name codex`
- Mark as known (hidden from now on): `~/.local/bin/rlc known WORD...`
- Add to the starred list (always shown): `~/.local/bin/rlc star WORD...`

Notes:
- Do not run `rlc auth ...`; logging in is something the user does themselves.
- If the command reports that Relingo is unreachable, it has already fallen back to the local dictionary; say so briefly.
- Keep the answer short: the word list, plus one line if something failed.
