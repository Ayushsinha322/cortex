"""cortex - your filesystem as a brain."""

import logging

__version__ = "0.1.0"

# Quiet unless the command line sets up somewhere for it to go: the server
# logs the errors it would otherwise print over an editor running in the
# foreground of the same terminal.
logging.getLogger("cortex").addHandler(logging.NullHandler())
