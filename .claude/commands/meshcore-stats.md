# meshcore-stats

Show MeshCore adapter status: recent stats polls, received messages, reboots, and noise floor.
Use this whenever the user asks "stats?", "status?", "update?", or "how is meshcore doing?".

```bash
# Last hour of meaningful meshcore events (no keepalive/boilerplate noise)
journalctl -u nodebot --since "1 hour ago" --no-pager 2>&1 \
  | grep "meshcore_adapter" \
  | grep -v "keepalive\|channel \|GPS\|tuning\|contacts\|backfilled\|imported\|listening\|node info\|self_info\|channel decrypt\|node name\|worker\|transport\|started\|running\|advert-poll\|announced"
```

After running, report:
- **Uptime**: how long the current process has been running (no restarts = healthy)
- **recv**: current total and delta per poll — climbing = radio is receiving
- **noise**: -118 to -120 = excellent; -107 to -110 = borderline; -100 to -105 = AGC lockup
- **Reboots**: any since last check
- **Messages**: any channel messages received (rflog/push lines)
- **Lockup timer resets**: "quiet network, no reboot" fires = code working correctly
