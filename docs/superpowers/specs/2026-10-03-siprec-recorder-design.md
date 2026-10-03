# SwarmScribe — SIPREC Recorder Spec

Date: 2026-10-03
Status: Approved design, written for review
Parents: `2026-10-02-swarmscribe-architecture-design.md` (master spec),
`2026-10-02-leader-design.md` (leader spec),
`2026-10-03-channel-split-design.md` (per-channel transcription).

This spec covers SIPREC sub-projects 2 (call controller) and 3 (SIP and
media deployment). Sub-project 1 is the per-channel transcription spec.
Sub-project 4 (live transcription) is out of scope; this design only keeps
its hook.

## 1. Purpose

Record live calls forked to SwarmScribe over SIPREC (RFC 7865/7866) by
session border controllers and cloud contact-centre / UCaaS platforms, store
each call as one stereo file (one party left, the other right), and
transcribe it per channel with speaker roles — while refusing, before any
audio flows, every call that no consent rule allows.

### Success criteria

- A call that matches no enabled call rule is refused at the SIP level; no
  media is ever received for it.
- Every accepted call ends as a stored stereo recording with a transcription
  job, or as a call record marked `failed` with a reason.
- Transcripts label speakers by role (e.g. `Agent:` / `Customer:`), never by
  name or number.
- Signalling is SIP over TLS and media is SRTP; plain SIP or RTP is refused.
- The shape scales horizontally to 100,000 calls a day (about 1,500–2,000
  concurrent at peak) by adding media nodes and controller replicas, with no
  redesign.
- The same software runs in our Kubernetes cluster and as an on-premises
  deployment beside a customer's SBCs.

### Out of scope

Live transcription (sub-project 4); per-call consent withdrawal; calls with
more than two parties recorded as separate channels (extra parties are
mixed into the side their stream is assigned to, see 6.3); video; DTMF; load
testing beyond a functional CI test (a later step).

## 2. Decisions

| Topic | Decision |
|---|---|
| Purpose | Record now; live transcription later through rtpengine's media forwarding |
| Senders | SBCs (Cisco CUBE, Oracle, AudioCodes, Ribbon…) and cloud contact centres / UCaaS |
| Consent | Admin-maintained allowlist rules matched on call metadata; unmatched calls refused at SIP |
| Scale target | 100,000 calls/day without re-architecture |
| Audio layout | One stereo file: one party left, the other right |
| Placement | Our Kubernetes cluster, and optionally on-premises per customer |
| Transport security | SIP over TLS and SRTP mandatory |
| Recording | rtpengine (≥ 26.2, dfx.at packages) kernel-module recording on dedicated media nodes |
| SIP edge | Kamailio; all call logic in our Python call controller |
| Consent owner | The leader: rules, call records and job creation live there |
| Speaker labels | Roles defined by the matching rule |
| Metadata kept | Operational minimum (section 5.2) |
| Storage format | FLAC by default; Opus 32 kbps per rule |

Verified facts from the rtpengine spike (2026-10-03, rtpengine 26.2):
`proc` recording with `--output-mixed --mix-method=channels
--mix-num-inputs=2` writes one time-aligned stereo WAV, channel order
following stream creation order; per-participant files drop silence and must
not be used to build stereo; `proc` recording requires the kernel module;
`publish` accepts a two-stream SIPREC offer and sends nothing back;
rtpengine does not parse `rs-metadata`; the recording daemon's
`--notify-uri` reports each finished file with the call's `metadata`.

## 3. Components

```
 SBC / platform ──SIP/TLS──► Kamailio edge ──HTTP──► Call controller ──HTTPS──► Leader
        │                                               │  NG protocol
        └──────────SRTP───────────────────────────────► Media node: rtpengine (kernel)
                                                         │ recording daemon → stereo WAV
                                                         └─► Media agent ──► storage + leader
```

