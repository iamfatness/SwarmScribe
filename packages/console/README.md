# swarmscribe-console

The SwarmScribe fleet console. C2a is the console core: sign-in, sessions, role grants and
the leader registry.

## Deployment notes

### Egress policy for the poller (C4)

The console refuses leader URLs that are not https, that carry user info, a query or a
fragment, or whose host is loopback, link-local, unspecified, multicast, reserved, a
`localhost` name or a numeric spelling of an address. Private ranges are allowed, because
leaders are usually internal. It also refuses two cloud metadata addresses that those range
rules do not cover, by exact address:

- `fd00:ec2::254` (AWS IPv6 metadata)
- `100.100.100.200` (Alibaba Cloud metadata)

These checks are on the registered text only. A DNS name can still resolve to any address,
so C4's network egress policy must deny the console's traffic to link-local and metadata
addresses (`169.254.169.254`, `fd00:ec2::254`, `100.100.100.200`) and to loopback, and
should allow only the leaders' networks.
