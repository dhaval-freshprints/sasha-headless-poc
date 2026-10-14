# Deal Manager Agent

The agent identifies an existing deal from an inbound message using two companion skills and read-only database scripts. It has its own system prompt, worker and Docker container. Setup, web routes, configuration and test commands are in the repository [README](../README.md#independent-deal-manager-agent).

- [System prompt](system_prompt.md)
- [Database skill](skills/deal-manager-database/SKILL.md)
- [Matching skill](skills/deal-manager-resolve-deal/SKILL.md)
- [Database environment template](database.env.example)

Files use plain descriptive names within `deal_manager_agent/`. `SKILL.md` is the required skill manifest filename.

## Database configuration

Set the connection settings and both schema variables in a private environment file. `DEAL_MANAGER_MAIN_SCHEMA` contains deals, users, accounts, orders and proofs. `DEAL_MANAGER_OPERATIONS_SCHEMA` contains Sasha status, notes and `sasha_conversation_history`. DM uses this conversation table for provider, thread, sender and deal history lookups. No schema name is built in. The application relationships are documented in the database skill; inspect tables when using another deployment.

From this package directory:

```sh
python skills/deal-manager-database/scripts/database.py --env-file /absolute/private/database.env schemas
python skills/deal-manager-database/scripts/database.py --env-file /absolute/private/database.env --role main tables
python skills/deal-manager-database/scripts/database.py --env-file /absolute/private/database.env --role operations describe sasha_conversation_history
python skills/deal-manager-resolve-deal/scripts/collect_evidence.py --env-file /absolute/private/database.env --input /absolute/private/message.json
```

For standalone use, `install_skills.py` installs the two renamed skills as siblings, refusing to overwrite existing folders. The web agent copies only the repository's two runtime skill folders into each container. Previously installed skills with older names are not used by this web agent.

## Evidence and evaluation

Real sample messages, expected deal IDs and historical query snapshots are retained under `test_results` for evaluation and audit. They are not copied into the runtime skills or supplied as answers to the agent. Historical validation reports record earlier runs and do not certify later code or current data.

The matching policy compares all credible candidates and returns `needs_review` when evidence does not identify one deal. An unknown representative can still match through a verified person/organization/deal relationship. A proof can link to multiple deals. Stored routing labels, recency and account membership alone are not decisive.
