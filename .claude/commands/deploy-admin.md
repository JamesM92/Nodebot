Deploy admin.mu template to the live NomadNet pages directory, substituting PROJECT_DIR_PLACEHOLDER.

```bash
PROJECT_DIR=/home/penguin/github.com/JamesM92/NodeBot
sed "s|PROJECT_DIR_PLACEHOLDER|$PROJECT_DIR|g" \
  "$PROJECT_DIR/installer/lxmf_pages/nodebot/admin.mu" \
  > ~/.nomadnetwork/storage/pages/nodebot/admin.mu
chmod +x ~/.nomadnetwork/storage/pages/nodebot/admin.mu
echo "deployed admin.mu"
```
