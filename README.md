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