| Component | Package / image | Runs as | Responsibility |
|---|---|---|---|
| Kamailio edge | `swarmscribe-sip-edge` (Kamailio config + image) | Kubernetes Deployment behind a TCP load balancer on 5061, or on-prem | SIP over TLS (optionally mutual TLS per sender), transactions, retransmissions, OPTIONS keep-alive, routing every SIPREC request of a call to the same controller instance (dispatcher hashed on Call-ID) |
| Call controller | `swarmscribe-callrec` (Python, FastAPI) | Ordinary pods, scaled on call rate | Parse SIPREC bodies, ask the leader for a decision, drive rtpengine over NG, return the answer SDP |
| Media node | `swarmscribe-media` image: rtpengine + recording daemon + media agent | Dedicated node pool (kernel module, `hostNetwork`, privileged, public IPs) or an on-prem appliance | Receive SRTP, write the stereo WAV, compress, upload, report completion |
| Leader | existing | existing | Call rules, call records, consent decisions, recording and job creation |

Every component is horizontally scalable: Kamailio and controllers are
stateless apart from in-flight transactions; media capacity grows by adding
media nodes.

## 4. Call flow

1. **INVITE.** The sender forks a call: a SIP INVITE over TLS with a
   `multipart/mixed` body carrying SDP (one labelled `sendonly` m-line per
   participant stream, SDES-SRTP keys) and `application/rs-metadata+xml`.
   Kamailio verifies TLS (and the client certificate where configured),
   then POSTs to a controller `/v1/sip/invite` with: Call-ID, From/To,
   the sender identity (client-certificate subject or the source address of
   an allowlisted sender), the SDP and the metadata XML.
2. **Parse.** The controller parses `rs-metadata`: participants with their
   identifiers (AOR/URI/number), streams and their `label`s, and the
   participant-to-stream mapping. Malformed bodies → `400` to the sender.
3. **Decide.** The controller calls the leader
   `POST /v1/internal/calls/authorize` with the sender, direction, the
   participants' identifiers and any queue/extension fields the rules
   reference. The leader evaluates enabled rules in priority order:
   - no match → `{allowed: false}`; the controller answers `403
     Forbidden`, Kamailio sends it, and no media session is created;
   - match → the leader creates a `calls` row (state `authorized`) and
     returns `{allowed: true, call_id, rule_id, location_id, left_role,
     right_role, left_selector, format}`.
4. **Set up media.** The controller chooses a media node (section 6.2) and
   sends rtpengine `publish` with both streams, the **left** participant's
   stream first (channel order follows creation order), flags
   `record call=yes`, SDES-SRTP required, and `metadata=<call_id>`. It
   returns the resulting SDP (`recvonly` per stream) to Kamailio, which
   answers `200 OK`. The leader's call row moves to `recording` with the
   media node and rtpengine call-id stored.
5. **During the call.** Re-INVITEs (hold, codec or address changes) go to
   the same controller (`/v1/sip/update`), which updates rtpengine; stream
   order is preserved. Kamailio answers OPTIONS itself.
6. **End.** BYE → controller `/v1/sip/bye` → rtpengine `delete`. If no BYE
   arrives, rtpengine's media timeout (60 s without packets) ends the call.
7. **Finalise.** The recording daemon writes the stereo WAV and POSTs to the
   media agent on the same node (`--notify-uri` on localhost) with the call
   metadata. The agent:
   - compresses to the rule's format (FLAC, or Opus 32 kbps stereo);
   - asks the leader `POST /v1/internal/calls/{id}/upload-link` for a link
     bound to that call;
   - uploads, computes the SHA-256, and calls
     `POST /v1/internal/calls/{id}/complete {sha256, duration_s, size}`;
   - deletes the local file only after the leader confirms.
8. **Job.** On `complete`, the leader creates a `recordings` row in the
   call's location (consent `consented`, `consent_pattern` = `rule:<id>`)
   and a job with `channel_mode=stereo_split` and
   `channel_labels=[left_role, right_role]`. The call row moves to
   `stored` and links the recording.

## 5. Leader changes

### 5.1 Call rules

Table `call_rules`:

