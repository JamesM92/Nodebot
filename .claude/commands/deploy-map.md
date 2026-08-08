Deploy map.mu, map_paths.mu, and county.mu templates to the live NomadNet pages directory, substituting PROJECT_DIR_PLACEHOLDER.

```bash
PROJECT_DIR=/home/penguin/github.com/JamesM92/NodeBot
for page in map map_paths county digest; do
  sed "s|PROJECT_DIR_PLACEHOLDER|$PROJECT_DIR|g" \
    "$PROJECT_DIR/installer/lxmf_pages/nodebot/${page}.mu" \
    > ~/.nomadnetwork/storage/pages/nodebot/${page}.mu
  chmod +x ~/.nomadnetwork/storage/pages/nodebot/${page}.mu
  echo "deployed ${page}.mu"
done
```
