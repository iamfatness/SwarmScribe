# Fleet console C2 — carried-forward items

These come from the final review of `fleet-console-c2a` (2026-10-04). None blocks merge.

## Carried forward

- **M3: the last-admin rule counts rows, not administrators who can still sign in.**
  `grants.remove_console_admin` refuses to remove the last `console_admins` row, but it does not ask
  whether the remaining rows can match any person. In an Entra-only console, an administrator can add a
  `domain:example.org` entry (the API accepts it), then remove their own `entra_group` entry. The
  removal returns 204 and nobody can manage the console from the UI.
  - **Recovery today:** `swarmscribe-console admins add entra_group <group-object-id>`, run by whoever
    holds the database. The top-level README says so.
  - **Ruling (final fix wave, 2026-10-04):** not fixed in C2a. The CLI refuses a kind no configured provider
    can produce (so the bootstrap cannot make this state), and the README documents the rule.
  - **If fixed later:** refuse the removal when no remaining entry has a kind the configured providers
    can produce, or refuse the API's `POST /api/admin/console-admins` for such a kind the way the CLI does
    (the API would need the provider configuration, which it already holds in `app.state.settings`,
    through `swarmscribe_console.main.principal_problem`).
