#!/usr/bin/python3
# NomadNet index page — deployed by install_lxmf.sh
# Pass-through redirect: serves nodebot/nodebot.mu as the root page.
#
# To change the landing page, update TARGET below and re-run the installer,
# or edit the deployed copy at ~/.nomadnetwork/storage/pages/index.mu directly.

import subprocess, os, sys

TARGET = "nodebot/nodebot.mu"

page = os.path.join(os.path.dirname(os.path.abspath(__file__)), *TARGET.split("/"))
result = subprocess.run([page], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
sys.stdout.buffer.write(result.stdout)
