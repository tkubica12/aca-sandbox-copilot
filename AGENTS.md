# AGENTS.md

- Goal: Copilot CLI inside Azure Container Apps Sandboxes. Not ACA apps. Not dynamic sessions.
- Portal: `https://sandboxes.azure.com`.
- Sandbox control plane: preview `aca` CLI. Azure auth: `az login`.
- Image: `image/Dockerfile`. Ubuntu 24.04. amd64.
- Runtime user: root. Workdir/data: `/mnt/data`.
- Tmux session: `copilot`. Join: `tmux attach -t copilot`.
- Tmux boot: entrypoint + systemd + profile fallback. Keep all three.
- Storage: `DataDisk`, default `1Gi`. POSIX. One writer. Needs disk suspend mode.
- Sandbox: `2000m`, `4096Mi`, 20 GiB root. YAML apply. Leave data-disk headroom.
- Registry: `ghcr.io/tkubica12/aca-sandbox-copilot`. Public only. No pull secrets.
- CI: `.github/workflows/image.yml`. PR builds. Main/manual pushes GHCR.
- Deploy: `scripts/deploy.sh`. Test: `scripts/test-sandbox.sh`. Cleanup: `scripts/cleanup.sh`.
- Scripts: Bash, strict mode, rerunnable where sane. No secrets in files/output.
- Docs source: `sandboxes.azure.com/docs`. Product preview; CLI may drift.
- Validate: image build, CLI versions, tmux reconnect, volume survives sandbox replacement.
- Live test 2026-08-30 westus2: all passed. GHCR anonymous pull passed.
