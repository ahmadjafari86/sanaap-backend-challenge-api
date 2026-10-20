FROM golang:1.24-bookworm AS builder

ARG MINIO_VERSION=RELEASE.2025-09-07T16-13-09Z

ENV CGO_ENABLED=0
ENV GOTOOLCHAIN=auto

WORKDIR /src

RUN git clone --depth 1 --branch "${MINIO_VERSION}" \
    https://github.com/minio/minio.git .

RUN go build -trimpath -o /out/minio .


FROM debian:bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 minio \
    && useradd --uid 10001 --gid minio --no-create-home minio \
    && mkdir -p /data \
    && chown minio:minio /data

COPY --from=builder /out/minio /usr/local/bin/minio

USER minio
EXPOSE 9000 9001

ENTRYPOINT ["minio"]
CMD ["server", "/data", "--console-address", ":9001"]
