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
| `swarmscribe-follower` — the agent, its two images, its Helm chart (`deploy/helm/swarmscribe-follower`, one release per pool) and the native install for outside machines (a systemd unit, a Windows service) | Built; the Windows service is waiting for its first run under the service control manager (see "Run a follower on an outside machine") |
| `swarmscribe-console` — fleet console: backend, web app, image and Helm chart (`deploy/helm/swarmscribe-console`) | Built |
| Helm chart for the leader, and autoscaling | Not started |

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
| `SWARMSCRIBE_PUBLIC_URL` | the leader's external base URL, used in file links: an address every follower can reach (the same one for pods and for outside machines) |
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
| viewer | `status`, `whoami`, `locations list`, `jobs list`, `followers list`, `consent report`, and, signed in as a person only, `profiles list` |
| operator | viewer + `ingest`, `jobs retry/cancel/priority`, `followers drain` |
| admin | operator + `locations add/disable/enable`, `tokens create/list/revoke`, `followers revoke`; and, signed in as a person only (never through a console), `console create/list/revoke`, `pool-tokens create/list/revoke` and `profiles set` |

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
uv run swarmscribe-admin pool-tokens create --name gpu-pods --pool gpu
uv run swarmscribe-admin pool-tokens revoke gpu-pods
uv run swarmscribe-admin profiles list
uv run swarmscribe-admin profiles set cpu --model distil-large-v3 --compute-type int8
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

### Pool tokens versus join tokens

A **join token** expires and has a limited number of uses; use it for a machine
a person sets up. A **pool token** has no expiry and no limit on uses, and stays
valid until it is revoked; use it for a pool whose followers come and go by
themselves (Kubernetes pods). Anyone holding a pool token can register
followers for as long as it lives, so guard it like a password and revoke it if
it leaks.

```
uv run swarmscribe-admin pool-tokens create --name gpu-pods --pool gpu
uv run swarmscribe-admin pool-tokens list
uv run swarmscribe-admin pool-tokens revoke gpu-pods
uv run swarmscribe-admin pool-tokens revoke gpu-pods --revoke-followers
```

The token is shown once, by `create`; a follower is given it exactly like a
join token. Names are never reused. `revoke` stops further registrations and
leaves the followers it registered at work; `--revoke-followers` revokes them
too (each with its own audit entry, their leases released), which is what a
leaked token needs.

**Follower rows.** A follower that registers with a pool token takes over the
row of a `gone` follower of the same token that holds no lease, so pods that
start and stop do not grow the list of followers (the old credential stops
working at that moment). These rows are never reused: those of join-token
followers (an outside machine keeps its identity however long it is off), and
those of followers that are `draining` or `revoked`.

### Settings profiles

Each device class (`cuda`, `cpu`) transcribes with one profile: a model, a
compute type and a temperature ladder.

```
uv run swarmscribe-admin profiles list
uv run swarmscribe-admin profiles set cuda --model large-v3 --compute-type float16
uv run swarmscribe-admin profiles set cpu --model distil-large-v3 --compute-type int8 --temperatures 0,0.2,0.4
```

A change applies to every job claimed after it; a job already leased keeps what
it was given. A model is a name (`large-v3`, or `owner/name` for a Hugging Face
repository), never a path. `--temperatures` left out keeps the current ladder.

### What a follower is told

- **Drain.** `followers drain <id>` marks a follower `draining`. A draining
  follower gets no new work, and hears it on every claim: the empty `204`
  carries the header `X-SwarmScribe-Directive: drain`, so a follower with
  nothing to do can wind down too. Deregistering does not end a drain, and
  there is no command that does.
- **Fresh links.** `POST /v1/jobs/{id}/links` gives fresh download and upload
  links for a job that outlasts the two hours its upload links last. Only the
  follower that holds the job's lease may ask (anyone else is refused, as for
  any lease call). The claim counts as the first issue; after that, at most one
  per lease every `SWARMSCRIBE_LINKS_REFRESH_MIN_SECONDS` (60); sooner is
  answered `429` with `Retry-After`. The lease is not extended: that is the
  heartbeat's.

### Fleet console

Pool tokens and settings profiles are not managed from the fleet console. Person-only
commands (a console credential is refused, whatever its role): `console create/list/revoke`,
`pool-tokens create/list/revoke`, `profiles list` and `profiles set`.
Use `swarmscribe-admin`.

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

## Run a follower (development)

A follower takes recordings from a leader, transcribes them and uploads the
three outputs. It needs the leader's URL and, the first time, a join token (or
a pool token). With a leader on the same machine (its URL and its file links are
plain `http` there, which the follower accepts only with the development switch
`SWARMSCRIBE_FOLLOWER_ALLOW_HTTP=1`, loopback included):

```
uv run swarmscribe-admin tokens create --pool default
export SWARMSCRIBE_LEADER_URL=http://localhost:8080
read -rs SWARMSCRIBE_JOIN_TOKEN && export SWARMSCRIBE_JOIN_TOKEN     # paste the token, then Enter
export SWARMSCRIBE_FOLLOWER_ALLOW_HTTP=1
uv run swarmscribe-follower doctor
uv run swarmscribe-follower run
```

