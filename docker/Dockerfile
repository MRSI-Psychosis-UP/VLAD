FROM ubuntu:22.04

COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /usr/local/bin/uv

ARG DEBIAN_FRONTEND=noninteractive
ARG INSTALL_FSL=true
ARG FSLDIR=/opt/fsl

ENV TZ=Etc/UTC \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    MPLCONFIGDIR=/tmp/matplotlib \
    MPLBACKEND=Agg \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1 \
    FSLDIR=${FSLDIR} \
    FSLOUTPUTTYPE=NIFTI_GZ \
    PATH=/opt/vlad/.venv/bin:${FSLDIR}/share/fsl/bin:${FSLDIR}/bin:${PATH}

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Reports use Python wheels and embedded HTML assets. No R, Pandoc, compiler
# toolchain or CRAN repositories are part of the runtime image.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        bc \
        bzip2 \
        ca-certificates \
        curl \
        dbus-x11 \
        file \
        fonts-dejavu-core \
        libgomp1 \
        libstdc++6 \
        libx11-6 \
        python-is-python3 \
        python3 \
        python3-tk \
        python3-venv \
        unzip \
        wget \
        xauth \
        xvfb \
        zenity \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/vlad
COPY pyproject.toml uv.lock ./

# Use Ubuntu's Tk-enabled Python; install dependencies before source for caching.
RUN uv sync --python /usr/bin/python3 --locked --no-dev --no-install-project

RUN case "${INSTALL_FSL}" in \
      true) \
        wget -q https://fsl.fmrib.ox.ac.uk/fsldownloads/fslconda/releases/fslinstaller.py -O /tmp/fslinstaller.py \
        && /usr/bin/python3 /tmp/fslinstaller.py -d "${FSLDIR}" -n -r -c none \
        && rm -f /tmp/fslinstaller.py ;; \
      false) echo "Skipping FSL installation because INSTALL_FSL=${INSTALL_FSL}" ;; \
      *) echo "INSTALL_FSL must be true or false" >&2; exit 2 ;; \
    esac

COPY . /opt/vlad
COPY docker/entrypoint.sh /usr/local/bin/vlad-entrypoint

RUN uv sync --python /usr/bin/python3 --locked --no-dev \
    && chmod +x \
        /opt/vlad/install.sh \
        /opt/vlad/vlad \
        /opt/vlad/scripts/compat/VLAD_beta7.sh \
        /opt/vlad/scripts/compat/Prep_VLAD_b6.1.sh \
        /opt/vlad/workflows/VLAD_beta7.sh \
        /opt/vlad/workflows/Prep_VLAD_b6.1.sh \
        /usr/local/bin/vlad-entrypoint \
    && mkdir -p /work \
    && chmod 1777 /work

ENTRYPOINT ["vlad-entrypoint"]
CMD ["bash"]
