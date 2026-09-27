ARG BASE_IMAGE=timescale/timescaledb-ha:pg15@sha256:f49bf91b8966318e9bab4c44e4cc6c6533539dd44fcb049ee3334349dfcb233f
FROM ${BASE_IMAGE}

LABEL org.opencontainers.image.title="smart-city-drainage-postgis" \
      org.opencontainers.image.description="Digest-pinned PostgreSQL 15 + TimescaleDB + PostGIS profile" \
      org.opencontainers.image.vendor="smart-city-drainage"

ENV HYDRAULIC_SPATIAL_BACKEND=postgis \
    HYDRAULIC_IMAGE_SPATIAL_BACKEND=postgis

COPY --chmod=0755 docker/postgres/entrypoint-profile.sh /usr/local/bin/entrypoint-profile.sh
ENTRYPOINT ["/usr/local/bin/entrypoint-profile.sh"]
CMD ["postgres"]
