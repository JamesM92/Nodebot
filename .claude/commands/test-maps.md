Time all three map generators against live data. Use this to check performance after changes to map_gen.py.

```bash
cd /home/penguin/github.com/JamesM92/NodeBot
timeout 60 .venv/bin/python3 -c "
import time, nodebot.map_gen as m
m._storage_path = '/home/penguin/.nodebot/lxmf_storage'
db = '/home/penguin/.nodebot/logs/announces.db'
t = time.time(); n, _ = m.generate(db);               print(f'node map:   {n} nodes  {time.time()-t:.1f}s')
t = time.time(); m.generate_county_map(db);            print(f'county map:          {time.time()-t:.1f}s')
t = time.time(); m.generate_path_map(db);              print(f'path map:            {time.time()-t:.1f}s')
" 2>&1
```

Expected times (Pi 4, ~1800 nodes):
- node map:   < 15 s
- county map: < 15 s
- path map:   < 15 s

Exit code 124 = timeout (>60 s); profile with timing prints to find the slow stage.
