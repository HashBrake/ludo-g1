# Training image for the Greennode VM (CLAUDE.md 5.8). Built and run on the VM, never on the control
# laptop: `docker` is not installed there (`command -v docker` is empty, recorded in agents/BUILD_LOG.md
# under T-031), so this file is delivered unbuilt and reviewed by reading. See docs/cloud.md.
#
#   docker build -t ludo-g1-train:0.2.0 -f cloud/Dockerfile .     # on the VM, from the pushed repo root
#   cloud/greennode.sh train policy/train.py --sessions data/raw/<session>
#
# Python 3.10 is a hard constraint for the repo (the DexH15 wheel is cp310, D-002 A1; lerobot 0.4.4 is
# the last release that installs on 3.10, D-015). Ubuntu 22.04's own python3 *is* 3.10, which is why
# the base below is a jammy CUDA image and no deadsnakes PPA is needed.
#
# Why nvidia/cuda:12.8.1-cudnn-runtime-ubuntu22.04, pinned by digest:
#   * torch 2.9.1 publishes cp310 linux x86_64 wheels for cu126, cu128, cu129 and cu130 (checked
#     against download.pytorch.org on 2026-09-12). cu128 is the middle of that range: CUDA 12.x minor
#     version compatibility means it runs on any >= 525.60.13 driver, it covers Ampere, Ada, Hopper
#     *and* Blackwell (sm_120), and unlike cu130 it does not require an r580+ driver. Greennode's
#     instance type and driver version are still unknown (Q-001); if `nvidia-smi` there reports a
#     CUDA version below 12.8, switch both this tag and cloud/requirements-train.txt to cu126.
#   * `runtime` (not `devel`): nothing here compiles CUDA code; the torch wheels ship their own
#     nvidia-*-cu12 libraries and only need the driver. `cudnn` because torch loads cuDNN.
#   * the digest is the multi-arch index digest of the 12.8.1-cudnn-runtime-ubuntu22.04 tag, so a
#     rebuild in six months is the same image even if the tag is re-pushed.
FROM nvidia/cuda@sha256:17e2934e1fa96152b14f78078bfbafd0f00f391df995dc6c641a720fce1202bb

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DEBIAN_FRONTEND=noninteractive

# ffmpeg: torchcodec (a lerobot dependency) links against the system FFmpeg shared libraries and
# raises at import time without them. git/rsync/procps: the same three the CPU image carried, for
# `git rev-parse HEAD` in policy/train.py's run record, for rsync's remote half, and for `ps`.
RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        python3.10=3.10.* \
        python3-pip=22.0.* \
        ffmpeg=7:4.4.* \
        git=1:2.34.* \
        rsync=3.2.* \
        procps=2:3.3.* \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf /usr/bin/python3.10 /usr/local/bin/python \
    && ln -sf /usr/bin/python3.10 /usr/local/bin/python3

# jammy ships pip 22.0.2, which predates the metadata of several wheels below.
RUN python -m pip install --no-cache-dir pip==26.2.1 setuptools==80.10.2 wheel==0.48.0

# The training subset of requirements.txt, with the CUDA torch build instead of the +cpu one. See
# that file for what is deliberately excluded and why.
COPY cloud/requirements-train.txt /tmp/requirements-train.txt
RUN python -m pip install --no-cache-dir -r /tmp/requirements-train.txt \
    && python -c "import torch, torchvision, lerobot; print(torch.__version__, torchvision.__version__, lerobot.__version__)"

# The repo is bind-mounted at /work by `greennode.sh train` (-v REMOTE_ROOT:/work -w /work), so the
# image carries no project code: the code that runs is whatever `greennode.sh up` last pushed.
WORKDIR /work
ENV PYTHONPATH=/work \
    LUDO_G1_ROOT=/work \
    LUDO_G1_CHECKPOINT_DIR=/work/data/checkpoints

# Overridden by `greennode.sh train SCRIPT ARGS...`; this default is the Phase 0 round-trip check.
CMD ["python", "cloud/dummy_job.py"]