(`read -rs` takes the token without echoing it and keeps it off the command line and out of
the shell's history; it is bash's, and zsh's.)

Commands:

- `doctor` checks the settings, folders and device, loads the start-up model
  and runs it once (the first time that downloads it), asks the leader's
  `/healthz`, and says whether this machine has joined. It registers nothing
  and prints no token. Invalid settings, a plain-`http` leader without
  `ALLOW_HTTP=1` for one, are reported as `NOT READY`, in words. The start-up
  model is `SWARMSCRIBE_FOLLOWER_STARTUP_MODEL`, by default the device's
  default (`distil-large-v3` on CPU); the model a job uses is the leader's
  profile for the device. `--no-model` leaves the model out and `--no-leader`
  leaves the leader out, for a machine or an image checked before it has a
  network; what is left out is said (`not checked`), never counted as passed.
- `run` does the same checks, joins if there is no stored credential, then
  claims and transcribes until it is stopped.
- `join [--leader URL] [--token-stdin]` only registers and stores the
  credential. `--leader` replaces `SWARMSCRIBE_LEADER_URL` (and wins over it);
  `--token-stdin` reads the token from standard input. A token is never a
  command-line argument. Joining again while a credential exists does nothing
  (it would orphan the first registration); run `leave` first.
- `leave` deregisters with the stored credential and deletes it. It needs no
  settings: the credential remembers its leader, so `SWARMSCRIBE_LEADER_URL`
  may be unset; with a state folder other than the default, set
  `SWARMSCRIBE_FOLLOWER_STATE_DIR` as for `run`. Only a missing credential file
  means "has not joined" (exit 0); a credential file that cannot be read, or an
  invalid setting, is an error (exit 2). If the leader cannot be reached the credential is deleted
  anyway and the leader notices the silence.
- `cuda-paths` prints the folders of the GPU libraries that the `cuda` extra installed
  (`swarmscribe-follower[cuda]`), joined as `PATH` and `LD_LIBRARY_PATH` want them, or exits
  `3` and says the extra is missing. The follower does not need the variable: it loads cuBLAS
  from there itself before it loads a model on a GPU.
- `service install | uninstall | foreground` is the Windows service; see "Run a follower on
  an outside machine".

Every command takes `--env-file PATH` before its name (`swarmscribe-follower --env-file PATH
doctor`; the option is not abbreviated): `NAME=value` lines that are read into the
environment first and win over it (`#` comments, nothing expanded; a mistake is reported by
its line number and never by its content, and a validation error names the file). That is how
the systemd unit and the Windows service are configured, and how `doctor` and `leave` are run
by hand with the same settings. After `join --leader URL`, `run` and `doctor` use the leader
the stored credential names when `SWARMSCRIBE_LEADER_URL` is not set, and say so (`leader
https://... from the stored credential`); the settings file and the environment win over the
stored address. A stored plain-`http://` leader is used by `run` and `doctor` only with
`SWARMSCRIBE_FOLLOWER_ALLOW_HTTP=1` (`leave` may use it without, to tell the leader it is
going). With no leader set and another setting wrong, `doctor` reports a failed `settings`
check and `run` exits `2`.

| Variable | Default | Meaning |
|---|---|---|
| `SWARMSCRIBE_LEADER_URL` | required (except for `join --leader`, `leave`, and `run` and `doctor` after a join: they use the stored credential's leader) | the leader; `https`, or `http` only with `SWARMSCRIBE_FOLLOWER_ALLOW_HTTP=1` |
| `SWARMSCRIBE_JOIN_TOKEN`, `SWARMSCRIBE_JOIN_TOKEN_FILE` | none | a join or pool token, read only when a registration needs it: with no stored credential, and again if the leader stops knowing the credential (the file wins) |
| `SWARMSCRIBE_LEADER_CA_FILE` | none | PEM certificates trusted in addition to the public roots |
| `SWARMSCRIBE_FOLLOWER_ALLOW_HTTP` | `0` | `1`: accept a plain `http` leader URL and plain `http` file links (development only) |
| `SWARMSCRIBE_FOLLOWER_STARTUP_MODEL` | the device default | the model loaded and exercised at start-up, before registering; a model name or `owner/name`, in `ALLOWED_MODELS` when that is set. With `OFFLINE=1` a model that is not in the cache is exit `3`, naming this setting. An image that bakes one model sets it to that model |
| `SWARMSCRIBE_FOLLOWER_DEVICE` | `auto` | `auto`, `cuda` or `cpu` |
| `SWARMSCRIBE_FOLLOWER_POOL` | `default` | the pool name it reports; the token decides the real pool |
| `SWARMSCRIBE_FOLLOWER_STATE_DIR` | the user's data folder | credential and lock file (on Windows it must be reachable only by this account, SYSTEM and Administrators: see "Known limits") |
| `SWARMSCRIBE_FOLLOWER_SCRATCH_DIR` | `<state>/scratch` | working files; **everything in it is deleted** |
| `SWARMSCRIBE_FOLLOWER_MODEL_DIR` | Hugging Face's default | model cache |
| `SWARMSCRIBE_FOLLOWER_OFFLINE` | `0` | `1`: never download a model |
| `SWARMSCRIBE_FOLLOWER_ALLOWED_MODELS` | none | comma-separated; only these are loaded |
| `SWARMSCRIBE_FOLLOWER_SHUTDOWN_GRACE_SECONDS` | `8` | how long a stop may wait for the current job |
| `SWARMSCRIBE_FOLLOWER_ON_DRAINED` | `exit` | `exit`, or `park` under a supervisor that restarts whatever exits |
| `SWARMSCRIBE_FOLLOWER_LOG_FORMAT` | `json` | `json` or `text` |
| `SWARMSCRIBE_FOLLOWER_HEALTH_ADDR` | none (the images: `127.0.0.1:9108`) | `host:port` to listen on for `/healthz` and `/metrics`; unset, the follower opens no port |
| `SWARMSCRIBE_FOLLOWER_MEMORY_LIMIT_MB` | the cgroup's limit (a container's, or a systemd unit's `MemoryMax=`) or the machine's memory, the smaller | the memory the follower may use, at least 64; a recording that cannot fit is failed `out_of_resources` before it is transcribed |

Only one follower may use a state folder at a time (a second one exits `2`).
It refuses a scratch folder that holds files it did not put there. The
leader's `SWARMSCRIBE_PUBLIC_URL` must be an address the follower can reach:
the leader's own file links are built from it.

How it ends:

| Exit code | Meaning | Restart it? |
|---|---|---|
| `0` | stopped, or drained with nothing left to do | no |
| `2` | invalid settings, a state folder another follower holds, a scratch folder that is not its own | after fixing it |
| `3` | this machine cannot do the work: device, GPU libraries, model (`doctor` says which) | after fixing it |
| `4` | no or invalid token, or the follower was revoked | no: it needs a new token |
| `5` | the leader speaks another protocol version | no: upgrade |
| `1` | a bug in the follower (`error: unexpected ...`); `doctor`: the leader does not answer | report it |
| `130` | Ctrl+C before `run` started supervising (any other command) | n/a |

**Stopping.** Ctrl+C or `SIGTERM` (on Windows also Ctrl+Break, and the Windows service's
stop control) stops claiming.
The handlers are installed before anything else is even imported, so a stop also
works during start-up (in the first moments of the process, against a leader that
is down, while registering, or while the model loads): the process exits `0` and
registers nothing. A model load or download itself cannot be interrupted; the stop
is seen the moment it ends. In the image an init (`tini`) is PID 1 and covers the
moment before Python itself is up; see "Follower images".
The current job is finished only if its estimated time left fits the grace
period, or if it is already uploading or submitting; otherwise it is released
without counting an attempt and another follower redoes it. The stop takes
effect at the end of the segment being transcribed. The follower then
deregisters (the leader shows it `gone`), empties its scratch folder and exits
`0`; on a short recording that is well under a second, on a long one a second
or two. A second signal releases at once.

**Drain.** `swarmscribe-admin followers drain <id>` makes the follower exit `0`
on its next claim. It keeps its credential, so starting it again finds it still
drained (after loading the model, which takes a few seconds): `leave` it and
join again to put the machine back to work. **Revoke.** A revoked follower
notices on its next heartbeat or claim, releases its job, exits `4` and never
registers again by itself; its credential file stays, so a restart exits `4`
again.

**Pool tokens.** With a pool token, a follower with no stored credential (a new
pod, a fresh state folder) takes over the row of a `gone` follower of that
token. The old credential then stops working: a machine that still holds it is
answered `401`, logs `registering again`, and takes a row (that one's, if it is
`gone`) with a new credential. After a hard kill the leader marks the follower
`gone` once its lease and the leader's `SWARMSCRIBE_FOLLOWER_GONE_AFTER_SECONDS`
have passed, and requeues its job; the next start deletes what the dead process
left in the scratch folder.

Logs are one JSON object per line on stderr (`text` for people), with `job_id`
and `lease_id`. They never hold audio, transcript text, links, tokens or
credentials.

**Known limits.**

- The credential file is `0600` in a `0700` folder on POSIX. On Windows the folder and the
  file may be reachable only by the follower's account, SYSTEM and Administrators (their
  access control lists are read): a state folder of the account's own is made so before the
  follower registers, and one that other accounts can reach once it holds a credential is
  refused (exit `2`) with the `icacls` command that fixes it. An entry of a kind the follower
  does not understand is refused too. A state folder that is itself a junction or a symbolic
  link is refused; a link higher up its path is not checked. When the calling process is an
  elevated administrator the follower's own service account (`NT SERVICE\SwarmScribeFollower`)
  is trusted as well, which is what lets an administrator run `doctor` and `leave` against the
  service's folder; a prompt that is not elevated is refused there. A follower that joined on
  Windows in a state folder that others can reach, before this check existed, may be refused
  once: run the printed command, or `leave` and join again. Deny entries are not read, and a
  custom scratch folder is made private but never refused.
- A download that is interrupted starts again from the beginning; it does not
  resume.
- A stop during the upload can leave outputs in storage that were never
  submitted. The next attempt at the job overwrites them.
- A start-up always loads the start-up model, even when the leader's profile
  names another, and a drained or revoked follower pays that load before it
  learns it has nothing to do. The cheap checks (settings, folders, a credential
  or a token) come first, so a follower that cannot register fails at once.

### Follower images

`docker/follower.Dockerfile` builds two images from the same code. Each holds the follower,
the engine and the protocol package, installed with `uv` into an environment that is all
the final image holds beside Python: no leader, no console, no build tools.

| | `swarmscribe-follower:cpu` | `swarmscribe-follower:cuda` |
|---|---|---|
| Build | `--target cpu` (the default) | `--target cuda` |
| Adds | nothing | cuBLAS, from the `nvidia-cublas-cu12` wheel (the follower's `cuda` extra) |
| Device | CPU (`auto` finds no GPU) | `SWARMSCRIBE_FOLLOWER_DEVICE=cuda`: without a GPU it exits `3` and says so, before it registers |
| Default model | `distil-large-v3`, `int8` | `large-v3`, `float16` |
| Size without a model | 0.8 GB (789 MB) | 2.5 GB (2548 MB) |
| Needs at run time | nothing | the NVIDIA container runtime, for the driver's own libraries |

```
docker build -t swarmscribe-follower:cpu  --target cpu  -f docker/follower.Dockerfile .
docker build -t swarmscribe-follower:cuda --target cuda -f docker/follower.Dockerfile .
```

**Run one.** The follower runs as user 10001. PID 1 is a minimal init (`tini`, Debian's
package) and the follower is its only child: `docker stop` reaches the follower through it,
and the follower exits `0` with the job in hand released (see "Stopping" above). A stop in
the first tens of milliseconds, before Python has started, ends the container with `143`:
nothing had been started. No `--init` flag is needed, and a stop is never lost (a lost stop
would end in a kill, `137`, when the stop window closes). Restart it on failure, a few
times: exit `0` (stopped, or drained) is final for Docker, but Docker cannot tell the other
codes apart, and without a count a revoked follower (exit `4`) or a misconfigured one
(exit `2`) would be started again for ever.

```
docker run -d --restart on-failure:5 --read-only --cap-drop ALL --stop-timeout 930 \
  --security-opt no-new-privileges \
  -e SWARMSCRIBE_LEADER_URL=https://leader.example.org \
  -v /path/to/join-token:/run/secrets/join-token:ro \
  -e SWARMSCRIBE_JOIN_TOKEN_FILE=/run/secrets/join-token \
  -e SWARMSCRIBE_FOLLOWER_SHUTDOWN_GRACE_SECONDS=900 \
  -v swarmscribe-follower:/var/lib/swarmscribe-follower \
  -v swarmscribe-models:/models \
  swarmscribe-follower:cpu
```

The token is in a file on the host that holds nothing else and that user 10001 can read
(mode `0644`, say, in a folder only you can enter), mounted read-only and named by
`SWARMSCRIBE_JOIN_TOKEN_FILE`; that is how the follower Compose test hands it over. The
variable `SWARMSCRIBE_JOIN_TOKEN` exists too, for throwaway tests: a token written after
`-e` stays in the shell's history and shows in `docker inspect`.

For a GPU machine: the `cuda` tag and `--gpus all`. The shorter line of the design
(`docker run -e SWARMSCRIBE_LEADER_URL=... -e SWARMSCRIBE_JOIN_TOKEN=... swarmscribe-follower:cpu`)
works too, with Docker's 10-second stop, a credential that lasts as long as the container,
and a model downloaded again for every new container.

It writes in three places and nowhere else, so the root filesystem can be read-only:

| Path | Holds | In the image |
|---|---|---|
| `/var/lib/swarmscribe-follower` | the credential and the lock file (also `HOME`) | a declared volume; name it (`-v swarmscribe-follower:...`) so that the credential outlives `docker rm`, which a single-use join token needs |
| `/scratch` | the recording being transcribed; emptied after every job and at every start | a declared volume |
| `/models` | the model cache | a plain folder: mount a volume to keep downloads (with `--read-only` it is needed), or bake the models in |

**The state folder must be the follower's own.** The credential is kept only in a folder
that belongs to the user the follower runs as (10001 in the image) and that nobody else can
write to. The follower checks this before it registers: a folder that fails is refused at
once with exit `2`, a message that names the folder, its owner and its mode, and no join
token is used. `swarmscribe-follower doctor` reports the same on its `state folder` line.
What each kind of mount needs:

| The state folder is | What it needs |
|---|---|
| a named volume (`-v swarmscribe-follower:/var/lib/swarmscribe-follower`), or nothing at all | nothing: Docker gives a new volume the image's owner and mode (10001, `0700`) |
| a folder of a Linux host (`-v /srv/follower:...`) | `sudo chown 10001:10001 /srv/follower && sudo chmod 700 /srv/follower` before the first start |
| a `tmpfs` | its owner and mode said: `--tmpfs /var/lib/swarmscribe-follower:uid=10001,gid=10001,mode=0700`. A plain tmpfs belongs to root and is writable by all |
| a folder of a Windows or macOS host under Docker Desktop (`-v C:\follower:...`) | not usable: inside the container it is seen as root's and writable by all (measured). Use a named volume |
| a Kubernetes `emptyDir` | it is root's and mode `0777` by default, and `fsGroup` changes its group, not its owner: refused as the state folder itself. Point `SWARMSCRIBE_FOLLOWER_STATE_DIR` at a folder inside the mount (`/var/lib/swarmscribe-follower/state`): the follower creates it as its own, `0700`. The chart does exactly this ("Deploy a follower pool"); on a kind cluster the mount was `0:10001` mode `3777` (read by hand in the first run: the kind driver asserts only that the mount is root's, and the folder's owner and mode `10001:2700`) and the folder `10001:10001` mode `2700` |

The follower never changes a folder that is not its own; one of its own that is looser than
`0700` it tightens itself.

**GPU prerequisites.** An NVIDIA GPU with a driver new enough for CUDA 12, and a Docker that
can hand it to a container: on Linux the NVIDIA Container Toolkit, on Windows Docker Desktop
with WSL 2 (nothing to install). Check with `docker run --rm --gpus all
nvidia/cuda:12.9.1-base-ubuntu24.04 nvidia-smi`, then with the image itself:

```
docker run --rm --gpus all -e SWARMSCRIBE_LEADER_URL=https://leader.example.org \
  swarmscribe-follower:cuda doctor --no-leader
```

`--no-leader` checks the machine without asking a leader. `doctor` names the GPU (`device: cuda (NVIDIA GeForce RTX 4090, 24564 MiB)`), loads the
model and runs it once: GPU libraries are loaded at the first inference, so a missing one
shows here and not in the middle of a job. The image carries cuBLAS only; the CTranslate2
it is locked to does not use cuDNN. One follower uses one GPU: on a machine with several,
run one container per GPU (`--gpus device=0`, `--gpus device=1`), each with its own state
volume.

**Baked models.** By default a follower downloads its start-up model from Hugging Face on
first start into `/models` (1.5 GB for `distil-large-v3`, 3.1 GB for `large-v3`). To put
models into the image instead, which is what a Kubernetes pool should run, name them at
build time:

```
docker build --build-arg MODELS=large-v3 -t swarmscribe-follower:cuda-large-v3 \
  --target cuda -f docker/follower.Dockerfile .
```

`MODELS` is a list of names separated by commas, with no spaces (`tiny.en,large-v3`); a
space or an empty item fails the build. Each model is downloaded during the build from one
pinned commit and every file is checked against its SHA-256 in `docker/models.lock.json`;
a file that differs fails the build. The first name becomes the start-up model
(`SWARMSCRIBE_FOLLOWER_STARTUP_MODEL`), and the image is offline
(`SWARMSCRIBE_FOLLOWER_OFFLINE=1`): it never asks Hugging Face for anything, and a job for
a model it does not hold is handed back and the follower exits `3`. The leader's profile
for the device must therefore name a model the image holds (`swarmscribe-admin profiles
set`). To allow a model that is not in the lock file yet, add the entry that
`uv run python docker/fetch_models.py --pin <name>` prints. A baked image grows by more
than the model's download size: with `tiny.en` baked the `cpu` image grows from 789 MB to
938 MB, and with `large-v3` baked (a 3.1 GB download) the `cuda` image grows from 2548 MB
to 8486 MB (4.8 GB of layers, by the planner's count).

The image also sets `HF_HUB_CACHE=/models` (and `HF_HUB_OFFLINE=1` when a model is baked),
so the engine's own command in it, `swarmscribe-engine`, reads the same cache and stays
offline. It does not know which model was baked: it asks for its device's default unless
told, so name the baked model and its compute type:

```
docker run --rm --network none --entrypoint swarmscribe-engine \
  -v "$PWD/recordings:/in:ro" -v "$PWD/out:/out" \
  swarmscribe-follower:cpu-tiny.en /in/call.wav --out /out --model tiny.en --compute-type int8
```

(`out` must be writable by user 10001. Without `--model` an image that holds only `tiny.en`
fails with `LocalEntryNotFoundError`.)

**Health and metrics.** In the images the follower listens on `127.0.0.1:9108`, inside the
container only, for the image's own `HEALTHCHECK`. `/healthz` answers 200 while the
follower's threads are alive: from the moment it starts supervising, so also while the
model loads, and also while the leader is away, so that nothing kills a follower that is
transcribing through an outage. It answers 503 when the supervising thread has not gone
round for 30 seconds, or, during a job, when the lease keeper has not gone round its loop
for three heartbeat intervals plus about 30 seconds (the time one request to the leader may
take). `/metrics`
is in the Prometheus format: `swarmscribe_follower_jobs_total{outcome}`,
`_audio_seconds_total` and `_transcribe_seconds_total` (their ratio is the speed),
`_job_progress`, `_model_load_seconds`, `_heartbeat_failures_total`,
`_download_bytes_total`, `_upload_bytes_total` and `_state` (idle, working, draining,
stopping). To scrape it from outside, listen on the container's address
(`-e SWARMSCRIBE_FOLLOWER_HEALTH_ADDR=0.0.0.0:9108`; an IPv6 literal such as `[::]:9108`
works too) and publish the port to the network
your Prometheus is on, never to the internet: it has no authentication. Outside the images
the listener is off unless the variable is set. Wherever it listens, it answers one request
per connection (`Connection: close`), drops a connection after five seconds in all however
slowly its bytes arrive, and holds at most eight connections at once (a ninth is closed
unanswered): a slow or silent client cannot keep a thread, or keep a probe waiting for long.

**Memory.** The engine reads a whole recording into memory and computes its features in
one piece, so what a job needs grows with the recording's length, on a GPU as on a CPU
(measured, 2026-10-05; GiB are 1024 MiB):

| | Host memory |
|---|---|
| `distil-large-v3`, `int8`, on a CPU | 1.7 GiB once loaded (1730 MiB) |
| `large-v3`, `float16`, on a GPU | 0.7 GiB once loaded (741 MiB), 3.1 GiB at the peak of loading (3155 MiB): the model passes through host memory |
| `large-v3`, `int8`, on a CPU | 3.5 GiB once loaded (3627 MiB) |
| each hour of the longest recording, mono | 3.4 GiB (3493 MiB on a CPU, 3511 MiB on a GPU); the follower counts 3600 MiB |
| each hour of the longest recording, split into channels | 2.6 GiB when the speakers alternate (2706 MiB); the follower counts 3900 MiB, for two who both talk throughout |

So a CPU follower with `distil-large-v3` for recordings of up to one hour needs about
5.6 GiB (what the follower counts for the first one, 1730 + 100 + 3600 MiB, and 300 MiB for
what the process keeps after a long recording: see "Sizing a pool"), and one for three hours
about 12.6 GiB; a GPU follower with `large-v3` needs 3.1 GiB to load its model at all. Before each
job the follower adds its estimate for the recording to what the process already holds and
compares that with what it may use: `SWARMSCRIBE_FOLLOWER_MEMORY_LIMIT_MB`, else the smaller
of the cgroup's memory limit and the machine's memory (`doctor` prints the figure and
where it comes from). A recording that cannot fit is failed `out_of_resources` at once, with
its length and the limit in the reason, instead of being killed half-way three times, hours
apart. Give the container a memory limit (`--memory 8g`) and the guard follows it; under
systemd a `MemoryMax=` on the unit (or on its slice) does the same, because the guard reads
the smallest limit from the process's own cgroup upwards. That is cgroup v2; on cgroup v1
only the root's limit file is read, so a unit's limit is not seen there: set
`SWARMSCRIBE_FOLLOWER_MEMORY_LIMIT_MB`.

On Windows the guard counts what the process holds (its working set), as on Linux; on macOS
the platform does not say, and the recording is counted alone. On both, when no limit is set
the limit is the machine's total physical memory, not what is free: set
`SWARMSCRIBE_FOLLOWER_MEMORY_LIMIT_MB` to what the follower may really use. (A Windows job
object's memory limit is not read.)

`bash docker/check-follower-image.sh <image> <cpu|cuda> [<baked model>]` checks an image
without a leader: its labels (title, source, description, and a version that is the
installed follower's), the user and the folders, that the leader and build tools are absent,
that an init is PID 1, that it starts read-only without capabilities, the listener and the
`HEALTHCHECK`, for `cuda` that cuBLAS loads and that the image refuses to start without a
GPU, and, for a baked image, that the model loads and runs with no network at all, that a
container stopped the moment it starts ends with `0` or `143` and never `137`, and that
`docker stop` ends a follower that is still starting. `CHECK_GPU=1` also runs a baked `cuda`
image on this machine's GPU.

**Known limits.**

- The `cuda` image is run on a GPU by hand, not in CI: GitHub's runners have none. It was
  run on an RTX 4090 in Docker Desktop (WSL 2), with a real leader: two followers shared six
  recordings, a stop mid-job took 1.8 s, drain exited 0 and revoke exited 4
  (`docs/superpowers/plans/2026-10-05-follower-f2-outcomes.md`). A Linux host with the
  NVIDIA Container Toolkit and Kubernetes with the device plugin have not been run.
- Only `amd64` has been built and run.
- A model downloaded at run time, not baked, is whatever its repository's `main` is that
  day; only baked models are pinned and checked.
- A long recording needs a great deal of memory (the table above); nothing splits it.
- On macOS the memory guard counts the recording alone and not the loaded model; on Windows
  and macOS its default limit is the machine's total memory.
- The images are not published: build them, or push them to a registry of your own.
- With `--init`, or in a pod that shares its process namespace, `tini` is not PID 1 and says
  so in one warning line on stderr; it still forwards signals.

### Follower Compose test

`e2e/follower-compose/` runs the `cpu` image, with `tiny.en` baked in, as two followers
against Postgres and two leader replicas behind nginx. The followers are read-only, without
capabilities, and on a network with no route out. It checks that both register with one
join token, are reported healthy by Docker, and share six recordings, each transcribed once
and saying what was said (on the right channel, where the location splits channels); that a
follower killed in the middle of a recording loses nothing and comes back as the same
follower with an empty scratch folder; that an 8-second lease survives a transcription
several times as long; that a follower stopped with `SIGTERM` mid-job releases the job
without a counted attempt; that an hour-long recording is refused by the memory guard and
parked with its reason, and that `/metrics` counts it (the followers there are limited to 2500 MiB); that a drained follower exits `0`
and a revoked one `4`, again when started again; and that a recording without consent is
never touched and no follower log holds a token, a link or transcript text. It runs in
GitHub Actions (job `follower-compose-e2e`); locally, with Docker:

```
docker build -t swarmscribe-leader:e2e -f e2e/compose/Dockerfile .
docker build --build-arg MODELS=tiny.en -t swarmscribe-follower:e2e --target cpu -f docker/follower.Dockerfile .
bash docker/check-follower-image.sh swarmscribe-follower:e2e cpu tiny.en
uv run python e2e/follower-compose/run_e2e.py prepare
docker compose -f e2e/follower-compose/docker-compose.yml up -d
uv run python e2e/follower-compose/run_e2e.py run
docker compose -f e2e/follower-compose/docker-compose.yml --profile followers down -v
```

It takes about two minutes and runs once per stack (it drains and revokes its followers): a
second run stops at once and says to `down -v` first. A follower that exits before it has
registered (an image built without `MODELS=tiny.en`, for one) stops the scenario at once,
with the follower's exit code and what it said. The leader is plain `http` behind the
proxy, as it is behind an ingress, so the followers set the development switch
`SWARMSCRIBE_FOLLOWER_ALLOW_HTTP=1`. The driver adds the join token, the profile and the
locations, and drains and revokes, with the leader's own functions against its database
(published on `127.0.0.1:15432`): this stack has no identity provider to sign an
administrator in. `LEADER_IMAGE` and `FOLLOWER_IMAGE` replace the two images.

On a machine with an NVIDIA GPU the same scenario runs the `cuda` image, with `large-v3`:

```
docker build --build-arg MODELS=large-v3 -t swarmscribe-follower:cuda-large-v3 --target cuda -f docker/follower.Dockerfile .
CHECK_GPU=1 bash docker/check-follower-image.sh swarmscribe-follower:cuda-large-v3 cuda large-v3
uv run python e2e/follower-compose/run_e2e.py prepare
docker compose -f e2e/follower-compose/docker-compose.yml up -d
FOLLOWER_IMAGE=swarmscribe-follower:cuda-large-v3 uv run python e2e/follower-compose/run_e2e.py run --gpu
docker compose -f e2e/follower-compose/docker-compose.yml -f e2e/follower-compose/docker-compose.gpu.yml --profile followers down -v
```

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
| `SWARMSCRIBE_CONSOLE_LEADER_CA_FILE` | optional: a PEM file of CA certificates trusted for calls to leaders, in addition to the public roots (for leaders whose certificates come from a private CA). It applies to leader calls only, which never read `SSL_CERT_FILE`. It is read at start, so restart the console after rotating the file. Calls to the identity providers (sign-in) read the standard `SSL_CERT_FILE`, which REPLACES the public roots for those calls: if you set it, the file must be a full bundle (the public roots plus any private CA), or sign-in at the real Entra ID or Google stops working. Most deployments should leave it unset |
| `SWARMSCRIBE_CONSOLE_LOGIN_ATTEMPTS_MAX` | optional: how many started sign-ins may be pending at once (default 10000; at least 100, since a smaller cap would drop sign-ins that are still in progress; the cost of each sign-in grows with the cap). Beyond it the oldest are dropped, so requests to `/auth/login` cannot fill the database; a flood of about cap divided by sign-in time requests a second can still evict real sign-ins, so rate-limit that path per client at your ingress as well |

Bootstrap the first console administrator with the command that fits your
sign-in provider (see "Which principals a sign-in yields" below):

```
uv run swarmscribe-console migrate

# Entra ID sign-in: a group's object id (Entra gives group ids only, never an email)
uv run swarmscribe-console admins add entra_group <group-object-id>

# Google sign-in (never on an Entra-only console, which refuses `email`): an email, or
# a Workspace domain
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

A console administrator then registers the leader, in the web app under
Administration, Leaders (or `POST /api/admin/leaders` with its name, `https://`
URL, labels and the credential). The URL rules, the
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
`packages/console/README.md`). Sizing: replicas x (2 x concurrency + 2 + 10), plus what
other clients of the same server need and the server's own reserve (Postgres keeps
`superuser_reserved_connections`, 3 by default). Behind a pooler such as PgBouncer in
transaction mode, set the pooler's server-side limit to that product, and note that the
poller's advisory locks are held per session and need session pooling.

**Web app.** The web app is `packages/console-web` (React and TypeScript, built
with Vite). `npm run build` there writes `packages/console-web/dist`; point
`SWARMSCRIBE_CONSOLE_STATIC_DIR` at that folder (it must hold `index.html`;
blank means none). The console serves it under its Content Security Policy
(scripts and styles from its own origin only, no inline). A path that is not a
file and has no extension gets `index.html` so the app's own routes survive a
reload; `/api` and `/auth` never do. Every built asset is named
`<name>-<16 hex characters>.<ext>` and cached for a year; `index.html` is
revalidated on every load. The overview refreshes every 10 seconds and stops
refreshing after 55 minutes without input, so the one-hour idle timeout still
applies to an open tab.

The console looks like SwarmScribe: honey amber on warm ink by default, with a designed
light theme that follows the system or the Theme switch. A rail on the left holds Fleet, a
link per leader and (for console administrators) Administration; below 900 pixels it
becomes a top bar with a Menu button. The fleet overview is four totals, a "Needs a look"
band and a card per leader. Its words are the console's own ("answering", "waiting",
"switched off", a role "given"), not the leader's protocol words.

Each leader opens a leader page with Jobs, Pools and followers, Locations, Consent report
and Join tokens tabs. Those read the leader live through the console's proxy (on open and
on Refresh, never on a timer) and carry the leader actions: try a job again, cancel or
reprioritise it, wind down or revoke a follower, add, switch on or off or scan a location,
create or revoke a join token. A button is switched off and names the role it needs when
the person's role on the leader is lower. Administration, shown to console administrators
only, is one page with three sections: Leaders (add, edit, replace the credential, remove),
Who can do what (give and remove roles) and Console administrators. Giving or removing a
role applies at once; group membership is read at sign-in.

