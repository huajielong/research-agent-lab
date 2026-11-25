"""Keep diagnostic output from failing a workflow on Windows GBK streams."""
import sys


def configure_console(*streams):
    # Preserve the terminal's encoding. Characters it cannot represent are
    # escaped in logs; report text and database contents remain unchanged.
    for stream in streams or (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, 'reconfigure', None)
        if reconfigure is not None:
            reconfigure(errors='backslashreplace')
