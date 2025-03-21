#! /bin/bash

set -ue

SCRIPT_PATH=$(cd "$(dirname "$0")";pwd)

if docker --version > /dev/null 2>&1; then
    echo "docker is already installed"
    docker --version
else
    cd "$SCRIPT_PATH"/docker
    dpkg -i ./*.deb
fi

if nvidia-container-cli --version > /dev/null 2>&1; then
    echo "nvidia container runtime is already installed"
    nvidia-container-cli --version
else
    cd "$SCRIPT_PATH"/nvidia-container-runtime
    dpkg -i ./*.deb
    nvidia-ctk runtime configure --runtime=docker
    systemctl restart docker
fi

configured_nvidia_runtime=$(docker info|grep Runtimes|grep nvidia)

echo "$configured_nvidia_runtime"