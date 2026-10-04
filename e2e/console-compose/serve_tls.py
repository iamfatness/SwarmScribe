"""A real leader, served over TLS, for the console Compose test only.

The console calls leaders over https only, and `swarmscribe-leader serve` speaks plain HTTP
(in production TLS ends at the leader's ingress). There is no ingress here, so this starts
the same application `serve` starts, with the test CA's certificate. docker-compose.yml runs
`swarmscribe-leader migrate` first; nothing here is part of the leader image.

    python /e2e/serve_tls.py
"""

import logging.config

import uvicorn
from swarmscribe_leader.app import create_app
from swarmscribe_leader.config import Settings
from swarmscribe_leader.main import LOGGING

if __name__ == "__main__":
    logging.config.dictConfig(LOGGING)
    uvicorn.run(
        create_app(Settings()),
        host="0.0.0.0",
        port=8443,
        ssl_certfile="/certs/server.pem",
        ssl_keyfile="/certs/server.key",
        proxy_headers=True,
        access_log=False,
        log_config=None,
    )
