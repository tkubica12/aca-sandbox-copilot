# syntax=docker/dockerfile:1
FROM ubuntu:24.04

ARG TARGETARCH
ARG NODE_MAJOR=22
ARG COPILOT_VERSION=1.0.81-8
ARG YQ_VERSION=4.47.2

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PIP_BREAK_SYSTEM_PACKAGES=1 \
    UV_INSTALL_DIR=/usr/local/bin

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        apt-transport-https \
        bash \
        build-essential \
        ca-certificates \
        curl \
        dnsutils \
        fd-find \
        file \
        git \
        gnupg \
        iproute2 \
        iputils-ping \
        jq \
        less \
        locales \
        lsof \
        nano \
        netcat-openbsd \
        openssh-client \
        procps \
        ripgrep \
        rsync \
        software-properties-common \
        sudo \
        tmux \
        unzip \
        vim \
        wget \
        zip \
    && rm -rf /var/lib/apt/lists/*

# Node.js 22+ is required by GitHub Copilot CLI.
RUN install -d -m 0755 /etc/apt/keyrings \
    && curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
        | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg \
    && echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_${NODE_MAJOR}.x nodistro main" \
        > /etc/apt/sources.list.d/nodesource.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install --global "@github/copilot@${COPILOT_VERSION}" \
    && npm cache clean --force \
    && rm -rf /var/lib/apt/lists/*

# GitHub CLI, Azure CLI, and Terraform from vendor repositories.
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        -o /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        > /etc/apt/sources.list.d/github-cli.list \
    && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc \
        | gpg --dearmor -o /etc/apt/keyrings/microsoft.gpg \
    && chmod go+r /etc/apt/keyrings/microsoft.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/microsoft.gpg] https://packages.microsoft.com/repos/azure-cli/ noble main" \
        > /etc/apt/sources.list.d/azure-cli.list \
    && curl -fsSL https://apt.releases.hashicorp.com/gpg \
        | gpg --dearmor -o /etc/apt/keyrings/hashicorp.gpg \
    && chmod go+r /etc/apt/keyrings/hashicorp.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/hashicorp.gpg] https://apt.releases.hashicorp.com noble main" \
        > /etc/apt/sources.list.d/hashicorp.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends azure-cli gh terraform \
    && rm -rf /var/lib/apt/lists/*

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-pip python3-venv \
    && ln -s /usr/bin/python3 /usr/local/bin/python \
    && ln -s /usr/bin/fdfind /usr/local/bin/fd \
    && curl -LsSf https://astral.sh/uv/install.sh | sh \
    && case "$TARGETARCH" in \
         amd64) yq_arch=amd64 ;; \
         arm64) yq_arch=arm64 ;; \
         *) echo "Unsupported architecture: $TARGETARCH" >&2; exit 1 ;; \
       esac \
    && curl -fsSL "https://github.com/mikefarah/yq/releases/download/v${YQ_VERSION}/yq_linux_${yq_arch}" \
        -o /usr/local/bin/yq \
    && chmod +x /usr/local/bin/yq \
    && curl -fsSL https://aka.ms/aca-cli-install | sh \
    && rm -rf /var/lib/apt/lists/*

COPY ensure-tmux.sh /usr/local/bin/ensure-tmux
COPY entrypoint.sh /usr/local/bin/container-entrypoint
COPY copilot-tmux.service /etc/systemd/system/copilot-tmux.service
COPY tmux-profile.sh /etc/profile.d/copilot-tmux.sh

RUN chmod +x /usr/local/bin/ensure-tmux /usr/local/bin/container-entrypoint \
    && ln -s /etc/systemd/system/copilot-tmux.service \
        /etc/systemd/system/multi-user.target.wants/copilot-tmux.service \
    && mkdir -p /mnt/data \
    && printf '%s\n' \
        'set -g mouse on' \
        'set -g history-limit 100000' \
        'set -g detach-on-destroy off' \
        > /root/.tmux.conf

WORKDIR /mnt/data

ENTRYPOINT ["/usr/local/bin/container-entrypoint"]
