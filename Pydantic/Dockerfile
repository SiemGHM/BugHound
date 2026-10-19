# Sandbox image for validating and later solving pydantic benchmark cases.


FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends git build-essential curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*


COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV RUSTUP_HOME=/usr/local/rustup \
    CARGO_HOME=/usr/local/cargo \
    PATH=/usr/local/cargo/bin:$PATH
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
      | sh -s -- -y --default-toolchain stable --profile minimal \
 && rustc --version

WORKDIR /repo
RUN git clone --quiet https://github.com/pydantic/pydantic.git . \
 && git config --global --add safe.directory /repo


RUN uv sync --all-extras --no-progress || true

ENV UV_LINK_MODE=copy \
    PYTHONDONTWRITEBYTECODE=1

# No network for test runs happens at `docker run --network none`; the image
# itself needs network only at build time.
CMD ["bash"]