Developing the web app: `npm run screens` in `packages/console-web` photographs every page and
dialog in both themes at three widths for a person to look at. The brand's colours live only
in `src/styles/tokens.css`; a small blocking script (`src/theme-boot.ts`) sets the theme
before first paint; a new route needs its prefix in `src/app/routePrefixes.json`.
`packages/console-web/README.md` has the app's own notes.

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
address later. So the same destinations must be refused when the connection is
made: the Helm chart's NetworkPolicy does that on Kubernetes, and "Deploy the
fleet console", "Egress", says what it covers, what it cannot, and what to do
on other hosts.

### Console image

`docker/console.Dockerfile` builds `swarmscribe-console`, the console's backend serving
the built web app. A Node stage builds `packages/console-web`; a Python stage installs the
console and the two packages it imports (leader and protocol) with `uv`; the final image
holds only that environment and `dist/` (`SWARMSCRIBE_CONSOLE_STATIC_DIR=/app/web`). It
carries no engine and no model libraries. It runs as user 10001, writes nothing (the root
filesystem can be read-only), listens on port 8080 and has a `HEALTHCHECK` on `/healthz`.
`serve` is the default command; `migrate` and `admins ...` are run by replacing the
arguments:

```
docker build -t swarmscribe-console -f docker/console.Dockerfile .
docker run --rm --env-file console.env swarmscribe-console migrate
# the first administrator on an Entra ID console: a group's object id
docker run --rm --env-file console.env swarmscribe-console admins add entra_group <group-object-id>
docker run -d --read-only --cap-drop ALL -p 8080:8080 --env-file console.env swarmscribe-console
```

A console that signs in with Google adds its first administrator with an email instead
(`admins add email you@example.org`, or a Workspace domain); an Entra-only console refuses
`email`. The image needs no writable path and no tmpfs.

The port is `SWARMSCRIBE_CONSOLE_PORT` (default 8080); the `HEALTHCHECK` reads the same
variable. To serve on another port set the variable rather than passing `serve --port`,
which the `HEALTHCHECK` cannot see. Probes must use GET (Kubernetes `httpGet` does).
`HEAD` on `/healthz` and `/readyz` answers like GET without a body, any other method is 405,
and `/healthz/` and `/readyz/` redirect to the canonical path; the web app's page is never
returned for a probe. Do not publish the probes on a public ingress: `/readyz` tells an
anonymous caller whether the database is up.

**During a database outage** the console logs one line per 30 seconds per cause (the
exception type, with its stack once) from the poller, and `/readyz` logs one line, at most
every 30 seconds, saying either that it cannot query the database or that the migrations are
not current. The responses to the probe stay fixed.

`console.env` holds the `SWARMSCRIBE_CONSOLE_*` variables from the table above. Put TLS in
front of it: the console's cookies are `Secure`, and its public URL must be `https://`
(plain `http` is accepted for localhost only).

The console answers two probes without a session. `/healthz` is 200 while the process
runs. `/readyz` is 200 when the database answers and its schema is this console's, or a
newer one (a rolling upgrade has migrated it and this replica is about to be replaced);
otherwise it is 503 with `database unreachable` or `database migrations are not current`.
Neither asks an identity provider or a leader, so their outages do not take the console
out of service.

### Console Compose test

`e2e/console-compose/` runs that image, read-only and as its own user, with Postgres, a
stand-in for Entra ID and two real leaders that have a database each. It does what an
operator does: the first console administrator is added with `swarmscribe-console admins
add`; a leader administrator signs in with `swarmscribe-admin login` and runs
`swarmscribe-admin console create` on each leader; a console administrator signs in
through the browser flow, grants a role and registers both leaders. Then one leader is
killed: within a minute the console shows it unreachable, while the other still answers a
proxied read and takes an action. It runs in GitHub Actions (job `console-compose-e2e`);
locally, with Docker:

```
docker build -t swarmscribe-leader:e2e -f e2e/compose/Dockerfile .
docker build -t swarmscribe-console:e2e -f docker/console.Dockerfile .
bash docker/check-console-image.sh swarmscribe-console:e2e
uv run python e2e/console-compose/run_e2e.py certs
docker compose -f e2e/console-compose/docker-compose.yml up -d
uv run python e2e/console-compose/run_e2e.py run
docker compose -f e2e/console-compose/docker-compose.yml --profile tools down -v
```

The scenario kills a leader, so it runs once per stack: a second run stops at once and says
to `down -v` first. It fails, with the measured number, if the leader takes more than 60
seconds to show unreachable.

The test changes nothing in the console, the leader or the admin CLI to make this
possible. All three use Entra ID's fixed address, so inside the Compose network the
stand-in holds the name `login.microsoftonline.com` (a network alias) with a certificate
from a CA the test makes for itself (`run_e2e.py certs`), which the containers trust
through `SSL_CERT_FILE` (it replaces the public roots there, which is what the test wants:
only the stand-in is reached). The leaders serve TLS from the same CA, and the console
trusts it for them through `SWARMSCRIBE_CONSOLE_LEADER_CA_FILE`.

## Deploy the fleet console

