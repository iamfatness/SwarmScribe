# SwarmScribe roadmap

Ordered list of capabilities beyond the current build. Each becomes its own
spec, plan and build. Agreed with the owner 2026-10-03, informed by a
review of a commercial call-recording product's feature list.

## In progress / designed

1. Leader Plan A2 — admin sign-in (Entra ID, Google), admin API and CLI,
   multi-replica test.
2. Per-channel transcription (`2026-10-03-channel-split-design.md`).
3. SIPREC recorder S1–S3 (`2026-10-03-siprec-recorder-design.md`).
4. Follower agent and container images.
5. Plan B — Azure/GCS storage, vocabulary versions and report, metrics.
6. Helm chart and autoscaling. *The leader's chart is built
   (`2026-10-05-leader-chart-design.md`); with the console's and the
   follower's, every part now has one. Autoscaling of follower pools is
   open: it needs the queue-depth metric of item 5.*

## Added 2026-10-03

7. **Redaction.** Detect sensitive data (payment card numbers, government
   IDs, personal details) in transcripts, redact it there, and silence the
   matching audio spans in stored recordings; record what was redacted and
   by which rule. Runs as part of finishing a job, before results are
   visible. Must keep word timings so audio spans can be silenced exactly.
8. **Search, tagging and indexing.** Full-text search over transcripts and
   call metadata; keyword/phrase rules that tag calls automatically; index
   by agent, group, queue and date. Requires per-call business metadata
   (agent, queue, group, CRM identifiers): the SIPREC recorder's metadata
   decision is widened accordingly — rules may name extra fields to keep
   (see the SIPREC spec, section 5.2 note).
9. **Live monitoring and compliance alerts.** Live transcription during calls
   through the reserved live hook (rtpengine media forwarding), live
   listen-in for authorised users, and compliance phrase rules (required
   disclosures, prohibited or abusive language) that alert in real time and
   flag calls afterwards.
10. **Web dashboard, playback and sharing.** A browser interface: concurrent
    and peak calls, volume and summary statistics, recent calls, playback
    with a synced, speaker-labelled transcript, and time-limited, audited
    share links. Reverses the master spec's "no web dashboard" non-goal for
    the release that ships it.

Order of 7–10 to be set when the current work lands; redaction should
precede anything that widens who can see transcripts (search, playback,
sharing).
