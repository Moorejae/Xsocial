# Read this before you touch anything

This file exists so an agent cannot start work without the project's own memory. It is loaded automatically;
`AGENTS.md`, `HANDOFF_LOG.md` and `skills/` are not, so open them now.

## Mandatory, in this order, before any edit or command

1. **`AGENTS.md`** — top to bottom. §1 is one hard prohibition (never touch `178.18.245.136`), §4 names the
   skills and when to load them, §8 is the resilience rule every unattended thing must satisfy.
2. **`HANDOFF_LOG.md`** — the **top entry first**. It is newest-first on purpose. Read until you reach work you
   already understand. It records what was measured, what failed, and which dead ends must not be retried.
3. **`skills/`** — list the folder, then open every skill whose `description` matches the task. The description
   says *when* to use it, so decide from that sentence rather than from the title.
   - Project skills: `C:\Users\hp\Downloads\X\skills\<kebab-name>\SKILL.md`
   - Shared library (larger, machine-wide): `C:\Users\hp\Downloads\REZMO-FRONTEND-DESIGN-SPEC\.github\skills\`

## Why this is compulsory rather than advisory

Every one of those files was written because something cost real time — a 403 that threw away two valid
refresh tokens, seven scheduled passes that never ran, a key file that nearly reached a public repository, a
voice rejected three times before the cause (pace, not timbre) was found. Skipping the reading does not save
time; it spends it again.

## Two habits that are not optional

- **Write a handoff entry for every change**, in the shape AGENTS.md §6 gives, with evidence — the command and
  its output. "Looks right" is not evidence. Name the **skills you actually read** in that entry.
- **Write a skill for everything you learn** that a future agent would otherwise rediscover. Project-specific
  knowledge goes to `skills/<kebab-name>/SKILL.md`; extend an existing skill rather than adding a near-duplicate.

## Never

- Never print, echo, or commit a credential value. Name the setting; report *shape* (`set/unset`, length).
  `node C:\Users\hp\Downloads\REZMO-FRONTEND-DESIGN-SPEC\scripts\env-shape.mjs <file>` does this safely.
- Never `git add -A` in `C:\Users\hp\forchi` — it is full of untracked scratch files. Add named paths only.
- Never touch `178.18.245.136`. Read AGENTS.md §1 before you believe that is an exaggeration.