One console serves one organisation and any number of leaders. It needs:

- **Its own Postgres, version 14 or later** (the history view uses `date_bin`). Never a
  leader's database.
- **The image, built and loaded** where the cluster can pull it ("The image" below).
- **A Secret, created beforehand,** that holds the database URL, the console key and the
  identity provider's client secret ("Kubernetes, with the Helm chart", step 1). The chart
  never creates it, and the migration hook reads it before anything else exists, so it
  must be there before `helm install`.
- **An identity provider**: an Entra ID web app registration, a Google OAuth web client,
  or both, with `<public URL>/auth/callback` as a redirect URI.
- **HTTPS to every leader** it will manage. Leaders need nothing inbound from the console
  beyond their normal admin API, and never connect to it.
- **TLS in front of it.** The public URL is `https://` and the cookies are `Secure`.

### The image

`docker/console.Dockerfile` (section "Console image" above) builds `swarmscribe-console`.
No image is published yet, so the chart has no working default for `image.repository` and
`image.tag`: both are required, and the render fails saying so. Build the image and put it
where the cluster can pull it, or load it into the cluster. Publishing an image is a
follow-up.

```
docker build -t swarmscribe-console:0.1.0 -f docker/console.Dockerfile .
# a local cluster:        kind load docker-image swarmscribe-console:0.1.0
#                         (or: minikube image load swarmscribe-console:0.1.0)
# a registry of your own: docker tag swarmscribe-console:0.1.0 registry.example.org/swarmscribe-console:0.1.0
#                         docker push registry.example.org/swarmscribe-console:0.1.0
```

If `kind load docker-image` fails on Docker Desktop (its containerd image store can make it
stop with "content digest ... not found"), load the image from an archive instead. Either
form worked on kind v0.30.0 with Docker Desktop on Windows (the first command here also
worked on that machine, so the failure itself was not reproduced there):

```
docker save swarmscribe-console:0.1.0 -o swarmscribe-console.tar
kind load image-archive swarmscribe-console.tar
# or, without a file (the node's name is <cluster>-control-plane):
docker save swarmscribe-console:0.1.0 | docker exec -i kind-control-plane ctr -n k8s.io images import -
```

Use `image.pullPolicy: Never` (or `IfNotPresent`) for a loaded image. The image needs no
writable path, so the chart mounts no `/tmp`.

### Kubernetes, with the Helm chart

The chart is `deploy/helm/swarmscribe-console` (the console only; the leader's chart is a
separate piece of work). CI lints and renders it with Helm 4.3.0. It was also installed
once on a kind cluster (Kubernetes 1.34) with a throwaway Postgres: the migration hook ran
and finished before any console pod started, the pods became ready, `helm upgrade` ran the
hook again and rolled the pods, and a stopped database made `/readyz` answer 503 without
restarting a pod. That cluster's network plugin (kind v0.30.0, Kubernetes 1.34.0) enforces
NetworkPolicy: from a console pod, TCP 443 to the two identity providers and to a public
address and the Postgres port were reachable, while port 80 and port 8443 on a public
address, and the console's own Service on port 80, timed out. So a leader on a port other
than 443 is blocked until it is added to `networkPolicy.egress.https.ports`. A second
install, with a 49-character release name, also ran there after the fixes of the final
review: the hook ran, both pods became ready and `/healthz` and `/readyz` answered. What
was not run: any ingress controller, and a node drain.

1. Create the namespace and the Secret. The chart never creates a Secret: a values file and
   Helm's release history are not a place for the console key. The values below are
   placeholders. (`helm install --create-namespace` would also create the namespace, but
   the Secret has to exist before the install, because the migration hook reads it.)

   ```
   kubectl create namespace fleet
   kubectl -n fleet create secret generic swarmscribe-console \
     --from-literal=database-url='postgresql://console:...@db.internal:5432/swarmscribe_console' \
     --from-literal=console-key="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')" \
     --from-literal=entra-client-secret='...'
   ```

   A Google sign-in adds `google-client-secret` (and `google-service-account`, the service
   account's JSON key, when Google Groups are used). Keep a copy of the console key
   somewhere safe. It seals every leader credential; without it each one has to be created
   again on its leader and entered again. The key comes from the environment only: there
   is no KMS integration and no command that re-seals under a new key.

2. Write the values.

   ```yaml
   image:
     repository: swarmscribe-console   # or your registry's name for it
     tag: "0.1.0"
     pullPolicy: IfNotPresent
   publicUrl: https://console.example.org
   secrets:
     existingSecret: swarmscribe-console
   oidc:
     entra:
       enabled: true
       tenantId: 00000000-0000-0000-0000-000000000000
       clientId: 11111111-1111-1111-1111-111111111111
   ingress:
     className: traefik        # the class of YOUR ingress controller
     tls:
       secretName: console-tls
   networkPolicy:
     egress:
       postgres:
         peers:
           - ipBlock:
               cidr: 10.20.30.40/32     # Postgres outside the cluster
   ```

   For a Postgres inside the cluster, name its pods, not its Service (see "Egress"):

   ```yaml
   networkPolicy:
     egress:
       postgres:
         peers:
           - podSelector: {matchLabels: {app: pg}}   # same namespace; add a
                                                     # namespaceSelector for another one
   ```

   For Google, replace the `entra` block with `oidc.google.enabled: true` and
   `oidc.google.clientId` (optionally `hostedDomain`).

3. Install. The migration runs first, as a hook; the console pods start after it.

   ```
   helm upgrade --install console deploy/helm/swarmscribe-console -n fleet -f values.yaml
   ```

4. Add the first console administrator, with the command that fits the sign-in provider
   ("Which principals a sign-in yields" above). The release is called `console` here, so
   the Deployment is `console-swarmscribe-console`.

   With Entra ID, a group's object id (Entra gives group ids only, never an email, and an
   Entra-only console refuses `email`):

   ```
   kubectl -n fleet exec deploy/console-swarmscribe-console -- \
     swarmscribe-console admins add entra_group <group-object-id>
   ```

   With Google, an email or a Workspace domain:

   ```
   kubectl -n fleet exec deploy/console-swarmscribe-console -- \
     swarmscribe-console admins add email you@example.org
   ```

