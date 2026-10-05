import logging

FOLLOWER_VERSION = "0.1.0"

# httpx logs every request's URL at INFO, and a link's URL carries its secret. These loggers
# are raised to WARNING as soon as the package is imported, not only when the command line
# configures logging, so that no way of using the follower can log a link (follower spec 7).
for _name in ("httpx", "httpcore"):
    logging.getLogger(_name).setLevel(logging.WARNING)