| Column | Meaning |
|---|---|
| `name` | unique |
| `priority` | evaluation order, highest first |
| `enabled` | |
| `sender` | sender identity (certificate subject or configured sender name) the rule applies to |
| `direction` | `inbound`, `outbound` or `any` |
| `match_numbers` | optional glob list matched against any participant's number or URI |
| `match_fields` | optional map of metadata field → glob (e.g. queue, extension), from fields the sender is configured to pass |
| `location_id` | the calls location recordings go to |
| `left_selector` | which participant goes on the left: `internal` (the participant matching the sender's configured internal number patterns), `caller`, or `callee` |
| `left_role`, `right_role` | labels, 1–40 characters each (e.g. `Agent`, `Customer`) |
| `format` | `flac` or `opus` |

Rules are evaluated on every INVITE (about one per second on average at
100,000 calls a day). A rule change applies to new calls only.

### 5.2 Call records

Table `calls`: `id`, `sender`, `sip_call_id`, `rule_id`, `location_id`,
`direction`, `participants` (list of `{role, identifier}` — number or URI
only), `started_at`, `ended_at`, `duration_s`, `media_node`,
`rtpengine_call_id`, `state` (`authorized` → `recording` → `ended` →
`stored`, or `failed`), `failure_reason`, `recording_id`, `live` (reserved
for sub-project 4, always false). Nothing else from `rs-metadata` is stored.

*Roadmap note (2026-10-03):* search and indexing by agent, group and queue
(roadmap item 8) need business metadata per call. Call rules will therefore be
able to name extra fields to keep — from `rs-metadata` extensions or sender
headers (for example agent ID, queue, group) — stored in a `calls.fields`
map. Phone numbers and names stay out of transcripts either way. This is
designed with roadmap item 8, not in S1.

### 5.3 Calls locations

`storage_locations` gains `source` (`files` default, or `calls`). The scanner
skips `calls` locations: their recordings enter only through the call path.
Recording keys are `<yyyy>/<mm>/<dd>/<call id>.<flac|opus>`.

### 5.4 Two consent-gated paths to a job

The master spec's rule becomes: a job is created only (a) by the scanner for
a recording matched by its location's `consent.txt`, or (b) by the call path
for a call matched by an enabled call rule. No other code creates jobs.

### 5.5 Internal API and service credentials

`/v1/internal/*` is for controllers and media agents only. They authenticate
with service credentials: created by an admin
(`swarmscribe-admin services create --kind controller|media-agent`), shown
once, stored hashed, revocable — separate from follower credentials and
admin sign-in. Each call endpoint checks the caller's kind (only
controllers authorize and update calls; only media agents request upload
links and complete calls) and that the call is in the expected state.

### 5.6 Admin commands

`call-rules add|list|update|disable`, `calls list [--state] [--since]`,
`calls show <id>`, `services create|list|revoke`. Viewer may list; operator
may enable/disable rules; admin may add/update rules and manage services.

## 6. Media layer

### 6.1 rtpengine configuration

rtpengine ≥ 26.2 from the dfx.at repository, kernel module via DKMS on the
media node image; recording daemon with `--recording-method=proc
--output-mixed --mix-method=channels --mix-num-inputs=2 --output-format=wav
--notify-uri=http://127.0.0.1:<agent port>/notify --notify-post`; SDES-SRTP
required on publish; port range sized for the node's call capacity (four
ports per call); `--timeout=60`.

### 6.2 Choosing a media node

Controllers keep a list of media nodes (from configuration, or Kubernetes
endpoints) and choose the node with the fewest active calls recorded in the
leader (`calls` in state `recording` per `media_node`), skipping nodes that
fail an NG `ping`. On-prem deployments list their local nodes.

### 6.3 More than two participants

SIPREC can carry more than two streams (conference, transfer). The rule's
`left_selector` picks one participant for the left; every other stream is
assigned to the right side. Mixing more than one stream onto one side is a
sub-project 3 verification item: if the recording daemon cannot do it with
`--mix-num-inputs=2`, such calls are recorded with only the first stream per
side and the call record notes the dropped streams.

