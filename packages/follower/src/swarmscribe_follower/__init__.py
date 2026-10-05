FOLLOWER_VERSION = "0.1.0"

# Importing this package changes nothing outside it: no logger's level, no handler. Logging is
# configured in one place, `logs.configure_logging`, which only the command line calls.
