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

Pushes to `main` publish `latest` and an immutable UTC timestamp tag such as
`20260831-192054`. Seconds keep multiple builds in one day distinct while the
tag stays short and sortable. Pull requests build without pushing. The GHCR
package is **public**, so the Sandbox service imports it without credentials.

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

- Python 3.10 or later
- Azure CLI authenticated with `az login`
- Permission to create resource groups and role assignments
- Fine-grained GitHub PAT with the **Copilot Requests** account permission

Create `.env` from `.env.sample` and set `COPILOT_GITHUB_TOKEN` to a token
starting with `github_pat_`. The `.env` file is ignored by Git.

```text
COPILOT_GITHUB_TOKEN=github_pat_...
```

Deploy:

```console
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/deploy.py
```

On Windows PowerShell:

```console
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe scripts\deploy.py
```

The deployment uses the official `azure-containerapps-sandbox` Python SDK.
The preview `aca` CLI is needed only for the interactive shell command below.

Defaults create:

- resource group `rg-copilot-sandbox` in Sweden Central
- sandbox group `copilot-sandbox-group`
- public custom disk from `ghcr.io/tkubica12/aca-sandbox-copilot:latest`
- 1 GiB persistent DataDisk mounted at `/mnt/data`
- sandbox `copilot-cli` with the GitHub Copilot credential attached

Change values in `.env` to override these defaults or select an immutable
timestamp image tag.

## Test

Run `.venv/bin/python scripts/test.py` on macOS/Linux or
`.venv\Scripts\python.exe scripts\test.py` on Windows.

The test checks the installed CLIs, tmux, Copilot authentication, and DataDisk
persistence across sandbox replacement.

## Connect

```console
aca --resource-group rg-copilot-sandbox --region swedencentral sandbox shell --group copilot-sandbox-group --selector name=copilot-cli --command "tmux attach -t copilot"
```

Detach with `Ctrl-b`, then `d`. Reconnect with the same command.

## Clean up

Run `.venv/bin/python scripts/cleanup.py` on macOS/Linux or
`.venv\Scripts\python.exe scripts\cleanup.py` on Windows.

`DataDisk` is single-writer, full-POSIX storage and fits durable agent
workspaces. It requires disk-mode auto-suspend, which the deployment configures.
The sandbox uses a 20 GiB root disk, leaving disk-budget headroom for the
1 GiB data disk at 2000m CPU.

## References

- [ACA Sandboxes CLI quickstart](https://learn.microsoft.com/azure/container-apps/sandboxes-quickstart-cli)
- [Sandbox disk images](https://sandboxes.azure.com/docs/sandboxes/disk-images)
- [Sandbox volumes](https://sandboxes.azure.com/docs/sandboxes/volumes)
- [Install GitHub Copilot CLI](https://docs.github.com/copilot/how-tos/copilot-cli/set-up-copilot-cli/install-copilot-cli)