### 6.4 Media agent

A small Python service on each media node, listening on localhost only. It
compresses (PyAV), uploads with retry and back-off, survives restarts by
scanning its spool directory for finished files with no confirmation, and
deletes local files only after the leader confirms `complete`.

## 7. Failure handling

| Failure | Result |
|---|---|
| No rule matches | `403` at INVITE; no media; no call row |
| Leader unreachable at INVITE | `503` with `Retry-After`; no media (fail closed) |
| Controller crashes mid-call | rtpengine keeps recording; Kamailio routes the BYE to another controller, which loads the call from the leader by SIP Call-ID and deletes it in rtpengine |
| Sender never sends BYE | rtpengine media timeout ends the call; finalisation runs |
| Media node crashes mid-call | That call's audio is lost; the call is marked `failed` (`media node lost`) when the controller or leader detects the node is gone |
| Upload or `complete` fails | The media agent retries from its spool; the call stays `ended` until stored |
| Recording never arrives | The leader marks calls `failed` (`no recording`) 15 minutes after `ended` |
| Plain SIP or RTP offered | Kamailio refuses non-TLS; the controller refuses non-SRTP offers with `488` |

## 8. Deployment

- **Kubernetes:** Kamailio as a Deployment behind a TCP LoadBalancer on 5061
  (TLS certificate from cert-manager, per-sender client CA bundle);
  controllers as a Deployment; a `media` node pool with the kernel module in
  the node image, running the media DaemonSet with `hostNetwork`,
  privileged, and each node's public IP advertised to rtpengine; UDP port
  range open in the node security group.
- **On-premises:** the same three images on a Linux host (or VM appliance)
  with the kernel module, configured to reach the central leader over HTTPS.
  Recordings upload to the customer's configured location.
- Media nodes are sized by a later load test; the shape needs only more
  nodes for more calls.

## 9. Security

- TLS 1.2+ for SIP; mutual TLS per sender where the sender supports it,
  otherwise an allowlist of sender source addresses. SRTP (SDES) required;
  DTLS-SRTP offers are refused until verified.
- SDES keys are passed to rtpengine and never logged or stored.
- Controllers and media agents hold only their service credential; they
  never see database credentials. Upload links are bound to one call.
- Logs carry call ids and rule names, never phone numbers, URIs, SDP keys or
  links.

## 10. Live transcription hook (sub-project 4)

`calls.live` is reserved. Later, for calls whose rule enables live
transcription, the controller will start rtpengine media forwarding
(verified: `start forwarding` with the recording daemon's per-participant
PCM over TCP, or `subscribe request`) toward a live-transcription service.
Nothing in this spec builds it.

## 11. Testing

- **Controller unit tests:** `rs-metadata` parsing (RFC 7865 examples and
  vendor samples), left/right assignment by selector, decisions mapped to SIP
  responses, NG messages sent to a fake rtpengine, fail-closed on leader
  errors.
- **Leader:** rule evaluation and priority, call state machine, internal API
  authentication and per-kind authorisation, job creation with channel
  settings, the scanner skipping `calls` locations, `failed` after no
  recording.
- **Media agent:** spool recovery after restart, retry, delete-after-confirm,
  FLAC/Opus output stereo and duration.
- **CI integration (GitHub Actions):** Kamailio + controller + leader +
  rtpengine with the kernel module + SIPp sending SDES-SRTP SIPREC INVITEs
  with two known tones; asserts a refused call creates nothing, an allowed
  call produces a stereo FLAC with the right tone on each side and a job with
  the rule's roles.

## 12. Build plans

1. **S1 — Leader:** call rules, call records, `calls` locations, internal API
   and service credentials, admin commands (after Plan A2, which provides
   admin sign-in and the CLI).
2. **Per-channel transcription plan** (sub-project 1).
3. **S2 — Call controller and media agent.**
4. **S3 — Kamailio and media-node images, Kubernetes and on-prem
   deployment, CI integration test.**
