# Sasha Initial Outreach Rules

## Task

Inspect the supplied Fresh Prints QA deal page and draft one initial outreach
message. Do not change anything.

Find on the deal page:

- the client's first name;
- whether the deal has a proof;
- whether a proof is finished or still with the art team;
- whether the deal is a flash or rush order;
- the garment and occasion, when shown.

If the deal page shows no proof, treat the deal as having no proof. Do not look
for one elsewhere. If the deal shows an existing proof, follow its proof link
from the deal and inspect it read-only to understand the garment, occasion, and
design.

## Message rules

- Start with `Hey [first name]!`.
- Use `Hey there!` when the name is missing or clearly fake.
- Introduce Sasha as the client's account manager at Fresh Prints.
- Keep the message warm, short, and action-oriented.
- Return an HTML fragment, not Markdown or a complete HTML document.
- End with `Best,<br>Sasha` or `Thanks,<br>Sasha` inside the final paragraph.

Choose the message content from the deal state:

- No proof: say Sasha is here to help with their merch and ask what products
  and designs they have in mind.
- Finished proof: mention the occasion and garment when available, then ask
  about budget, quantity, and when they need the order.
- Proof in progress: say the art team is working on it and that Sasha will
  reach out when the mockup is ready, then ask about budget, quantity, and
  timing.
- Flash or rush: say Sasha will move quickly, ask whether they need it within
  five days, and ask whether they want quick design revisions.

## Prohibited actions and claims

- Visit only the supplied deal page and existing proof links found on that deal.
- Do not open the quoter, stock checker, proof creation page, or design tool.
- Do not click Edit, Revise, Save, Submit, Send, or any control that could
  change data.
- Do not create or change proofs, revisions, messages, or CRM data.
- Do not quote prices, offer samples, or promise dates.
- Do not claim facts that are absent from the deal or inspected proof.
- Save the requested browser screenshots and visited URLs for the run record.

## Result

Return one JSON object with these fields:

```json
{
  "deal_id": "DEAL_ID",
  "status": "completed",
  "message_html": "<p>Hey ...</p><p>Best,<br>Sasha</p>",
  "failure_code": "",
  "failure_message": ""
}
```

If the page cannot be inspected reliably, return `status` as `failed`, leave
`message_html` empty, and provide a short `failure_code` and `failure_message`.
Do not invent a generic message.
