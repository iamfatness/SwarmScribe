# SwarmScribe

Distributed transcription for a large archive of English audio and video
recordings. A leader hands recordings to follower machines, which transcribe
them and return text, subtitles and word-level timings. Only recordings
explicitly marked OK to publish are ever processed.

Design: [`docs/superpowers/specs/2026-10-02-swarmscribe-architecture-design.md`](docs/superpowers/specs/2026-10-02-swarmscribe-architecture-design.md)

## Status

| Package | State |
|---|---|
| `swarmscribe-protocol` — leader–follower wire models | Built |
| `swarmscribe-engine` — single-file transcriber | Built |
| `swarmscribe-leader` — catalogue, consent, jobs, admin API and `swarmscribe-admin` | Built (local storage); cloud storage and vocabulary next |
| `swarmscribe-follower` | Not started |
| Helm chart | Not started |

## Transcribe one file

```
uv sync
uv run swarmscribe-engine recording.mp3 --out out --vocabulary vocabulary.txt --corrections corrections.txt
```

Writes `out/recording.mp3.txt`, `out/recording.mp3.srt` and
`out/recording.mp3.segments.json`.

- `--device auto|cuda|cpu` — default `auto`: a CUDA GPU uses `large-v3`
  (`float16`); otherwise `distil-large-v3` (`int8`) on CPU.
- `--model`, `--compute-type` — override the choice.
- `--vocabulary` — a text file with one word, name or phrase per line. These
  bias recognition throughout the recording. Order is
  priority: Whisper can take only about 220 tokens of terms (typically 60-150
  terms, depending on how unusual they are), so put the ones that matter most
  at the top. `segments.json` lists exactly which were used.
- `--corrections` — a text file with one fix per line, written
  `heard as => should be`, for example `jay son => Jason`. Applied after
  transcription as whole-word, case-insensitive replacements.
- `--channels mono|stereo-split|auto` — default `mono`: the channels are mixed.
  `stereo-split` transcribes the left and right channels separately (for
  recordings with one speaker per channel, such as calls) and merges them in
  time order; a file that is not two-channel is refused. `auto` splits a
  two-channel file and mixes anything else. `auto` assumes one speaker per
  channel, so an ordinary stereo recording (music, a dual-mono export, a phone
  app that writes mono into both channels) will be split too and the same
  speech can appear on both channels; use `mono` for those. Splitting decodes both channels,
  so it needs about twice the memory of mono (roughly 460 MB held and 1.2 GB
  peak per hour of audio during decoding).
- `--labels "Agent,Customer"` — names for the left and right channels
  (default `Left,Right`). Each label is 1–40 characters, with no leading or
  trailing spaces and no control characters or line breaks; the two labels
  must differ ignoring case; and a label cannot contain a comma, because the
  comma separates the two. In a split transcript every
  txt line and srt cue starts with `<label>: `, and `segments.json` records
  each segment's `channel` (0 left, 1 right) and the labels. Only allowed
  with `stereo-split` or `auto`.

In both files, blank lines are ignored and a line starting with `#` is a
comment (a `#` later in a line is part of the text, so `C#` works).

Rules for corrections:

- Whole words only, case-insensitive. The longest rule wins.
- Punctuation written on the "heard as" side must be present in the
  transcript.
- A multi-word rule matches only when nothing but spaces sits between the
  words.
- Possessive and hyphenated forms need their own line
  (`jay son's => Jason's`).
- Matches do not span a segment boundary.
- Two lines with the same "heard as" text and different replacements are an
  error.

`segments.json` records which terms were used and every correction that
fired, and keeps the original text of each corrected word.

Some settings are fixed on purpose: English only, no conditioning on previous
text (prevents repeated-sentence loops), and a temperature ladder capped at
0.4 (prevents gibberish on noisy audio).

## Run the leader (development)

The leader needs Postgres. Set:

| Variable | Meaning |
|---|---|
| `SWARMSCRIBE_DATABASE_URL` | e.g. `postgresql://user:pass@host:5432/swarmscribe` |
| `SWARMSCRIBE_PUBLIC_URL` | the leader's external base URL, used in file links |
| `SWARMSCRIBE_LINK_KEY` | at least 32 random characters, used to sign file links |

```
uv run swarmscribe-leader migrate
uv run swarmscribe-leader serve --port 8080
```

