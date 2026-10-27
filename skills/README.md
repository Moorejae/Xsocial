# Project skills

Skills written for **this** project live here, one folder each:

```
X\skills\<kebab-case-name>\SKILL.md
```

The shared library lives elsewhere and is the first place to look:
`C:\Users\hp\Downloads\REZMO-FRONTEND-DESIGN-SPEC\.github\skills\` — see [AGENTS.md](../AGENTS.md) §4 for which one to
load when. **Read a relevant shared skill before starting; write a project skill only for what the shared library does
not already cover.**

## The format

```markdown
---
name: kebab-case-name
description: 'What this skill contains, and when to use it. One paragraph, no line breaks. A reader
  should be able to decide from this sentence whether to load the file, so name the symptoms and the
  triggers, not just the topic.'
---

# A title a human would say out loud

One paragraph: the shape of the problem and the fact that makes it tractable.

## When to use

- The symptom that sends someone here.
- The task that needs this.

## The body

Tables, commands, quoted error text, and short code blocks. Prefer a table of "symptom -> cause ->
fix" over prose. Every claim should be something that was measured, with the command that shows it.

## Verification

The check that proves the fix worked.
```

## Rules

- **`name` must equal the folder name.** A mismatch means the skill never loads.
- **The `description` decides loading.** Say *when* to use it, using the words a person would actually type.
- **Write from measurement.** Quote real error text and real command output. A skill full of plausible guesses teaches
  the next agent the wrong thing, which is worse than no skill.
- **Record the dead ends.** "Tried X, it fails with Y, do not repeat" saves more time than the fix does.
- **No credential values, ever.** Name the setting; never paste the contents.
- **Reference the resilience rule.** If the skill helps build something that runs unattended, point at
  `build-fallbacks` in the shared library.

## Naming

Describe the capability, not the tool: `vps-bootstrap`, `certbot-renewal`, `telegram-webhook`, not `notes` or `misc`.
