"""Command line for maintainers: `python -m live_fixture provision|teardown`."""

import argparse
import json

from . import provision, teardown

parser = argparse.ArgumentParser(prog="python -m live_fixture", description=__doc__)
commands = parser.add_subparsers(dest="command", required=True)
create = commands.add_parser("provision", help="create a new fixture (billable Azure resources)")
create.add_argument("--ticket", required=True, help="label used in the resource group name rg-are-<ticket>-<random>")
create.add_argument("--location", default="swedencentral")
create.add_argument("--subscription", default="Sandbox", help="subscription name; nothing is created elsewhere")
remove = commands.add_parser("teardown", help="delete a fixture's resource group and confirm its absence")
remove.add_argument("resource_group")
remove.add_argument("--subscription", default="Sandbox")
arguments = parser.parse_args()

if arguments.command == "provision":
    fixture = provision(arguments.ticket, arguments.location, arguments.subscription)
    print(json.dumps(fixture.summary(), indent=2))
else:
    teardown(arguments.resource_group, arguments.subscription)