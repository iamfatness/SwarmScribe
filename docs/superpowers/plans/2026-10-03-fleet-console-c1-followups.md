# Fleet console C1 (leader side) — carried-forward items

These come from the final review of `fleet-console-leader` (2026-10-03). Neither blocks merge.

## Carried forward

- **For C2: a revoked console that keeps polling writes an audit row every 15 s.** A revoked console's poller
  still gets a `console.refused` row (`{code: revoked}`) on each poll, which is 5,760 rows a day per leader
  (Spec amendment 1). The console must stop polling a leader once it gets 401 "revoked", and show that leader as
  revoked.
- **For later: pools whose followers are all gone stay in `follower_pools` forever.** Follower rows are never
  deleted (only `state="gone"` is set), so a retired pool keeps appearing with `active: 0`. `followers.state` has
  no CHECK constraint; consider adding one alongside follower cleanup, so that an unknown state cannot make
  `followers` differ from the sum of `follower_pools`.

- **Queue age meaning (C1b).** `oldest_queued_age_s` counts from job creation, so a job that went back to the queue (lease expiry, retryable failure) or is held back from claiming keeps its original age. The console should label it "oldest queued job (since created)". The C1b database-clock test cannot tell the two clocks apart on one machine; the guarantee rests on the code using `now()`.
