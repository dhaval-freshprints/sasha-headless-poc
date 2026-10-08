---
name: outreach
description: Carry out the caller-selected Fresh Prints initial outreach workflow to inspect the complete deal history and relevant proof, then draft the initial message.
---

# Initial Outreach

This file owns what Sasha inspects and does during initial outreach. Reply wording belongs in the linked crafting guides.

Use this skill when explicitly selected by the caller. Use `sasha-sales` for shared browser, evidence, verification, and output rules. Read the relevant sections of [Workplace](../sasha-sales/references/workplace.md) for browser navigation. This skill owns outreach inspection and sales decisions.

## What to inspect and do

1. Open the exact deal URL supplied in the task and read its activity history alongside any supplied conversation and deal notes.
2. Identify the client's first name, proof state, and flash or rush state on the deal page.
3. If the deal page shows no proof, treat the deal as having no proof. Do not search elsewhere for one.
4. When a proof exists, inspect the relevant proof linked from the deal to determine whether it is finished or in progress. Inspect the garment and occasion only when a proof exists.
5. Before drafting, read [shared crafting](../sasha-sales/references/shared_crafting.md) and [outreach crafting](references/outreach_crafting.md). Draft from the verified deal and proof state. Do not open the quoter or change deal or proof records.

Before drafting, inspect the complete deal history. Use page text and screenshots to identify content that is collapsed, truncated, behind tabs, or not yet loaded. Scroll the activity area, open information-bearing items, and follow controls that reveal more content. After each action, read the newly revealed information. Finish when you reach the end of the history and have checked all discovered expandable content. If anything cannot be read, record that gap instead of treating it as absent. Keep this inspection read-only.

When the page indicates additional content, verify that it has loaded and read it before continuing. A successful click or expanded panel is not proof that the content was retrieved. If the expected content is missing, inspect the loading or error state, use a bounded wait for the content to appear, and retry using the current page state. If it remains unavailable, record the history as incomplete and avoid presenting potentially superseded details as confirmed.

## Questions

- Ask only for information not already provided in the conversation or verified on the deal. Do not repeat answered questions.
- Never ask for a shipping address, phone number, or size breakdown.
