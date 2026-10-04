# swarmscribe-console

The SwarmScribe fleet console. C2a is the console core: sign-in, sessions, role grants and
the leader registry.

Configuration, the bootstrap commands for each sign-in provider, the grant and registry rules
and the egress note (the single list of the leader URLs the console refuses) are in the
repository's top-level README, section "Run the fleet console (development)".

## Deployment: database connections

The engine's pool is sized from `SWARMSCRIBE_CONSOLE_POLL_CONCURRENCY` (default 8): `pool_size`
is `2 * concurrency + 2` (each poll in flight holds its leader's advisory-lock connection and
takes a second to read and record; the hourly prune holds two), and `max_overflow` is 10 for
web requests. At the default that is 18 pooled connections and up to 28 per replica, so size
the database's `max_connections` for the replicas times that.
