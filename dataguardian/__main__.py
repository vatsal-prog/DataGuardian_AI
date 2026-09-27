"""Start the dashboard, or forward a subcommand to the CLI."""

from __future__ import annotations

import sys

from dataguardian.cli import main

if len(sys.argv) == 1:
    sys.argv.append("serve")

main()
