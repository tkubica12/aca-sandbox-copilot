# GitHub Copilot CLI on Azure Container Apps Sandboxes

Rich Ubuntu image for running GitHub Copilot CLI in **Azure Container Apps
Sandboxes** at [sandboxes.azure.com](https://sandboxes.azure.com). This is not
regular Azure Container Apps and not dynamic sessions.

The image includes:

- GitHub Copilot CLI and GitHub CLI
- Azure CLI and the preview `aca` Sandboxes CLI
- Terraform
- Bash, Python, pip, uv, Node.js
- jq, yq, ripgrep, fd, git, SSH, networking and build utilities
- tmux with a persistent `copilot` session rooted at `/mnt/data`

## Image

GitHub Actions builds `image/Dockerfile` and publishes:

```text
ghcr.io/tkubica12/aca-sandbox-copilot:latest
```

Pushes to `main` publish `latest`, branch, and commit-SHA tags. Pull requests
build without pushing. The GHCR package is **public**, so the Sandbox service
imports it without registry credentials.

Run locally:

```bash
docker build -t copilot-sandbox image
docker run --rm -d --name copilot-sandbox \
  -v copilot-data:/mnt/data copilot-sandbox
docker exec -it copilot-sandbox tmux attach -t copilot
```

Detach without stopping Copilot: `Ctrl-b`, then `d`. Reconnect with the same
`docker exec` command.

## Deploy to Azure Container Apps Sandboxes

Prerequisites:

- Azure subscription and `az login`
- preview [`aca` CLI](https://learn.microsoft.com/azure/container-apps/sandboxes-quickstart-cli)
- `Container Apps SandboxGroup Data Owner` permission
- `jq`
- public GHCR image produced by the workflow

Install `aca` on Linux:

```bash
curl -fsSL https://aka.ms/aca-cli-install | sh
```

Provision a resource group, sandbox group, custom disk image, persistent
`DataDisk`, and sandbox:

```bash
chmod +x scripts/*.sh
./scripts/deploy.sh
```

Defaults can be overridden:

```bash
RESOURCE_GROUP=my-rg \
LOCATION=westus2 \
SANDBOX_GROUP=my-group \
VOLUME_SIZE=1Gi \
SANDBOX_CPU=2000m \
SANDBOX_MEMORY=4096Mi \
SANDBOX_DISK=20480Mi \
IMAGE=ghcr.io/my-org/my-image:tag \
./scripts/deploy.sh
```

The deployment writes non-secret resource identifiers to ignored
`.sandbox.env`.

## Test

The test checks all installed CLIs, verifies the `copilot` tmux session, writes
to `/mnt/data`, replaces the sandbox, remounts the same volume, verifies the
file survived, and leaves a detachable tmux window running.

```bash
./scripts/test-sandbox.sh
```

Join the session:

```bash
aca sandbox shell -l name=copilot-cli -c "tmux attach -t copilot"
```

You can also open **Interactive shell** in
[sandboxes.azure.com](https://sandboxes.azure.com), then run
`tmux attach -t copilot`. Authenticate Copilot with `/login`. Never bake a token
into the image.

## Clean up

```bash
./scripts/cleanup.sh
```

`DataDisk` is single-writer, full-POSIX storage and fits durable agent
workspaces. It requires disk-mode auto-suspend, which the scripts configure.
The declarative sandbox manifest sets a 20 GiB root disk, leaving disk-budget
headroom for the 1 GiB data disk at 2000m CPU. Deleting the volume permanently
deletes its data.

## References

- [ACA Sandboxes CLI quickstart](https://learn.microsoft.com/azure/container-apps/sandboxes-quickstart-cli)
- [Sandbox disk images](https://sandboxes.azure.com/docs/sandboxes/disk-images)
- [Sandbox volumes](https://sandboxes.azure.com/docs/sandboxes/volumes)
- [Install GitHub Copilot CLI](https://docs.github.com/copilot/how-tos/copilot-cli/set-up-copilot-cli/install-copilot-cli)