#!/bin/sh
# Called by git (GIT_ASKPASS) and ssh (SSH_ASKPASS) inside the sandbox.
exec python3 /app/share/qdvc-spork/qdvc_spork.py --askpass "$@"
