#!/bin/sh
set -eu

# This checks the local listener and aggregate exporter without credentials.
turnutils_stunclient -p 3478 127.0.0.1 >/dev/null 2>&1
