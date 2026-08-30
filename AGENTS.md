# AGENTS.md

- Goal: Copilot CLI inside Azure Container Apps Sandboxes. Not ACA apps. Not dynamic sessions.
- Portal: `https://sandboxes.azure.com`.
- Sandbox control plane: preview `aca` CLI. Azure auth: `az login`.
- Image: `image/Dockerfile`. Ubuntu 24.04. amd64.
- Runtime user: root. Workdir/data: `/mnt/data`.
- Tmux session: `copilot`. Join: `tmux attach -t copilot`.
- Tmux boot: entrypoint + systemd + profile fallback. Keep all three.
- Storage: `DataDisk`. POSIX. One sandbox writer. Mount `/mnt/data`.
- Registry: `ghcr.io/tkubica12/aca-sandbox-copilot`. Must be public for no-auth import.
- CI: `.github/workflows/image.yml`. PR builds. Main/manual pushes GHCR.
- Deploy: `scripts/deploy.sh`. Test: `scripts/test-sandbox.sh`. Cleanup: `scripts/cleanup.sh`.
- Scripts: Bash, strict mode, rerunnable where sane. No secrets in files/output.
- Docs source: `sandboxes.azure.com/docs`. Product preview; CLI may drift.
- Validate: image build, CLI versions, tmux reconnect, volume survives sandbox replacement.

