#!/usr/bin/env bash
# Restart the ForgeSRE MCP server (e.g. after editing config/*.yaml).
source "$(dirname "$0")/lib.sh"
stop_mcp
start_mcp