5. On each leader, a leader administrator creates a credential for the console
   (`swarmscribe-admin console create --name fleet --max-role operator`; "Leaders in the
   console" above says when `admin` is needed). A console administrator then registers the
   leader with it.

What the chart installs:

| Object | What it is |
|---|---|
| Deployment | `replicaCount` console pods (default 2) running `serve`: non-root (10001), read-only root filesystem, no capabilities, no service-account token; startup and liveness probes on `/healthz`, readiness on `/readyz`; requests 100m CPU and 256Mi |
| Job (hook) | `swarmscribe-console migrate`, before install and before every upgrade |
| Service | port 80 to the pods' 8080 |
| Ingress | the host of `publicUrl`, with TLS from `ingress.tls.secretName` (required) |
| Ingress (optional) | exactly `/auth/login`, with its own annotations: `ingress.signIn` |
| ConfigMap | the settings that are not secret |
| PodDisruptionBudget | `maxUnavailable: 1`, when there is more than one replica |
| NetworkPolicy | what the pods may reach and be reached from: "Egress" below |
| ServiceAccount | one with no token mounted; the console never calls the Kubernetes API |

Values:

| Value | Setting or meaning |
|---|---|
| `publicUrl` | `SWARMSCRIBE_CONSOLE_PUBLIC_URL`, and the Ingress host. Required. `https://<host>`, no port, no path |
| `image.repository`, `image.tag` | the image. Both required (`image.digest` wins over the tag) |
| `secrets.existingSecret` | the Secret's name. Required. `secrets.keys.*` name its keys: `database-url`, `console-key`, `entra-client-secret`, `google-client-secret`, `google-service-account` |
| `oidc.entra.enabled`, `.tenantId`, `.clientId` | Entra ID sign-in (`SWARMSCRIBE_CONSOLE_ENTRA_*`) |
| `oidc.google.enabled`, `.clientId`, `.hostedDomain`, `.serviceAccount` | Google sign-in (`SWARMSCRIBE_CONSOLE_GOOGLE_*`); `serviceAccount: true` reads the Google Groups key from the Secret |
| `ingress.tls.secretName` | the TLS Secret of the Ingress. Required while the Ingress is enabled |
| `networkPolicy.egress.postgres.peers` | where Postgres is. Required while the NetworkPolicy is enabled |
| `settings` | any other `SWARMSCRIBE_CONSOLE_*` setting that is not a secret, without the prefix: `POLL_CONCURRENCY`, `SESSION_IDLE_SECONDS`, `LOGIN_ATTEMPTS_MAX`, ... |
| `leaderCa.existingConfigMap`, `.key` | CA certificates for leaders on a private CA (`SWARMSCRIBE_CONSOLE_LEADER_CA_FILE`) |
| `port` | the port in the pod: the container port, `SWARMSCRIBE_CONSOLE_PORT`, and what the Service and NetworkPolicy follow |
| `replicaCount`, `resources`, `podDisruptionBudget` | scale and availability |
| `ingress.*`, `networkPolicy.*`, `migrate.*` | described in `values.yaml` |

`helm template` fails, saying why, before any hook can run, when:

- `publicUrl`, the image, the Secret, a sign-in provider, the Ingress's TLS secret or the
  Postgres peer of the NetworkPolicy is missing;
- `publicUrl` is anything but `https://<lowercase DNS hostname>` (a port, path, query,
  fragment, user, upper-case letter, space or IP address is refused);
- a value has the wrong type or range (`values.schema.json`): `replicaCount` is at least 1,
  `port` and `service.port` are 1 to 65535, `pullPolicy`, `service.type` and `pathType`
  take their listed values;
- a secret is put under `settings` or `extraEnv` in any letter case (`key`, `Database_URL`,
  `SWARMSCRIBE_CONSOLE_KEY`, `*_SECRET`, `GOOGLE_SERVICE_ACCOUNT`), or a name the chart sets
  itself (`PUBLIC_URL`, `PORT`, `LEADER_CA_FILE`, the `oidc` ones); a `settings` key must
  be upper case (`^[A-Z][A-Z0-9_]*$`);
- `ingress.paths` holds a Prefix `/`, which would publish `/healthz` and `/readyz`.

Object names are cut to 55 characters (`fullnameOverride` and `nameOverride` change the
base), so that `-migrate` and `-sign-in` keep every name and label within 63 even for a
53-character release name. With two or more replicas the pods are spread across nodes
softly (`spreadAcrossNodes`; your own `topologySpreadConstraints` replace it), and the
PodDisruptionBudget lets unready pods be evicted (`unhealthyPodEvictionPolicy:
AlwaysAllow`, Kubernetes 1.27 and later, so the chart needs 1.27), so a database outage
cannot stall a node drain.

### Database connections

Each replica pools `2 * POLL_CONCURRENCY + 2` connections and may open 10 more for web
requests: 18 pooled and up to 28 at the default concurrency of 8. With two replicas that
is up to 56, plus one for the migration job. Size Postgres's `max_connections` for it
(and for Postgres's own reserved connections). Behind a pooler such as PgBouncer, the
poller's advisory locks need session pooling.

### Probes, the Ingress and a database outage

The probes are `httpGet` (GET). Startup and liveness use `/healthz`, which never touches
the database; readiness uses `/readyz` with a 5 second timeout (the console's own database
check gives up at 3). The console also answers HEAD like GET and 405 to any other method,
and redirects `/healthz/` and `/readyz/` to the canonical path. The Ingress lists explicit
paths (`ingress.paths`) and does not route `/healthz` or `/readyz`: `/readyz` tells an
anonymous caller whether the database is up. A route added to the web app needs a new
entry in `ingress.paths`, for example `- {path: /reports, pathType: Prefix}`. The web
app's top-level routes are listed in one file,
`packages/console-web/src/app/routePrefixes.json`, which the app itself consults: a path
whose first segment is not in it is "Page not found", so a page cannot exist without being
listed. The render check (`deploy/helm/swarmscribe-console/ci/check_render.py`) reads that
file and fails when `ingress.paths` does not cover a prefix in it. The backend's side is
a console test (`packages/console/tests/test_route_prefixes.py`): every route the console
serves starts with `/api/`, `/auth/`, `/healthz` or `/readyz`, so the chart's `/api` and
`/auth` cannot go stale unnoticed.

During a database outage the console stays alive (liveness does not use the database), its
pods go unready, and the log stays short on purpose: the poller logs one line per 30
seconds per cause and `/readyz` logs one line, at most every 30 seconds, saying whether it
cannot query the database or the migrations are not current. Before C4a's limit this was
about 1,500 lines a minute per replica. The pods are not restarted, and are ready again
once the database answers. (A pod that starts while the database is unreachable exits and
is restarted until it answers; one restart was seen.)

### Egress

The console sends its leader credentials to whatever a leader's URL resolves to. It
refuses a URL that names a loopback, link-local, metadata or reserved address ("Deployment
note: egress" above), but a DNS name is only checked as text, so the network must refuse
the same destinations when the connection is made. The chart's NetworkPolicy allows, from
the console pods:

- DNS, to `networkPolicy.egress.dns.peers` (kube-dns by default);
- Postgres, to `networkPolicy.egress.postgres.peers` on port 5432;
- TCP 443 to anywhere **except** `0.0.0.0/8`, `127.0.0.0/8`, `169.254.0.0/16` (link-local:
  the AWS, Azure and Google metadata services, whatever name was used to reach them),
  `100.100.100.200` (Alibaba Cloud metadata), `168.63.129.16` (the Azure platform
  address), `224.0.0.0/4` and `240.0.0.0/4`; and for IPv6 `::/8` (loopback, IPv4-mapped,
  NAT64), `2001::/32` (Teredo), `2002::/16` (6to4), `fd00:ec2::254` (AWS metadata),
  `fe80::/10`, `fec0::/10` and `ff00::/8`. This one rule serves both the leaders and the
  identity providers.

**Peers are matched after Service translation.** Most network plugins compare the peer and
the port of a packet after the Service's virtual address has been replaced by a pod's. So
for a Postgres or a leader inside the cluster, name its pods (`podSelector`, with a
`namespaceSelector` for another namespace) or the pod CIDR, and the pod's own port: never
the Service's ClusterIP, which nothing is addressed to by then, and never a Service port
that differs from the pod's. The mistake looks like this: the pre-upgrade hook still
passes (it runs under the previous release's policy), then the new pods log
`cannot connect to the database: TimeoutError`, fail their startup probe, and
`helm upgrade --wait` times out. For the same reason, moving the database and changing
its peer in one upgrade fails the hook: the hook runs under the old peer.

Private addresses stay reachable, because leaders usually live on them. If every leader is
outside the cluster, add the cluster's pod and service ranges to
`networkPolicy.egress.https.extraExcept`. If leaders listen on another port, add it to
`networkPolicy.egress.https.ports`.

What a NetworkPolicy cannot do, so that nobody relies on it for more:

- It does nothing unless the cluster's network plugin enforces NetworkPolicy (Calico,
  Cilium, most managed clusters' policy add-ons and, observed here, kind v0.30.0's default
  network plugin do). Without one the object is accepted and does nothing.
- With the default `ingress.from: []`, any pod in the cluster can reach the console's port.
  Name your ingress controller's namespace there to narrow it.
- It cannot name a DNS host. Identity providers cannot be pinned to their names, and the
  metadata *names* the console refuses are blocked only through the addresses they resolve
  to. To allow only the leaders' networks, narrow `networkPolicy.egress.https.cidrs` to
  them and send identity-provider calls through a proxy (`extraEnv` with `HTTPS_PROXY`,
  and the proxy under `networkPolicy.egress.extra`): calls to leaders never use a proxy.
- It cannot block loopback inside the pod. The console is the only container there, so
  nothing but the console itself listens on it; do not add a sidecar that trusts
  loopback callers.
- NodeLocal DNSCache listens on a link-local address (`169.254.20.10`). Add it as an
  `ipBlock` under `networkPolicy.egress.dns.peers`.
- On a first install the migration Job runs before this policy exists. If the namespace
  denies egress by default, allow the Job's pod
  (`app.kubernetes.io/component: migrate`) to reach Postgres yourself.

Outside Kubernetes, put the same list in the host's firewall or the cloud's security
group: deny the console's outbound traffic to the ranges above, and allow only Postgres,
DNS, the leaders and the identity providers.

### Limiting sign-in attempts

Starting a sign-in (`/auth/login`) needs no session and stores a row until the person comes
back from the identity provider, or for ten minutes. Two things bound it:

- **In the console:** at most `SWARMSCRIBE_CONSOLE_LOGIN_ATTEMPTS_MAX` sign-ins are pending
  (default 10000). Beyond that the oldest are dropped, so a flood evicts itself and a
  person who signs in promptly still finishes. The console logs one warning a minute while
  it is dropping.
- **At the ingress:** a per-client rate limit, which only the ingress can do (the console
  does not see the client's address). `ingress.signIn.enabled` adds an Ingress for exactly
  `/auth/login` that carries its own annotations, so the limit does not slow the rest of
  the console. The annotations are your ingress controller's, not the chart's.

**The limit depends on the controller serving two Ingresses for one host as one site.**
Traefik and ingress-nginx do. GKE Ingress, and the AWS Load Balancer Controller without
a shared group (`alb.ingress.kubernetes.io/group.name`), give each Ingress its own load
balancer: the host's DNS reaches only one of them, the main Ingress's `/auth` Prefix serves
`/auth/login`, and the rate limit silently never applies. Check on your controller that
`/auth/login` really reaches the sign-in Ingress. The sign-in Ingress also carries the main
Ingress's annotations (a cert-manager issuer, for example), so a second certificate
request for the same Secret is possible with cert-manager's annotation on both.

A worked example for Traefik, which is maintained and widely used. The annotation and the
Middleware below are Traefik's names, not the chart's. Create the Middleware in the
release's namespace, then name it in the annotation as `<namespace>-<name>@kubernetescrd`:

```yaml
apiVersion: traefik.io/v1alpha1
kind: Middleware
metadata:
  name: sign-in-limit
  namespace: fleet
spec:
  rateLimit:
    average: 30      # requests ...
    period: 1m       # ... per minute, per client
    burst: 10
    # Behind a cloud load balancer the client is its forwarded address, not the peer:
    # sourceCriterion: {ipStrategy: {depth: 1}}
```

```yaml
ingress:
  signIn:
    enabled: true
    annotations:
      traefik.ingress.kubernetes.io/router.middlewares: fleet-sign-in-limit@kubernetescrd
```

The chart renders the Ingress with that annotation and nothing else; the Traefik side
(that the CRDs are installed, and what its counter keys on) was not run here. ingress-nginx
was retired in March 2026 and gets no further fixes, so the chart's examples and CI values
use neither it nor `className: nginx`; a cluster that still runs it can set its own
rate-limit annotation on `ingress.signIn.annotations`.

### Leaders on a private CA

Calls to leaders verify the leader's certificate against the public roots and never read
`SSL_CERT_FILE`. For leaders whose certificates come from your own CA, put the CA
certificates (PEM) in a ConfigMap and name it in `leaderCa.existingConfigMap`; outside
Kubernetes, set `SWARMSCRIBE_CONSOLE_LEADER_CA_FILE` to the file. They are trusted in
addition to the public roots, and for leader calls only. The file is read at start: restart
the pods after rotating it.

Do not set `SSL_CERT_FILE` (through `extraEnv`) for this. Calls to the identity providers
read it, and it REPLACES the public roots for those calls: a file that holds only your CA
stops sign-in at the real Entra ID or Google. If you must set it, make it a full bundle
(the public roots plus your CA).

### Upgrades

`helm upgrade` runs the migration, then replaces the pods one at a time
(`maxUnavailable: 0`). The migration is a pre-upgrade hook, so it runs while the old pods
are still serving: they keep serving on the migrated schema until they are replaced, and
`/readyz` stays 200 when the database is ahead. Every migration must therefore stay
compatible with the previous release (add a column now, drop the old one in a later
release); a migration that breaks the previous version breaks the live pods for the length
of the upgrade.

A console never *starts* on a database that is ahead of it, and there is no downgrade
command: after a migration, a rollback of the image alone leaves pods that refuse to start,
so roll forward again. A Job that succeeds is removed; one that fails is kept, the upgrade
fails, and `kubectl -n fleet logs job/console-swarmscribe-console-migrate` says why. A changed setting restarts the pods (the
Deployment carries a checksum of the ConfigMap).

## Deploy a follower pool

A pool is a set of followers that are alike: one image, one model, one size, one kind of
node. One release of the chart `deploy/helm/swarmscribe-follower` is one pool; install it
again under another name for another pool (a CPU pool and a GPU pool, two sizes, two
clusters). It needs:

- **A leader the pods can reach**, by the address in `leader.url`. The leader's own file
  links are built from its `SWARMSCRIBE_PUBLIC_URL`, so that address must be reachable from
  the pods too. The leader has no chart yet; the follower chart needs only its URL.
- **An image with its model baked in**, where the cluster can pull it ("The pool's image").
- **A pool token in a Secret, created beforehand.** The chart never creates a Secret.
- **For a GPU pool:** nodes with an NVIDIA GPU, the NVIDIA device plugin (it advertises
  `nvidia.com/gpu`) and whatever your cluster needs to hand the driver to a container
  (often a `RuntimeClass` named `nvidia`).

### The pool's image

No image is published, so `image.repository` and `image.tag` have no default: both are
required, and the render fails saying so. Bake the model in ("Follower images" above): a pod
then starts without Hugging Face, and every pod of the pool holds the same, checked model.

```
docker build --build-arg MODELS=distil-large-v3 -t swarmscribe-follower:cpu-distil-large-v3 \
  --target cpu -f docker/follower.Dockerfile .
docker build --build-arg MODELS=large-v3 -t swarmscribe-follower:cuda-large-v3 \
  --target cuda -f docker/follower.Dockerfile .
# a local cluster:        kind load docker-image swarmscribe-follower:cpu-distil-large-v3
# a registry of your own: docker tag ... && docker push ...
```

The leader's profile for the device must name the baked model (`uv run swarmscribe-admin
profiles set cpu --model distil-large-v3 --compute-type int8`): a baked image is offline, and a pod asked for a model it does not hold hands the
recording back and exits `3`. If `kind load docker-image` stops with "content digest ... not
found", load from an archive as "Deploy the fleet console" shows.

### Kubernetes, with the Helm chart

1. Create the pool token on the leader, and the Secret in the pool's namespace. A pool
   token does not expire and registers any number of pods ("Pool tokens versus join
   tokens").

   ```
   uv run swarmscribe-admin pool-tokens create --name cpu-pods --pool default   # shows the token, once
   kubectl create namespace transcribe
   kubectl -n transcribe create secret generic swarmscribe-pool-token \
     --from-file=pool-token=pool-token.txt    # a file that holds the token and nothing else
   ```

   (`--from-file`, so that the token is not in your shell's history; delete the file
   afterwards. A newline at its end does no harm.)

2. Write the values. A CPU pool:

   ```yaml
   image:
     repository: swarmscribe-follower   # or your registry's name for it
     tag: cpu-distil-large-v3
   leader:
     url: https://leader.example.org
   poolToken:
     existingSecret: swarmscribe-pool-token
   pool: default
   replicaCount: 4
   ```

   A GPU pool, as a second release:

   ```yaml
   image:
     repository: swarmscribe-follower
     tag: cuda-large-v3
   leader:
     url: https://leader.example.org
   poolToken:
     existingSecret: swarmscribe-gpu-pool-token
   pool: gpu
   replicaCount: 2
   gpu:
     enabled: true              # one GPU per pod, SWARMSCRIBE_FOLLOWER_DEVICE=cuda, no surge
   runtimeClassName: nvidia     # if your cluster hands out GPUs through a RuntimeClass
   nodeSelector:
     nvidia.com/gpu.present: "true"
   tolerations:
     - {key: nvidia.com/gpu, operator: Exists, effect: NoSchedule}
   ```

3. Install, one release per pool:

   ```
   helm upgrade --install cpu-pool deploy/helm/swarmscribe-follower -n transcribe -f cpu-values.yaml
   kubectl -n transcribe get pods -l app.kubernetes.io/instance=cpu-pool
   ```

   A pod is `Running` and ready within seconds: that says its threads are up, not that it
   has joined. That it has loaded its model and registered is in its log (`registered`) and
   in `swarmscribe-admin followers list`.

What the chart installs:

| Object | What it is |
|---|---|
| Deployment | `replicaCount` followers running `run` under the image's init: non-root (10001, `fsGroup` 10001), read-only root filesystem, no capabilities, no service-account token. Startup and liveness probes on `/healthz`; no readiness probe and no Service, because nothing connects to a follower. Rollouts replace a quarter of the pool at a time, with a surge pod on a CPU pool and none on a GPU pool, and count a new pod as available only after `minReadySeconds` (60) |
| NetworkPolicy | egress to DNS and to anywhere on port 443 and the port of `leader.url`, except loopback, link-local (cloud metadata) and reserved ranges; ingress to nobody but the peers in `networkPolicy.ingress.from` |
| ServiceAccount | one with no token mounted; a follower never calls the Kubernetes API |
| PodDisruptionBudget | only with `podDisruptionBudget.enabled` and more than one replica |

Values:

| Value | Setting or meaning |
|---|---|
| `image.repository`, `image.tag` | the image. Both required (`image.digest` wins over the tag) |
| `leader.url` | `SWARMSCRIBE_LEADER_URL`. Required, `https`. `leader.allowHttp: true` accepts plain `http` (a test cluster only) |
| `leader.ca.existingConfigMap`, `.key` | CA certificates (PEM) for a leader on a private CA (`SWARMSCRIBE_LEADER_CA_FILE`) |
| `poolToken.existingSecret`, `.key` | the Secret that holds the pool token (key `pool-token`). Required. Mounted as a file, mode 0440, and read only when a pod registers |
| `pool` | the pool name the followers report; the token decides the real pool |
| `replicaCount` | followers; `0` parks the pool |
| `resources` | CPU and memory; the memory limit is what the memory guard goes by ("Sizing a pool") |
| `gpu.enabled`, `gpu.resource`, `runtimeClassName`, `nodeSelector`, `tolerations`, `affinity` | a GPU pool and where it runs |
| `models.volume` | `none` (baked, the default), `emptyDir` (every new pod downloads its model) or `persistentVolumeClaim` with `models.existingClaim` |
| `scratch.sizeLimit` | room for the recording being transcribed (20Gi) |
| `terminationGracePeriodSeconds` | 900; the follower is told 30 s less |
| `minReadySeconds` | 60: how long a new pod must stay up before a rollout counts it as available (see below) |
| `settings` | any other `SWARMSCRIBE_FOLLOWER_*` setting, without the prefix: `ALLOWED_MODELS`, `STARTUP_MODEL`, `LOG_FORMAT`, ... |
| `extraEnv` | other environment, such as `HTTPS_PROXY` |
| `healthPort`, `metrics.scrapeAnnotations`, `networkPolicy.*`, `podDisruptionBudget.*` | described in `values.yaml`. `podDisruptionBudget.maxUnavailable` is an integer of at least 1 or a percentage such as `25%`; `0` and `0%` are refused (a budget that allows no eviction would stop every node drain) |

`helm template` fails, saying why, when the image, `leader.url` or the Secret's name is
missing; when `leader.url` is not `http(s)://host[:port][/path]`, or is `http` without
`leader.allowHttp`; when a value has the wrong type or a name the chart does not know
(`replicas` for `replicaCount`: `values.schema.json` allows no unknown value); when
`settings` or `extraEnv` names something the chart sets itself (the state folder, the
listener, the memory limit, the device, the leader's address, the token ...); and when a claim is not named for a
`persistentVolumeClaim` model volume.

### Rollouts and nodes

A pod is Ready about 3 seconds after it starts, before its model has loaded (exit `3` if it
cannot) and before it has registered (exit `4` if its token was revoked). Without a pause, a
new pod would count as available at once, and again after each restart, so an upgrade to an
image that cannot work would walk through every old pod and `helm upgrade --wait` would
report success. `minReadySeconds` (default 60, the Deployment's `spec.minReadySeconds`) is that
pause: a pod counts as available only after it has stayed up that long, so a rollout of pods
that cannot work stalls on its first batch instead of replacing the pool. It costs that long
per batch on every rollout.

- A rollout replaces about a quarter of the pool per batch, and each batch takes at least
  `minReadySeconds` plus the pod's start (the model load): a pool of four takes four batches,
  more than Helm's 5-minute `--wait` default once the model load is added. Pass `--timeout`
  longer than the rollout. A scale-down or node drain waits up to
  `terminationGracePeriodSeconds` for each pod.
- Spot or preemptible nodes give 30 to 120 seconds of notice: set
  `terminationGracePeriodSeconds` to the notice (the follower is told 30 s less). Otherwise a
  follower that judged its job fits in the default grace is killed first, the attempt is
  counted and the lease must expire.
- The cluster autoscaler does not scale down a node whose pods use an `emptyDir` (these do)
  unless the pod carries `cluster-autoscaler.kubernetes.io/safe-to-evict: "true"`; set it with
  `podAnnotations`.
- `models.volume: persistentVolumeClaim` with more than one replica needs a `ReadWriteMany`
  claim (a `ReadWriteOnce` claim can be mounted from one node only).
- With `resources.limits` removed, the memory guard and the thread count take the node's
  allocatable memory and CPU, not a figure sized for the pool.

### Sizing a pool

The memory limit decides the longest recording a pod takes. Before each job the follower
adds its estimate for the recording to what the process holds at that moment and compares
the sum with the limit, which the chart passes on (`SWARMSCRIBE_FOLLOWER_MEMORY_LIMIT_MB`);
a recording that cannot fit is failed `out_of_resources` with its length and the limit in
the reason, three times, and parked. Size the limit as:

```
what the model holds once loaded  +  0.4 GiB  +  3600 MiB x hours of the longest recording
```

(3900 MiB per hour where recordings are split into channels; 3600 MiB is about 3.5 GiB and 3900 MiB about 3.8 GiB.) The 0.4 GiB is the 100 MiB a
job of any length adds and 300 MiB for what the process keeps after long recordings (the
planner's measurement, 2026-10-05, in a pod: a follower held 230 MiB with `tiny.en` loaded
and 432 to 472 MiB after each of four hour-long recordings, so up to 242 MiB kept, rounded
up to 300; it does not keep growing; with `distil-large-v3` and half-hour recordings it kept
13 MiB). So a pod sized for exactly one long recording
by the table in "Follower images" refuses the next one: one that was tried did.

| Pool | Recordings up to | Memory limit (and request) |
|---|---|---|
| `distil-large-v3` on a CPU (1.7 GiB loaded) | 1 hour | 6Gi (the default) |
| | 2 hours | 10Gi |
| | 3 hours | 13Gi |
| `large-v3` on a GPU (0.7 GiB loaded, 3.1 GiB while loading) | 1 hour | 5Gi |
| | 2 hours | 9Gi |
| | 3 hours | 12Gi |
| `large-v3` on a CPU (3.5 GiB loaded) | 1 hour | 8Gi |

Keep the memory request equal to the limit: a follower uses nearly all of it for a few
seconds of every long recording, and a node that promised the same memory twice would kill
it there. The model figures are for a baked model, or one read from a cache. A pod that had
to download its model in the same start holds more until its container restarts
(2257 MiB against 1766 MiB with `distil-large-v3`), which is one more reason to bake. On a CPU pool the CPU limit is also the number of threads the engine uses
(`OMP_NUM_THREADS`); speed follows it. A GPU pod needs little CPU and exactly one GPU: one
follower uses one GPU, whole, so a node with four GPUs runs four pods.

### What the pods do

- **A stop** (a rollout, a scale-down, a node drain, `kubectl delete pod`): the follower
  finishes the recording in hand if its estimated time left fits in
  `terminationGracePeriodSeconds` less 30 s, and otherwise hands it back at the end of the
  segment it is on, without a counted attempt; another follower redoes it from the start.
  On the kind cluster a pod deleted in the middle of a recording was gone in 2.1 s of its 60 s grace (the second, timed run).
  What is lost is the compute already spent, so there is no PodDisruptionBudget by default:
  it would protect nothing else and make every node drain wait.
- **A crash or a kill of the container** (out of memory, a killed process): the container
  restarts in the same pod and is the same follower, because its credential lives in a
  memory-backed `emptyDir` that outlives the container, not the pod. The recording it held
  is redone when its lease expires, and that attempt is counted.
- **A new pod** (a deleted or evicted pod, a rollout, or a node that dies and takes its pod
  and the memory-backed credential with it) registers again with the pool token and takes
  over the row of a pod of the same token that has gone, so the leader's list stays the
  size of the pool at its largest. A recording the old pod held is redone when its lease
  expires, and that attempt is counted, unless the pod handed it back while stopping.
- **Drain** (`uv run swarmscribe-admin followers drain <id>`): the pod finishes its recording and
  parks. It stays `Running`, is not restarted, and takes nothing more
  (`swarmscribe_follower_state{state="draining"}` is 1). Delete the pod to replace it: its
  replacement is a new, active follower, and the drained row stays `draining`.
- **A pod that keeps restarting** says why in the last line of its log (`kubectl logs
  --previous`), and its exit code is in `kubectl describe pod`: `2` a setting is wrong; `3`
  this pod cannot do the work: a `cuda` image on a node without a GPU (`error: cuda was
  requested but no CUDA GPU is available`), or a model the image does not hold; `4` the
  pool token was refused or the follower was revoked. Each restart loads the model again
  before it finds out, with Kubernetes' growing back-off in between.
- **Shutting a pool out:** `swarmscribe-admin pool-tokens revoke <name> --revoke-followers`.
  Every pod then exits `4`, again at every restart: a revoked follower keeps its credential
  and never registers again by itself. **To bring the pool back,** create a new pool token,
  put it in the Secret, and `kubectl rollout restart deployment/<release>-swarmscribe-follower`:
  new pods have an empty state folder and register with the new token.
- **A changed Secret** needs no restart otherwise. The token is a mounted file, read only at
  a registration; the kubelet refreshes the file, and running followers keep their
  credentials.

### Probes, metrics and the network

The follower listens on the pod's own address (never loopback, never a Service) on
`healthPort` (9108). `/healthz` answers 200 while its threads are alive: also while the
model loads and while the leader is away, so a leader outage never restarts a pod that is
transcribing. `/metrics` is the Prometheus text of "Follower images". It has no
authentication, so by default the NetworkPolicy lets nobody in, and there is no Service and
no PodMonitor. On most network plugins the kubelet's probes are not affected: they come from
the pod's own node, which the policy does not cut off. If the pods' probes fail with the
policy on (the startup probe fails and every pod restarts), your plugin applies ingress
policy to node traffic too: add the nodes' address range to `networkPolicy.ingress.from` (an
`ipBlock`; the port is already limited to `healthPort`), or set `networkPolicy.enabled:
false`. The same limit applies to that advice on network plugins whose CIDR rules do not match
cluster-managed nodes or pods (Cilium, and GKE Dataplane V2, which inherits it): there an
`ipBlock` of the nodes' range does not let the probes in, so set `networkPolicy.enabled:
false` or use the plugin's own policy for node (host) traffic. Not run here. To scrape, name your Prometheus:

```yaml
networkPolicy:
  ingress:
    from:
      - namespaceSelector: {matchLabels: {kubernetes.io/metadata.name: monitoring}}
metrics:
  scrapeAnnotations: true    # prometheus.io/scrape, /port and /path on the pods
```

Name only peers you trust. The kubelet's probe shares the listener's eight connections with
every other client, so a named peer that opens eight slow connections every five seconds
(each is held for five seconds) can make `/healthz` unanswerable, and the kubelet then
restarts the pod. That is why nobody is allowed by default. Where the network plugin does not enforce
NetworkPolicy at all, every pod in the cluster is such a peer.

The listener answers one request per connection and drops a connection after five seconds
in all, however slowly its bytes arrive, and holds at most eight at once: a slow or silent
client cannot keep the kubelet's probe waiting for long (but see above for a peer that
keeps all eight places). To look at a pod by hand, ask from
inside it (`kubectl port-forward` reaches loopback, where nothing listens):

```
kubectl -n transcribe exec <pod> -- python -c "import os, urllib.request; print(urllib.request.urlopen('http://' + os.environ['POD_IP'] + ':9108/metrics').read().decode())"
```

Egress is open to port 443 and the port of `leader.url` anywhere but loopback, link-local
(the cloud metadata addresses) and reserved ranges: a NetworkPolicy cannot name a host, and
the leader's file links may point at a storage service. Narrow
`networkPolicy.egress.https.cidrs` to your leader and storage if you can. Peers are matched
after Service address translation: for a leader inside the cluster the port that counts is
its pod's, so add it to `networkPolicy.egress.https.ports` if it differs from the port in
`leader.url`. Object storage that serves its signed links on a port other than 443 (MinIO
on 9000, for example) is blocked until that port is added to the same list. A NetworkPolicy needs a network plugin that enforces it.

A leader or an object store that runs in the same cluster is reached through these `ipBlock`
rules, and on some network plugins (Cilium, and GKE Dataplane V2, which inherits it) a CIDR
rule does not match cluster-managed pods: the followers would be cut off from it, with pods
Ready that never register. This was not run here. Name the peer by selector instead, in
`networkPolicy.egress.extra` (raw `NetworkPolicyEgressRule` objects, added to the policy as
they are):

```yaml
networkPolicy:
  egress:
    extra:
      - to:
          - namespaceSelector: {matchLabels: {kubernetes.io/metadata.name: swarmscribe}}
            podSelector: {matchLabels: {app.kubernetes.io/name: swarmscribe-leader}}
        ports: [{protocol: TCP, port: 8080}]    # the pod's port, not the Service's
```

### What has been run, and what has not

CI lints the chart, validates what it renders against the Kubernetes schemas and checks
what must hold (`deploy/helm/swarmscribe-follower/ci/check_render.py`), for a CPU pool and a
GPU pool. On 2026-10-05 the chart was also installed on a local kind cluster (kind v0.30.0,
Kubernetes 1.34.0) against a real leader with Postgres, with the `cpu` image and `tiny.en`
(`e2e/follower-kind/`; the record is
`docs/superpowers/plans/2026-10-05-follower-f3-outcomes.md`). The scenario's driver,
`e2e/follower-kind/run_e2e.py`, checks each of the following and the scenario has passed
twice (the figures are those of the second, timed run): two pods became ready (in 3 s) and
registered (in 3 s) with a pool token from the Secret, and the state mount is root's and the
folder, credential and token file have the owners and modes the guide states (`10001:2700`,
`10001:600`, `0:440`; the mount's `3777` is from a hand inspection in the first run, not
asserted by the driver); recordings were
transcribed; a killed follower came back as itself and its recording was redone; a pod
deleted mid-recording handed it back and was gone in 2.1 s of its 60 s grace; a replacement
pod took over the row of one that had gone; an hour-long recording was refused under a
2500Mi limit; a drained pod parked and took none of the eight recordings submitted after
the drain; another pod was kept out of `/metrics` until `networkPolicy.ingress.from` named
it; and revoked pods exited `4` until a new token and a rollout restart. A second
scenario, `no-gpu`, checks that a `cuda` image exits `3` on a node without a GPU and a GPU
pool's pod stays `Pending` there. In the second run `up` took 1 min 51 s on a new
cluster, `run` 1 min 52 s and `no-gpu` 1 min 26 s. The first run exposed a race in the
test driver's reading of an exited container's log (fixed; the chart and the follower were
not at fault; the outcomes document has the detail). That the listener drops a slow client
after five seconds and turns away a ninth connection, and that a pod with
`models.volume: emptyDir` downloads its model through the NetworkPolicy, were observed in
the planner's run only (same document), not in either recorded run.

Not run:

- **A GPU pool on Kubernetes.** A kind cluster has no GPU. The `cuda` image was run on a
  GPU in Docker only (F2); the chart's GPU values are rendered and schema-checked, not run.
- A leader on a private CA (`leader.ca`), an IPv6 or dual-stack cluster, a persistent model
  volume, scrape annotations with a real Prometheus, a PodDisruptionBudget, a real node
  drain (a pod delete takes the same path), and any cluster but kind.
- The kind test is run by hand, not in CI.
- Autoscaling on queue depth belongs to the leader's chart (roadmap item 6).

## Run a follower on an outside machine

An outside machine is one that is not in the cluster: an office PC, a workstation with a GPU,
a volunteer's computer. It dials out to the leader over HTTPS and needs nothing opened towards
it. There are two ways to run a follower on one:

- **Docker**, where Docker is there already: "Follower images" above. Nothing more to install.
- **Natively**, which is what most Windows PCs want: the follower installed with `uv`, started
  by systemd or as a Windows service. This section.

Either way the machine receives recordings that are marked OK to publish, and only those; do
not give a join token to a machine you would not trust with them.

What has and has not been run is at the end of this section, and it matters: the Linux unit
has been run in a container with systemd as PID 1, the Windows follower has been run in a
console on a GPU, and **the Windows service has not yet been run under the Windows service
control manager**.

### What the machine installs

The packages are not on an index. Build three wheels from this repository, once, and hand
the machine the folder (with the constraints file, which pins every dependency to the
versions `uv.lock` has and CI tested):

```
uv build --package swarmscribe-protocol --wheel -o dist
uv build --package swarmscribe-engine --wheel -o dist
uv build --package swarmscribe-follower --wheel -o dist
cp deploy/follower-constraints.txt dist/
```

On the machine, with [uv](https://docs.astral.sh/uv/) installed and `dist` copied to it:

```
uv tool install --python 3.12 --find-links dist --constraints dist/follower-constraints.txt swarmscribe-follower
```

(`dist\follower-constraints.txt` in PowerShell.) On a machine with an NVIDIA GPU, write
`"swarmscribe-follower[cuda]"` in place of the last word. That is all a GPU needs besides its
driver (`nvidia-smi` must work): no CUDA toolkit, nothing to copy, no `PATH` or
`LD_LIBRARY_PATH`. The `cuda` extra brings cuBLAS as a wheel, and the follower loads it from
there itself, by its full path, before it loads a model on a GPU; `swarmscribe-follower
cuda-paths` shows where it is. (The planner measured why on Windows: CTranslate2 opens
`cublas64_12.dll` by its bare name, which found the wheel's folder through `PATH` on uv's
Python and not on the Microsoft Store's, and through `os.add_dll_directory` on the Store's
and not on uv's; a library already loaded by its full path was found on both, which is why
the follower loads it itself. A Python from python.org was not measured.) uv
downloads a Python of its own if the machine has none that fits. If the command is not found
afterwards, `uv tool update-shell` puts uv's tool folder on the `PATH`.

Where the machine has `git` and can reach the repository, this does the same without the
wheels (take the constraints file from the same commit to get the same versions):

```
uv tool install --python 3.12 --constraints deploy/follower-constraints.txt "swarmscribe-follower[cuda] @ git+https://github.com/iamfatness/SwarmScribe@<commit>#subdirectory=packages/follower"
```

(That form was run against a local clone of this repository, without the `cuda` extra, not
against the address on GitHub.) For yourself, without a service, either is enough:

```
swarmscribe-follower join --leader https://leader.example.org --token-stdin
swarmscribe-follower doctor
swarmscribe-follower run
```

`join` asks for the token on the terminal without echoing it; `doctor` and `run` then use the
leader the credential names. `doctor` loads the model and runs it once, on the GPU if there
is one; on a GPU machine its `device:` line must say `cuda`.

### Linux, with systemd

As root, from the folder that holds `dist` and `deploy` (a copy of this repository's
`deploy/systemd` will do). The follower is installed for the machine under
`/opt/swarmscribe-follower`, with its command in `/usr/local/bin`:

```
umask 022
export UV_TOOL_DIR=/opt/swarmscribe-follower/tools UV_TOOL_BIN_DIR=/usr/local/bin
export UV_PYTHON_INSTALL_DIR=/opt/swarmscribe-follower/python UV_COMPILE_BYTECODE=1
uv tool install --python 3.12 --find-links dist --constraints dist/follower-constraints.txt swarmscribe-follower

useradd --system --user-group --home-dir /var/lib/swarmscribe-follower --shell /usr/sbin/nologin swarmscribe-follower
install -d -o root -g swarmscribe-follower -m 0750 /etc/swarmscribe-follower
install -o root -g swarmscribe-follower -m 0640 deploy/systemd/follower.env.example /etc/swarmscribe-follower/follower.env
install -o root -g root -m 0644 deploy/systemd/swarmscribe-follower.service /etc/systemd/system/
```

Three things about where it goes:

- The tool directory, Python and command must be outside `/home` and `/root`: the unit sets
  `ProtectHome=yes`, which hides them from the service. (`/opt` and `/usr/local/bin`, as
  above, are the documented place.)
- The service's user must be able to read `/opt/swarmscribe-follower`, which is root's:
  hence `umask 022`. Under a root umask of `077` uv makes the folders for root alone (mode
  `700`), and the service's user cannot start the command (`Permission denied`; seen in the
  test machine). (`--user-group` makes the group the `install` lines name; not every
  distribution's `useradd --system` does that by itself.)
- On a GPU host, install `"swarmscribe-follower[cuda]"`, and the NVIDIA kernel modules must
  already be loaded at boot: the unit's hardening (`ProtectKernelModules=yes`,
  `NoNewPrivileges=yes`) stops `nvidia-modprobe` from loading them on demand. **No GPU has
  run under the unit** (see the end of this section).

Set `SWARMSCRIBE_LEADER_URL` in `/etc/swarmscribe-follower/follower.env`, and put the join
token (or a pool token), alone, into `/etc/swarmscribe-follower/join-token`:

```
( umask 027; cat > /etc/swarmscribe-follower/join-token )     # paste the token, then Ctrl+D
chgrp swarmscribe-follower /etc/swarmscribe-follower/join-token
systemctl daemon-reload
systemctl enable --now swarmscribe-follower
journalctl -u swarmscribe-follower -f
```

(Ctrl+D twice, when the pasted token has no newline after it.) The token is in no unit file,
no command line and no process's environment. The settings example names the file
(`SWARMSCRIBE_JOIN_TOKEN_FILE=/etc/swarmscribe-follower/join-token`), the service's group may
read it, and it stays that way: the follower reads it when it first registers and again
whenever the leader stops knowing its credential (a replaced leader database, say), and a
join token is used up by its first registration, so only a pool token can register again.
Do not delete or empty it. The credential the token was exchanged for is in
`/var/lib/swarmscribe-follower/state` (mode `0700`), with the scratch folder; the models are
in `/var/lib/swarmscribe-follower/models`. A leader on plain `http` (a test leader) also
needs `SWARMSCRIBE_FOLLOWER_ALLOW_HTTP=1` in `follower.env`.

To run a command by hand with the service's settings, run it as the service's user with the
same file:

```
runuser -u swarmscribe-follower -- swarmscribe-follower --env-file /etc/swarmscribe-follower/follower.env doctor
```

(`doctor`'s `memory:` line then describes the machine, not the unit: a command run by hand
is not in the unit's cgroup. `leave` the same way, with the service stopped, gives the
registration up.)

**To upgrade** (not run): stop the service, install the new wheels over the old with the
same variables and the same command plus `--reinstall`, and start it:

```
systemctl stop swarmscribe-follower
umask 022
export UV_TOOL_DIR=/opt/swarmscribe-follower/tools UV_TOOL_BIN_DIR=/usr/local/bin
export UV_PYTHON_INSTALL_DIR=/opt/swarmscribe-follower/python UV_COMPILE_BYTECODE=1
uv tool install --reinstall --python 3.12 --find-links dist --constraints dist/follower-constraints.txt swarmscribe-follower
systemctl start swarmscribe-follower
```

Without `--reinstall` uv answers that the tool `is already installed` and changes nothing
(seen with uv 0.12.22, on both systems; that is the only part of this that was run). Do not
run the `install ... follower.env` line again: it would overwrite your settings with the
example. Copy the unit again only if it changed, and then `systemctl daemon-reload`.

**What the unit says** (`deploy/systemd/swarmscribe-follower.service`, its `[Unit]` and
`[Service]` sections):

```
StartLimitIntervalSec=600
StartLimitBurst=5
Restart=on-failure
RestartSec=30
RestartPreventExitStatus=4 5
KillMode=mixed
TimeoutStopSec=930
```

### Windows, as a service

A service runs as an account of its own, which cannot read a user's profile and cannot use
the Microsoft Store's Python. So the follower is installed for the machine, with a Python
that uv fetches, under `C:\Program Files\swarmscribe-follower`. In PowerShell 5.1 opened with
"Run as administrator", from the folder that holds `dist`:

```
$root = "C:\Program Files\swarmscribe-follower"
$env:UV_PYTHON_INSTALL_DIR = "$root\python"
$env:UV_TOOL_DIR = "$root\tools"
$env:UV_TOOL_BIN_DIR = "$root\bin"
$env:UV_LINK_MODE = "copy"
$env:UV_COMPILE_BYTECODE = "1"
uv python install 3.12 --no-bin --no-registry
$python = (Get-ChildItem "$root\python\cpython-3.12.*-windows-*\python.exe" | Select-Object -First 1).FullName
uv tool install --python $python --find-links dist --constraints dist\follower-constraints.txt "swarmscribe-follower[cuda]"
& "$root\bin\swarmscribe-follower.exe" service install --print     # what it will do; changes nothing
& "$root\bin\swarmscribe-follower.exe" service install
```

(Write `swarmscribe-follower` without `[cuda]`, and without the quotes, on a machine without
an NVIDIA GPU.) `UV_LINK_MODE=copy` matters: without it uv hard-links the files from its
cache in your profile, and a hard link keeps the access list the file has there (you, SYSTEM
and Administrators), so the service's account could not read the follower's code and you,
not elevated, could change it. `UV_COMPILE_BYTECODE` compiles the code now, as on Linux: the
service's account cannot write beside it.

`service install` checks this before it changes anything. It reads the access lists of the
interpreter, of `service_boot.py` and of the package's `__init__.py`, and refuses (exit 2)
unless the service's account can read and execute each (an entry for Users, Authenticated
Users, Everyone or the account itself) and only Administrators, SYSTEM and TrustedInstaller
own it or can change it. An install made without `UV_LINK_MODE=copy` is refused with `error:
the service's account could not read ...\service_boot.py: ... install the follower again as
the README says: as an administrator, under C:\Program Files, with UV_LINK_MODE=copy set`;
the Microsoft Store's Python is refused too. `--print` prints the same message as a
`warning:` line, followed by the plan: if you see that line, install again as above, with
`--reinstall` added to the `uv tool install` line. The check has been run on installs made
without administrator rights (which it refuses); **the install under `C:\Program Files` has
not been made, so that the check passes there is expected, not seen.**

The plan, as the command would print it for this install (composed from the code with
these paths, not printed from an install under `C:\Program Files`; the long number is the
service's SID, which Windows derives from the name alone, so it is the same on every
machine):

```
folders: C:\ProgramData\swarmscribe-follower with state, models, logs inside
settings file: C:\ProgramData\swarmscribe-follower\follower.env (written if it is not there)
data folder: a new C:\ProgramData\swarmscribe-follower is created already restricted to Administrators and SYSTEM; the next line runs only for a folder that is already there
icacls.exe C:\ProgramData\swarmscribe-follower /inheritance:r /grant:r *S-1-5-32-544:(OI)(CI)F *S-1-5-18:(OI)(CI)F
sc.exe create SwarmScribeFollower binPath= "\"C:\Program Files\swarmscribe-follower\python\cpython-3.12.15-windows-x86_64-none\python.exe\" \"-I\" \"C:\Program Files\swarmscribe-follower\tools\swarmscribe-follower\Lib\site-packages\swarmscribe_follower\service_boot.py\"" start= delayed-auto obj= "NT SERVICE\SwarmScribeFollower" DisplayName= "SwarmScribe Follower"
sc.exe description SwarmScribeFollower "Takes recordings from a SwarmScribe leader and transcribes them."
sc.exe failure SwarmScribeFollower reset= 86400 actions= restart/60000/restart/60000//60000
sc.exe failureflag SwarmScribeFollower 0
icacls.exe C:\ProgramData\swarmscribe-follower /grant:r *S-1-5-80-274616168-3456989120-2567103782-416646175-3250980778:(OI)(CI)RX
icacls.exe C:\ProgramData\swarmscribe-follower\state /grant:r *S-1-5-80-274616168-3456989120-2567103782-416646175-3250980778:(OI)(CI)M
icacls.exe C:\ProgramData\swarmscribe-follower\models /grant:r *S-1-5-80-274616168-3456989120-2567103782-416646175-3250980778:(OI)(CI)M
icacls.exe C:\ProgramData\swarmscribe-follower\logs /grant:r *S-1-5-80-274616168-3456989120-2567103782-416646175-3250980778:(OI)(CI)M
```

(The `sc.exe create` line names the interpreter and the follower's own folder, so its paths
differ on another install; the `\"` are how Python prints the quotes, and the installer runs
each command as an argument list. `-I` starts the interpreter isolated: no `PYTHONPATH`, no
user site-packages; and `service_boot.py` puts the follower's own `site-packages` first on
the import path, so nothing installed elsewhere on the machine is imported in its place. The
first `icacls` line, the lock, is always printed but runs only for a data folder that already
existed, as the line before it says.)

One machine runs one follower service: the service's name (`SwarmScribeFollower`) and its
data folder (`C:\ProgramData\swarmscribe-follower`) are fixed.

`service install` does this, in this order. It looks at everything already under
`C:\ProgramData\swarmscribe-follower`, if the folder is there: every owner must be
Administrators, SYSTEM or the service's own account, and there must be no junction or
symbolic link at any depth; a folder it does not accept is refused, and it says to remove or
rename it. A folder it makes itself is created already protected, so that SYSTEM and
Administrators are the only accounts that can reach it, before anything is put in it. It then
makes `state`, `models` and `logs` and the settings file, registers the service
`SwarmScribeFollower` (not started; delayed automatic start) running as its own account
`NT SERVICE\SwarmScribeFollower` (no password to keep), grants that account its access by SID,
and looks at the owners and links again. If it fails after the service was created, it says
so: run `service uninstall` before trying again.

| There, under `C:\ProgramData\swarmscribe-follower` | What | Who |
|---|---|---|
| `follower.env` | the settings (`NAME=value`); written with the folders already filled in | administrators write, the service reads |
| `join-token` | the join or pool token, alone; you create it, and it takes the folder's list | administrators write, the service reads |
| `state\` | the credential and scratch | the service writes |
| `models\` | the model cache | the service writes |
| `logs\follower.log` | the service's log (a service has no console); kept as `follower.log.1` and begun again when it is over 10 MiB at a start | the service writes |

Set `SWARMSCRIBE_LEADER_URL` in `follower.env`, and put the token, alone, into `join-token`.
Notepad opened as administrator will do. Or, in the elevated PowerShell, these lines, which
ask for the token without showing it and put it on no command line and in no history:

```
$secure = Read-Host -AsSecureString "Token"
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
[Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) | Set-Content -Encoding ascii -NoNewline C:\ProgramData\swarmscribe-follower\join-token
[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
```

The token file is plain text: ASCII or UTF-8. What PowerShell 5.1 writes otherwise is read as
well (UTF-8 with a byte-order mark, from `Set-Content -Encoding UTF8`, and UTF-16 with one,
from `>` and `Out-File`); a file that is none of these is a settings error (exit 2) that
names the file. Do not put the token itself after `echo` or in any command: it would stay in
the history. The token is in no registry value and no command line.

Before the first start, look at the follower's code as the service's account will find it:

```
icacls "$root\tools\swarmscribe-follower\Lib\site-packages\swarmscribe_follower\service_boot.py"
```

It should list `BUILTIN\Users:(I)(RX)`, inherited from `C:\Program Files`, and nobody but
`NT AUTHORITY\SYSTEM` and `BUILTIN\Administrators` with more (expected, not seen: `service
install` has checked the same thing). Then:

```
sc.exe start SwarmScribeFollower
sc.exe query SwarmScribeFollower
Get-Content C:\ProgramData\swarmscribe-follower\logs\follower.log -Tail 20
```

As on Linux, keep the token file: the follower reads it again if the leader stops knowing its
credential. A start-up failure of the follower's own code is written to the log as `error:
unexpected <ClassName>`. A leader on plain `http` (a test leader) needs
`SWARMSCRIBE_FOLLOWER_ALLOW_HTTP=1` in `follower.env`. Behind a proxy, set `HTTPS_PROXY`
(and `NO_PROXY`) in `follower.env`, on either system: the file's lines become the follower's
environment, and its connections to an `https` leader and to `https` file links honour them
(a plain-`http` leader is never reached through a proxy).

`swarmscribe-follower service foreground` runs the service's own code in the console instead,
printing each status it would report to Windows; Ctrl+C is the stop control. It is the way to
see why a service does not start (as a user who may read `follower.env`, or with
`--env-file` naming another file: `service foreground` is the one `service` action that takes
`--env-file`, and `--print` is refused with it).

To run `doctor` or `leave` against the service's folder, use an **elevated** prompt and the
service's settings file:

```
& "$root\bin\swarmscribe-follower.exe" --env-file C:\ProgramData\swarmscribe-follower\follower.env doctor
```

The follower trusts the service's account on its own folder only when the process doing the
asking is an elevated administrator. From a prompt that is not elevated the command does not
get that far: the data folder admits SYSTEM, Administrators and the service's account only,
so the settings file cannot be opened, and the command ends with `error: the settings file
C:\ProgramData\swarmscribe-follower\follower.env cannot be read: Permission denied` and exit
2 (what the code does; not seen against a real service's folder). `leave` needs the service
stopped first (it takes the state folder's lock).

**To upgrade** (not run). The service's registration names the interpreter and the
follower's environment by their paths, and while the service runs its interpreter and
libraries are in use, so an install over them can fail part-way. The order, in an elevated
PowerShell with `$root`, the five `$env:UV_...` lines and `$python` of the install set again:

```
sc.exe stop SwarmScribeFollower
(Get-Service SwarmScribeFollower).WaitForStatus('Stopped', '00:16:00')
& "$root\bin\swarmscribe-follower.exe" service uninstall
uv tool install --reinstall --python $python --find-links dist --constraints dist\follower-constraints.txt "swarmscribe-follower[cuda]"
& "$root\bin\swarmscribe-follower.exe" service install
sc.exe start SwarmScribeFollower
```

The stop lets a recording in hand finish or hands it back, which can take up to the grace
period (900 s as installed): the second line waits until the service has stopped, and
nothing is installed before that. `service uninstall` removes the registration (by itself it
would also stop a running service, and the name could then stay busy for up to the grace
period); it keeps `C:\ProgramData\swarmscribe-follower`, with the settings, the token and
the credential, so the follower comes back as itself. `--reinstall` is needed: without it uv
answers that the tool `is already installed` and changes nothing. If the Python is to be a
new one, install it first, as in the install above.

### Stopping, and what restarts it

Exit `0` (stopped, or drained), `4` (revoked, or the token was refused) and `5` (protocol)
are final: nothing restarts them. Exits `1` (a bug), `2` (settings) and `3` (this machine
cannot do the work: `doctor` says why) are retried a limited number of times. The systemd
column was run (in a container); the Windows column is what the code does, tested without
Windows' control manager, and **has not been seen under it**.

| | systemd | Windows service |
|---|---|---|
| A stop (`systemctl stop`; `sc.exe stop`, the Services console) | `SIGTERM`. The recording in hand is finished if its estimated time left fits `SWARMSCRIBE_FOLLOWER_SHUTDOWN_GRACE_SECONDS` (900 in both settings files), else handed back with no attempt counted. At `TimeoutStopSec=930` systemd kills what is left | the stop control: the same rule. The service reports "stop pending" with a wait hint of the grace period plus 30 seconds |
| A reboot or shutdown | the same stop, so it can wait as long as the grace period for a recording that fits it (not run) | the pre-shutdown control: the recording is handed back at once, whatever the grace period (not run) |
| Sleep or hibernation (Windows: also a shutdown with Fast Startup) | not a stop: the process is frozen. When the machine wakes the lease has run out, the leader has given the recording to another follower, and this one starts on the next. One counted attempt (by design; not run) | the same |
| Exit `0`: stopped, or drained | left stopped | left stopped |
| Exit `4`, `5` | `failed`; `RestartPreventExitStatus=4 5`: never restarted | left stopped, with the code as the service's exit code (`sc.exe query`); never restarted |
| Exit `2`, `3`, `1` | `Restart=on-failure`, `RestartSec=30`, and at most five starts in ten minutes (`StartLimitBurst=5`, `StartLimitIntervalSec=600`); then `failed` | the process ends without telling Windows, and the recovery actions restart it after a minute, twice (`restart/60000/restart/60000`); then it is left stopped (the reason is in `follower.log`). The count starts again after a day (`reset= 86400`) |
| Killed (out of memory, a crash) | restarted, as above | restarted, as above |

In the recorded Linux run, with the restart delay shortened to 2 s so that the test is quick,
an exit `3` was restarted until systemd said `Start request repeated too quickly` and left
the unit `failed` (`NRestarts=5`).

A drained follower stays stopped across restarts and reboots (it keeps its credential and is
told `drain` again): `leave` and join again to put the machine back to work. A revoked one
needs a new token: put it in `join-token`, delete `state/credential.json` (`state\credential.json`
on Windows), start the service.

### Memory, and the health listener

Under systemd, `MemoryMax=` on the unit is the memory guard's limit (`systemctl edit
swarmscribe-follower`, then `[Service]` and `MemoryMax=8G`): a recording that cannot fit is
refused with a reason instead of being killed half-way. That is cgroup v2, which every
current distribution mounts; on cgroup v1 a unit's limit is not seen, so set
`SWARMSCRIBE_FOLLOWER_MEMORY_LIMIT_MB` in `follower.env`. On Windows the guard counts what
the process holds (its working set) against the machine's total memory; on a PC that is used
for other things, set `SWARMSCRIBE_FOLLOWER_MEMORY_LIMIT_MB` in `follower.env`. "Sizing a
pool" has the figures.

A native install opens no port. For `/healthz` and `/metrics` on the machine itself, set
`SWARMSCRIBE_FOLLOWER_HEALTH_ADDR=127.0.0.1:9108` in `follower.env`.

### What has been run, and what has not

The record is `docs/superpowers/plans/2026-10-05-follower-f4-outcomes.md`. Every figure below
is from it.

- **Linux, in a container.** `e2e/follower-systemd/run_e2e.py` installs the follower as above
  in a container that runs systemd 252 as PID 1 (Debian 12), with `tiny.en` on the CPU, and
  checks, against a real leader with Postgres: it registers as its own user with a private
  state folder, no token in its environment and no listening TCP port; a stop with 900 s of
  grace finishes the recording and one with 1 s hands it back with no attempt counted; started
  again it is the same follower; killed with `SIGKILL` it is restarted and the recording
  redone; `MemoryMax=` refuses an hour-long recording; drained it exits 0 and stays stopped;
  revoked it exits 4 and is not restarted; unable to work it is restarted and then left
  failed. It passed three times; in the latest run, after the final review's fixes, the
  follower registered in 23 s after `systemctl enable --now`, the 900 s stop finished the
  recording in 37 s, the 1 s stop handed it back in 1.0 s, and the whole `run` took 3 min
  34 s. In a separate run of the planner's, in a plain container with the GPU passed in (an
  RTX 4090), `doctor` loaded and ran `tiny.en` on the GPU with no `LD_LIBRARY_PATH`, which the
  engine alone could not (`Library libcublas.so.12 is not found`).
- **Windows, in a console, without administrator rights.** `e2e/follower-windows/run_e2e.py`
  installs the follower with its `cuda` extra on uv's Python, its files copied
  (`UV_LINK_MODE=copy`), in a folder of the user's, and runs the service's own command (the
  real interpreter, isolated, with `service_boot.py`) in a console, on an RTX 4090 with
  `large-v3`. In its latest runs, after the final review's fixes, it passed once on the GPU
  (1 min 17 s) and once on the CPU with `tiny.en` (44 s); the earlier runs are in the record.
  The follower registered (in 5 s on the GPU), the stop control arriving mid-job handed
  the recording back and ended it in 2.4 s with `SERVICE_STOPPED`, it came back as the same
  follower, revocation was reported as a stop with error 4, and a machine that could not do
  the work ended with code 3 and no `SERVICE_STOPPED`. What the service tells Windows and what
  its controls do is also tested on every platform, and the follower's tests are a CI job
  on `windows-latest` (`follower-windows`; **it has not yet run on GitHub**).
- **Not run: the Windows service under the service control manager.** Registering the service,
  the control manager starting it, the service's account reading its settings and writing its
  state, a GPU used from a service session, the stop control arriving from Windows, a
  shutdown and the recovery actions all need an administrator, and wait for the owner's run:
  the procedure is in the record, under "The owner's run (needs an administrator)", and its
  result is not filled in. Until it is, the Windows service is built and tested as far as
  tests reach, and not proven as a service.
- **Not run: the machine-wide install under `C:\Program Files`, and the service account
  reading it.** Every install so far was made without administrator rights, in a folder of
  the user's. That a `UV_LINK_MODE=copy` install under `C:\Program Files` gives files the
  service's account can read and an ordinary user cannot change rests on a measurement
  elsewhere (in a scratch folder, copied files took the folder's inherited list and
  hard-linked ones kept the cache's) and on `service install`'s check of the access lists,
  which on a real install has only been seen to refuse (its tests make it pass, with real
  lists and a stand-in owner). The upgrade orders, on both systems, are not run either.
- **Not run: a real Linux host, a GPU under the systemd unit, a reboot on either system.**
  Starting at boot rests on `WantedBy=multi-user.target` and on the Windows service's start
  type, not on a run. The record lists the rest: other distributions, systemd versions and
  cgroup v1, other Windows versions and GPUs, a leader on TLS, and recordings of people
  talking.

## Develop

```
uv sync
uv run pytest            # unit tests
uv run pytest -m smoke   # downloads tiny.en and runs the real model
uv run ruff check .
```

The web app (Node 24.15 or later in the 24 line; jsdom's dependencies need it, and
`.npmrc` sets `engine-strict`, so `npm ci` refuses an older Node. Upgrade Node
rather than relaxing that):

```
cd packages/console-web
npm ci
npm run typecheck && npm run lint && npm test
npm run build                      # dist/, checked by scripts/check-dist.mjs
npx playwright install chromium    # once
npm run e2e                        # starts e2e/harness/serve.py, then Playwright
```

The end-to-end harness runs the real console on `http://localhost:8900` with an
in-memory Entra ID and two in-memory leaders (`eu-1`, `us-1`), on
`SWARMSCRIBE_TEST_DATABASE_URL` or the local pgserver, and a control server on
`http://127.0.0.1:8901` that only the tests use. `E2E_HARNESS_COMMAND` replaces
the command Playwright starts it with (CI uses `uv run python …`). While working
on the app, `npm run watch` rebuilds `dist/` and `npx playwright test --ui`
drives it; the Vite dev server is not used, because the console's CSP and CSRF
checks apply only to the built app on the console's own origin.

`av` is pinned below 19 in `packages/engine/pyproject.toml`: faster-whisper
1.2.1 passes `metadata_errors=` to `av.open`, which av 19 removed. Lift the
bound when faster-whisper supports av 19.

`deploy/follower-constraints.txt` is `uv.lock`'s versions for a native install of the
follower; a test fails when the two disagree. After a change of `uv.lock`, regenerate it:

```
uv export --frozen --no-dev --no-emit-workspace --package swarmscribe-follower --extra cuda --no-hashes -o deploy/follower-constraints.txt
```

The two native end-to-end tests are run by hand and recorded in
`docs/superpowers/plans/2026-10-05-follower-f4-outcomes.md`: `e2e/follower-systemd/run_e2e.py`
(a container with systemd as PID 1) and `e2e/follower-windows/run_e2e.py` (Windows, with the
GPU).

If you change a wire model, the schema snapshot test fails. Decide whether
`PROTOCOL_VERSION` must change, then regenerate:

```
uv run python -m swarmscribe_protocol.schema packages/protocol/tests/schema_v1.json
```
