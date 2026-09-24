# Base image PINNED to a specific Debian release (Day 3 fix). An unpinned
# `python:3.12-slim` silently changes its Debian base over time, which can
# break the apt-get line below with no change to this repository -- that is
# exactly what happened to the sibling CertiFake project's Dockerfile when
# slim moved from bookworm to trixie and three of its package names
# (libgl1-mesa-glx, libmagic1, libglib2.0-0) stopped existing. Its CI
# docker-build job caught it; this pin is what stops the same class of
# drift here.
#
# This project's two apt packages (gcc, libpq-dev) were checked against a
# real trixie host and both still have valid installation candidates
# there -- they are not part of Debian's 64-bit time_t (t64) rename set.
FROM python:3.12-slim-trixie

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p logs

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
