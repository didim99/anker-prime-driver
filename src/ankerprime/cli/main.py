"""Command-line entry point."""

import argparse

from ankerprime import __version__


def main(argv: list[str] | None = None) -> int:
    """Run the command-line interface and return the process exit code."""

    parser = argparse.ArgumentParser(
        prog='ankerprime',
        description='Monitor and control Anker Prime chargers.')
    parser.add_argument('--version', action='version',
                        version=f'%(prog)s {__version__}')
    parser.parse_args(argv)
    parser.print_help()
    return 0
