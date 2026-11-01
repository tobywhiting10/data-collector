#!/bin/sh
# Start as root only to fix ownership of the mounted /data folder (bind mounts are usually root-owned),
# then drop to the unprivileged user for the app.
mkdir -p "$(dirname "$DB_PATH")"
chown -R bussage:bussage "$(dirname "$DB_PATH")"
exec setpriv --reuid=bussage --regid=bussage --init-groups "$@"
