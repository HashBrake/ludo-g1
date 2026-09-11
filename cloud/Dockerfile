# Training image for the Greennode VM. Built and run on the VM, never on the control laptop
# (docker is not installed there, so this file is delivered unbuilt and unverified: see docs/cloud.md
# "Untested until Q-001").
#
#   docker build -t ludo-g1-train:0.1.0 -f cloud/Dockerfile .
#   cloud/greennode.sh train cloud/dummy_job.py        # runs `python ...` inside this image
#
# Python 3.10 is a hard constraint for the repo (the DexH15 wheel is cp310, DECISIONS.md D-002 A1);
# it is kept here so the cloud interpreter matches the laptop's.
#
# TODO(Phase 3): swap the base for a CUDA image and the CPU torch wheel for the matching GPU build once
# the Greennode instance type and its driver version are known (agents/QUESTIONS.md Q-001). Candidate:
# nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04 + python3.10 + torch 2.5.1+cu124. The CPU wheel below is
# enough for cloud/dummy_job.py, which is the only job that exists today.
FROM python:3.10.20-slim-bookworm

# Pinned so a rebuild six months from now is the same image.
ARG TORCH_VERSION=2.5.1+cpu
ARG TORCH_INDEX=https://download.pytorch.org/whl/cpu

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        git=1:2.39.* \
        rsync=3.2.* \
        procps=2:4.0.* \
    && rm -rf /var/lib/apt/lists/*

# requirements.txt holds two entries that cannot resolve on the VM: the file:// DexH15 wheel (a laptop
# path, cp310 binary for the hand) and unitree_sdk2py (DDS to a robot the VM cannot see). Neither is a
# training dependency, so the image installs the training subset explicitly instead of the whole file.
RUN pip install --index-url "${TORCH_INDEX}" "torch==${TORCH_VERSION}" \
    && pip install \
        numpy==2.2.6 \
        pyyaml==6.0.3 \
        structlog==26.1.0 \
        opencv-python-headless==5.0.0.93 \
        scipy==1.15.3

# The repo is bind-mounted at /work by `greennode.sh train` (-v REMOTE_ROOT:/work -w /work), so the
# image carries no project code: the code that runs is whatever `greennode.sh up` last pushed.
WORKDIR /work
ENV PYTHONPATH=/work \
    LUDO_G1_ROOT=/work \
    LUDO_G1_CHECKPOINT_DIR=/work/data/checkpoints

CMD ["python", "cloud/dummy_job.py"]
