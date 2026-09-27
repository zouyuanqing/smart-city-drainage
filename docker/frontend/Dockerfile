# Node 18.20.8 (npm 10.8.2) is the verified toolchain; see frontend/.node-version.
# .npmrc (engine-strict) must be copied before npm ci so a wrong runtime fails fast.
ARG NODE_IMAGE=node:18.20.8-alpine
FROM ${NODE_IMAGE} AS builder
ARG NPM_REGISTRY=https://registry.npmjs.org/
WORKDIR /app
COPY package*.json .npmrc ./
RUN npm ci --registry="$NPM_REGISTRY"
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=builder /app/dist /usr/share/nginx/html
COPY --from=scn_frontend_deploy nginx-default.conf /etc/nginx/conf.d/default.conf
COPY --from=scn_frontend_deploy nginx.conf /etc/nginx/nginx.conf
USER 101:101
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD ["wget", "-qO-", "http://127.0.0.1:8080/nginx-health"]
CMD ["nginx", "-g", "daemon off;"]
