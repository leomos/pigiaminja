ARG PG_MAJOR=17

FROM postgres:${PG_MAJOR} AS builder

RUN mkdir -p /usr/local/pigiaminja
WORKDIR /usr/local/pigiaminja

RUN apt update && apt install -y \
        curl \
        build-essential \
        libreadline-dev \
        zlib1g-dev \
        flex \
        bison \
        libxml2-dev \
        libxslt-dev \
        libssl-dev \
        libxml2-utils \
        xsltproc \
        ccache \
        pkg-config \
        libicu-dev \
        postgresql-server-dev-${PG_MAJOR}

RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | bash -s -- -y
ENV PATH="/root/.cargo/bin:${PATH}"
RUN cargo install --locked cargo-pgrx@0.15.0

COPY . .

RUN cargo pgrx init --pg${PG_MAJOR} $(which pg_config)
RUN cargo pgrx install --release

FROM postgres:${PG_MAJOR}
COPY --from=builder /usr/lib/postgresql/${PG_MAJOR}/lib/pigiaminja* /usr/lib/postgresql/${PG_MAJOR}/lib/
COPY --from=builder /usr/share/postgresql/${PG_MAJOR}/extension/pigiaminja* /usr/share/postgresql/${PG_MAJOR}/extension/

CMD ["postgres", "-c", "shared_preload_libraries=pigiaminja"]
