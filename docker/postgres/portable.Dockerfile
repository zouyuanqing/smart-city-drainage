ARG BASE_IMAGE=timescale/timescaledb:2.17.2-pg15@sha256:d33b93c43b9db7a065f660847d4390d4a84ea1dd72395258aa8873f314de58c7
FROM ${BASE_IMAGE}

LABEL org.opencontainers.image.title="smart-city-drainage-portable" \
      org.opencontainers.image.description="Digest-pinned PostgreSQL 15 + TimescaleDB profile without PostGIS" \
      org.opencontainers.image.vendor="smart-city-drainage"

ENV HYDRAULIC_SPATIAL_BACKEND=portable \
    HYDRAULIC_IMAGE_SPATIAL_BACKEND=portable

COPY --chmod=0755 docker/postgres/entrypoint-profile.sh /usr/local/bin/entrypoint-profile.sh
ENTRYPOINT ["/usr/local/bin/entrypoint-profile.sh"]
CMD ["postgres"]