A location is a folder with a `consent.txt` at its root listing which
recordings may be processed, one glob per line (`talks/*.mp3`, `2024/**`).
Nothing else is ever queued. Transcripts are written beside the recordings
under `transcripts/`.

### Administrators: sign-in and roles

Administrators sign in with Microsoft Entra ID, Google, or either (both may be
configured at once). Each person gets a role; roles are cumulative:

| Role | May |
|---|---|
| viewer | `status`, `whoami`, `locations list`, `jobs list`, `followers list`, `consent report` |
| operator | viewer + `ingest`, `jobs retry/cancel/priority`, `followers drain` |
| admin | operator + `locations add/disable/enable`, `tokens create/list/revoke`, `followers revoke`, `console create/list/revoke` (a signed-in person only, never a console) |

Every admin call is written to the audit log with the person's email, issuer and
subject, except the console poller's successful reads of status, followers and
whoami (see "Fleet console credentials" below). Roles are cached for five
minutes, so a change of group membership takes up to five minutes to apply.

**Entra ID.** Register an application with "Allow public client flows" enabled
and the `groups` claim added to the ID token (security groups). Set
`SWARMSCRIBE_ENTRA_TENANT_ID` (the tenant's GUID) and
`SWARMSCRIBE_ENTRA_CLIENT_ID`, and map group object IDs to roles with
`SWARMSCRIBE_ROLE_VIEWER_ENTRA_GROUPS`, `…_OPERATOR_…`, `…_ADMIN_ENTRA_GROUPS`
(comma-separated). People in too many groups for the token are looked up in
Microsoft Graph: give the application a client secret
(`SWARMSCRIBE_ENTRA_CLIENT_SECRET`) and the `GroupMember.Read.All` application
permission.

**Google.** Create an OAuth client of type "TVs and Limited Input devices" and
set `SWARMSCRIBE_GOOGLE_CLIENT_ID` and `SWARMSCRIBE_GOOGLE_CLIENT_SECRET` (the
CLI needs this secret for device sign-in; Google treats it as public, and the
leader hands it to the CLI). Optionally restrict sign-in to one Workspace domain
with `SWARMSCRIBE_GOOGLE_HOSTED_DOMAIN`. Roles come from Google Groups when
`SWARMSCRIBE_GOOGLE_SERVICE_ACCOUNT` holds a service-account JSON key (or the path
of a mounted file with it) whose service account has the Groups Reader admin
role — map group emails with `SWARMSCRIBE_ROLE_<ROLE>_GOOGLE_GROUPS` — and, in
addition or instead, from `SWARMSCRIBE_ROLE_<ROLE>_EMAILS` and
`SWARMSCRIBE_ROLE_<ROLE>_DOMAINS`. Email and domain lists apply to Google
sign-ins only, and only to Google Workspace accounts (the token's `hd` claim):
a domain entry matches people whose Workspace is that domain, and an email entry
matches only when the address belongs to its own Workspace. Personal Google
accounts registered with a work address never match; the exception is a
`gmail.com` or `googlemail.com` address on an email list.

Use one app registration (client ID) per deployment, and sign in only to
leaders you trust: an ID token is accepted by any leader configured with the
same client ID, so a leader you sign in to could replay your token to another
one that shares it. A `503` while signing in or calling the API (an identity
provider or group directory that cannot be reached) is transient; retry
shortly.

`/readyz` reports ready once each configured provider's signing keys have been
fetched. When identity providers are unreachable, the check can take about
20 seconds per configured identity provider; set probe timeouts accordingly.

### `swarmscribe-admin`

```
uv run swarmscribe-admin --leader https://leader.example.org login --provider entra
uv run swarmscribe-admin status
uv run swarmscribe-admin locations add archive --root /mnt/archive --input-prefix incoming/
uv run swarmscribe-admin ingest archive
uv run swarmscribe-admin tokens create --pool default --expires 7d --max-uses 5
uv run swarmscribe-admin jobs list --state failed
uv run swarmscribe-admin jobs retry <job-id>
uv run swarmscribe-admin followers revoke <follower-id>
uv run swarmscribe-admin consent report
uv run swarmscribe-admin console create --name fleet --max-role operator
uv run swarmscribe-admin console list
uv run swarmscribe-admin console revoke fleet
```

`status --json` also gives `completed_last_day` and `oldest_queued_age_s` (how
long the oldest queued job has existed, by the database's clock; `null` when
nothing is queued).

For recordings with one speaker per channel (such as call recordings), add
`--channels stereo-split --labels "Agent,Customer"`: each recording is
transcribed per channel and every transcript line starts with its channel's
label. `--channels auto` splits two-channel files and mixes the rest, but it assumes
one speaker per channel: ordinary stereo, such as music or a dual-mono export,
is split too, so use it only for archives where that holds. The default,
`mono`, mixes the channels as before.

Labels are 1 to 40 characters, with no leading or trailing spaces and no
control characters, and the two must differ ignoring case. The CLI splits
`--labels` on commas, so a label cannot contain one there. Channel settings are
fixed when the location is added; this release has no command to change them.

The CLI accepts HTTPS leaders only; HTTP is allowed only for localhost
(`http://localhost`, `http://127.0.0.1`, or `http://[::1]`).

`login` shows a code to enter in the browser. The sign-in is kept in
`~/.config/swarmscribe/credentials.json` (readable by you only; override the
path with `SWARMSCRIBE_ADMIN_CREDENTIALS`) and refreshed silently; the leader
URL is remembered, or set `SWARMSCRIBE_LEADER_URL`. `--json` prints the
leader's answer as JSON. `--json` and `--leader` go before the command
(`swarmscribe-admin --json jobs list`, not `jobs list --json`). A join token is
shown once, by `tokens create`.

A location's root must be visible at the same path to every leader replica.

Local storage locations use the file's size, modification time and inode number
to detect changes; all leader replicas must see the same real filesystem. Network
filesystems that invent inode numbers per client are not supported.

`ingest` asks for a scan within a minute; `jobs cancel` is final for that
version of the recording until `jobs retry`; `followers revoke` releases the
follower's work at once.

### Fleet console credentials

A fleet console calls the admin API with its own credential, on behalf of the
person signed in to the console. Only a person who is an admin, signed in with
`login`, can manage console credentials: `console create`, `console list` and
`console revoke` are refused for a console credential (`403`) and for any role
below admin. `console create` takes a required `--max-role`
(`viewer`, `operator` or `admin`), the most the console may do. The credential
is printed once, to the terminal, with a "store this now; it will not be shown
again" line; the leader keeps only its hash, and the CLI never writes it to
`credentials.json` or any file.

A console's request acts with the lower of the role it asserts for the person
and that cap, and the same role checks apply as for a signed-in person. Every
admin call is audited, naming the person
"`<email> (<issuer> <subject>) via console <name>`", except the console's
status poller: its successful reads of status, followers and whoami write no
audit row. Any other poller read is still audited, and so is every refusal by a known
console; an unknown credential's 401 names no one and is not.

`console revoke` refuses the console (`401`, code `credential_revoked`) from
its next request; an unknown credential is `401 unauthorized`, so a console can
tell the two apart and stop calling until it is given a new credential. Console
names are never reused, even after a revocation, and a name already taken is
refused with `409 exists`: to rotate a credential, create one under a new name,
give it to the console, then revoke the old one. If the create succeeded but you
did not capture the credential, revoke that name and create a new one; names are
never reused. A console credential is
accepted only on `/v1/admin/*`, never on follower routes.

The console sends `Authorization: Console <credential>`,
`X-SwarmScribe-Actor: <issuer> <subject> <email>` (email `-` when unknown; or
`system:poller` for its status poller, which acts as viewer only) and
`X-SwarmScribe-Actor-Role: viewer|operator|admin`. The actor's parts are
printable ASCII separated by single spaces; the issuer is an `https://` URL of
at most 255 characters, the subject at most 255 characters and the email at
most 254. Malformed headers are refused with `400 invalid_actor` or
`400 invalid_actor_role`. These headers are ignored on a person's requests. A request must carry exactly
one `Authorization` header; two (two Bearer headers, or a Bearer and a Console)
are refused with `401`.

### Multi-replica test

`e2e/compose/` runs Postgres, two leader replicas behind nginx and scripted
followers, kills a follower and a replica mid-run, and checks that every
consented recording completes exactly once. It runs in GitHub Actions (job
`compose-e2e`); locally, with Docker:

```
docker build -t swarmscribe-leader:e2e -f e2e/compose/Dockerfile .
mkdir -p e2e/compose/work/data
docker compose -f e2e/compose/docker-compose.yml up -d
uv run python e2e/compose/run_e2e.py
docker compose -f e2e/compose/docker-compose.yml down -v
```

Tests use a real Postgres: set `SWARMSCRIBE_TEST_DATABASE_URL`, or leave it
unset and an embedded one starts automatically in `.pgdata/`.

## Run the fleet console (development)

The fleet console is one web service for many leaders. It has its own Postgres
(never a leader's). Set:

| Variable | Meaning |
|---|---|
| `SWARMSCRIBE_CONSOLE_DATABASE_URL` | the console's own database |
| `SWARMSCRIBE_CONSOLE_PUBLIC_URL` | the console's https origin, e.g. `https://console.example.org` (sign-in redirects to `<origin>/auth/callback`); plain `http` is accepted only for localhost |
| `SWARMSCRIBE_CONSOLE_KEY` | 32 random bytes, URL-safe base64: `python -c "import secrets; print(secrets.token_urlsafe(32))"`. Seals leader credentials; losing it means re-entering every credential |
| `SWARMSCRIBE_CONSOLE_ENTRA_TENANT_ID`, `_ENTRA_CLIENT_ID`, `_ENTRA_CLIENT_SECRET` | Entra ID web app registration (all three) |
| `SWARMSCRIBE_CONSOLE_GOOGLE_CLIENT_ID`, `_GOOGLE_CLIENT_SECRET` | Google OAuth web client (both); optional `_GOOGLE_HOSTED_DOMAIN`, `_GOOGLE_SERVICE_ACCOUNT` (Google Groups) |
| `SWARMSCRIBE_CONSOLE_SESSION_LIFETIME_SECONDS`, `_SESSION_IDLE_SECONDS`, `_LOGIN_ATTEMPT_SECONDS` | optional: session lifetime (default 28800, 8 hours), idle timeout (3600, 1 hour), how long a started sign-in may take (600) |

Bootstrap the first console administrator with the command that fits your
sign-in provider (see "Which principals a sign-in yields" below):

```
uv run swarmscribe-console migrate

# Entra ID sign-in: a group's object id (Entra gives group ids only, never an email)
uv run swarmscribe-console admins add entra_group <group-object-id>

# Google sign-in: an email, or a Workspace domain
uv run swarmscribe-console admins add email you@example.org

uv run swarmscribe-console serve --port 8443
```

Sign-in uses the same rules as the leader (section "Administrators: sign-in
and roles"). A person may sign in only if a grant or a console-administrator
entry names one of their principals. Group membership is read at sign-in; it
applies to an open session at the next sign-in (sessions last at most 8 hours,
1 hour idle).

### Which principals a sign-in yields

A grant or console-administrator entry only matches if the person's sign-in
produces that kind of principal:

| Provider | Principal kinds a sign-in yields |
|---|---|
| Entra ID | `entra_group` (group object ids) **only**. An Entra email is never a principal, so an `email` or `domain` entry can never match an Entra user |
| Google | `email` and `domain` under the Workspace rules (the token's `hd` claim equals the domain; a gmail.com address is its own email), and `google_group` when `SWARMSCRIBE_CONSOLE_GOOGLE_SERVICE_ACCOUNT` is set |

`swarmscribe-console admins add` knows which providers are configured. It
refuses a kind that no configured provider can produce (an `email` on an
Entra-only console would lock everyone out), and warns when a `google_group`
is added without the service account. A console with both providers accepts
every kind.

Console administrators manage leaders and grants (under `/api/admin`); that
gives them no role on any leader. A person's role on a leader is the highest
grant whose scope matches it: `leader:<name>`, `label:<key>=<value>` or `all`.

### Console administrators and grants

`swarmscribe-console admins add <kind> <principal>` (kind: `entra_group`,
`google_group`, `email` or `domain`) adds a console administrator straight in
the database, which is how the first one is made: whoever can run it holds the
database, so it is not behind sign-in. It also works when administrators
already exist (it adds one more; an existing one is refused as "already").
`swarmscribe-console admins list` prints `<kind>:<principal>` and who added it.
Both need a migrated database **and** the full `SWARMSCRIBE_CONSOLE_*`
configuration (database URL, public URL, key and at least one identity
provider, as for `serve`): the command reads the configuration to know which
principal kinds can match. Everything else is done by a console
administrator over the API, which needs the session cookie and, on every
change, the `X-CSRF-Token` header (from `GET /api/session`):

| Route | Does |
|---|---|
| `GET`, `POST /api/admin/leaders`; `PATCH`, `DELETE /api/admin/leaders/{name}`; `PUT /api/admin/leaders/{name}/credential` | the leader registry |
| `GET`, `POST /api/admin/grants`; `DELETE /api/admin/grants/{grant_id}` | role grants: `{role, scope, principal_kind, principal}`; role is `viewer`, `operator` or `admin` |
| `GET`, `POST /api/admin/console-admins`; `DELETE /api/admin/console-admins/{admin_id}` | console administrators: `{principal_kind, principal}` |

The leader registry has its own rules. A leader's name is unique ignoring case.
Changing its `base_url` (`PATCH`) needs the console credential in the same
request (`422 credential_required` without it), because the credential is
sealed bound to the name and URL and must not follow an edit to another host;
a credential sent with an unchanged URL is `422 use_rotate` (replace it alone
with `PUT .../credential`). A URL change or a rotation clears the revoked mark
and resets the leader's poll health. Removing a leader also removes the grants
whose scope is `leader:<that name>` (each is audited), so a leader registered
later under the same name inherits none; label and `all` grants stay. An
unknown leader, grant or administrator on `/api/admin` is `404 not_found`.

Principals are stored as lowercase ASCII (an Entra group id as a lowercase
GUID), exactly as sign-in produces them, so `Person@Example.org` and
`person@example.org` are one principal. A principal has at most one role per
scope (a second grant is `409 exists`; remove the first). Removing the last
console administrator is refused with `409 last_admin`, also when two removals
race. (The rule counts administrator entries, not administrators who can still
sign in; the `admins add` command is the way back in.) A grant may name a leader
that is not registered yet: a scope only matches registered leaders when a role
is looked up. Every change is written to the audit log.

### Leaders in the console

On each leader, a leader administrator creates a credential for the console:

```
swarmscribe-admin console create --name fleet --max-role operator
```

Prefer `operator`: the leader trusts the console's word for who the person is,
so the cap is the only bound on what a leaked credential can do. Use `admin`
only if people must do one of these from the console, because the leader needs
the `admin` role for each of them (and the console refuses them below it):

| Leader action | Needs |
|---|---|
| `GET tokens` (list join tokens), `POST tokens` (create), `POST tokens/{id}/revoke` | `admin` |
| `POST followers/{id}/revoke` | `admin` |
| `POST locations` (add), `POST locations/{name}/enable`, `.../disable` | `admin` |

Everything else the console offers needs `operator` (retry, cancel or reprioritise a
job, drain a follower, request a location scan) or `viewer` (status, locations,
jobs, followers, consent report). With an `operator`-capped credential the
`admin` actions fail with the leader's own `403`. Console administration (the
leader's `consoles` routes) is never proxied.

A console administrator then registers the leader (`POST /api/admin/leaders`
with its name, `https://` URL, labels and the credential). The URL rules, the
egress policy and what a URL change or rotation resets are in "Console
administrators and grants" and "Deployment note: egress" in this section, and
are not repeated here. The credential is sealed with `SWARMSCRIBE_CONSOLE_KEY`,
bound to the leader's name and URL, and never shown again.

**Rotation.** Console names are never reused on a leader. Create a new one
(`console create --name fleet-2 ...`), replace the credential in the console in
place (`PUT /api/admin/leaders/<name>/credential`), then revoke the old one
(`console revoke fleet`).

**Errors from the leader routes.** `/api/leaders/{name}/...` and `/api/fleet` answer
`{code, message}`. An unknown leader, or one the person holds no grant on, is
`404 leader_not_found` (the two are indistinguishable); `not_found` is the
admin API's, and on `/api/leaders/{name}/...` it is an action the console does
not offer or the leader's own 404 (an unknown job, follower or token). A leader's
own errors pass through with its status and code (a leader 422 keeps its code but
gets fixed text; `Retry-After` is passed on, at most 3600). A `401` is always the
console's own session, never the leader's. The console adds these codes:

| Status | Code | Meaning |
|---|---|---|
| 503 | `leader_unreachable` | cannot reach the leader or it timed out; `Retry-After: 15` |
| 503 | `leader_credential_revoked` | the leader revoked this console's credential; replace it |
| 503 | `leader_credential_unreadable` | the stored credential cannot be opened with the console key |
| 502 | `leader_credential_rejected` | the leader does not accept the credential |
| 502 | `bad_gateway` | an unusable answer, or an unexpected console-side error; on a `POST` it is not known whether the action happened (the audit entry says `bad_gateway` or `error`) |
| 409 | `leader_disabled` | the leader is disabled in the console |
| 403 | `forbidden` | the person's role on this leader is below the action's; the message names the role needed |
| 403 | `actor_not_representable` | the person's identity cannot be sent to the leader |
| 422 | `invalid_request` | a bad query or body (fixed text, never an echo) |
| 413 | `too_large` | the request body is over 64 KiB |

**Polling.** Every 15 seconds the console reads each leader's status as
`system:poller` (viewer); the leader does not audit these reads. A leader that
fails three polls in a row is shown unreachable (within a minute). A leader
that answers `401 credential_revoked` is shown as revoked and not polled again
until its credential is replaced. Snapshots are kept for 24 hours; the overview
shows each leader's queue, completions in the last hour and day, failures,
followers by pool, the oldest queued job's age and scan errors, all from the
latest successful one. The 24-hour history is bucketed with `date_bin`, so the
console's Postgres must be **14 or later**.

**Actions.** The web app calls `/api/leaders/<name>/...`; the console checks the
person's role there, then forwards the call with the person as actor, and the
leader applies its own cap and role checks. Every action is audited in the
console and in the leader. A join token's plaintext is shown once and kept
nowhere.

**Connections.** The engine's pool is sized from
`SWARMSCRIBE_CONSOLE_POLL_CONCURRENCY` (default 8): `pool_size` is
`2 * concurrency + 2` and `max_overflow` is 10 for web requests, so 18 pooled
and up to 28 connections per replica at the default. Size the database's
`max_connections` for the replicas times that (the reasoning is in
`packages/console/README.md`).

**Web app.** `SWARMSCRIBE_CONSOLE_STATIC_DIR` points at the built web app (C3):
a folder holding `index.html`; blank means none. The console serves it under
its Content Security Policy (scripts and styles from its own origin only, no
inline). A path that is not a file and has no extension gets `index.html` so the
app's own routes survive a reload; `/api` and `/auth` never do.

### Deployment note: egress

This is the one place the leader-URL rules and the egress policy are written
down. The console refuses a leader URL that:

- is not `https://`, or carries user info, a query or a fragment, a non-ASCII or
  non-DNS host, a port outside 1-65535, or a path of anything but plain segments;
- names a loopback, link-local, unspecified, multicast or reserved address
  (this includes NAT64 `64:ff9b::/96`), a numeric spelling of an address, an
  IPv6 address with a zone id, or an IPv4-mapped form of any of those;
- names `localhost`, a `*.localhost` host, or one of `ip6-localhost`,
  `ip6-loopback`, `metadata`, `metadata.internal`, `metadata.google.internal`,
  `instance-data`, `instance-data.ec2.internal`;
- is in `0.0.0.0/8`, `fec0::/10`, 6to4 (`2002::/16`) or Teredo (`2001::/32`), or is
  one of the metadata addresses `fd00:ec2::254` (AWS IPv6) and `100.100.100.200`
  (Alibaba Cloud).

Private addresses are allowed, so the console can reach leaders on a LAN. These
checks read only the registered text; a DNS name can still resolve to any
address later. Add the same blocks to the console host's egress policy: deny
its traffic to loopback, link-local and metadata addresses (`169.254.169.254`,
`fd00:ec2::254`, `100.100.100.200`) and allow only the leaders' networks (the
poller's egress policy itself is C4's).

## Develop

```
uv sync
uv run pytest            # unit tests
uv run pytest -m smoke   # downloads tiny.en and runs the real model
uv run ruff check .
```

`av` is pinned below 19 in `packages/engine/pyproject.toml`: faster-whisper
1.2.1 passes `metadata_errors=` to `av.open`, which av 19 removed. Lift the
bound when faster-whisper supports av 19.

If you change a wire model, the schema snapshot test fails. Decide whether
`PROTOCOL_VERSION` must change, then regenerate:

```
uv run python -m swarmscribe_protocol.schema packages/protocol/tests/schema_v1.json
```
