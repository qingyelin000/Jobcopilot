FROM node:22-alpine AS build

WORKDIR /app

ARG VITE_ENABLE_JOBS=true
ENV VITE_ENABLE_JOBS=${VITE_ENABLE_JOBS}
ARG NPM_REGISTRY=https://registry.npmjs.org/
ARG NPM_REGISTRY_FALLBACK=https://registry.npmmirror.com/
ARG NPM_FETCH_RETRIES=5
ARG NPM_FETCH_RETRY_FACTOR=2
ARG NPM_FETCH_RETRY_MINTIMEOUT=20000
ARG NPM_FETCH_RETRY_MAXTIMEOUT=120000
ARG NPM_FETCH_TIMEOUT=180000

COPY package.json tsconfig.json tsconfig.node.json vite.config.ts index.html /app/
COPY src /app/src

RUN set -eux; \
    npm config set fetch-retries "${NPM_FETCH_RETRIES}"; \
    npm config set fetch-retry-factor "${NPM_FETCH_RETRY_FACTOR}"; \
    npm config set fetch-retry-mintimeout "${NPM_FETCH_RETRY_MINTIMEOUT}"; \
    npm config set fetch-retry-maxtimeout "${NPM_FETCH_RETRY_MAXTIMEOUT}"; \
    npm config set fetch-timeout "${NPM_FETCH_TIMEOUT}"; \
    npm config set audit false; \
    npm config set fund false; \
    install_once() { \
      registry_url="$1"; \
      echo "Using npm registry: ${registry_url}"; \
      npm config set registry "${registry_url}"; \
      npm config get registry; \
      npm install --legacy-peer-deps --loglevel=info; \
    }; \
    if ! install_once "${NPM_REGISTRY}"; then \
      if [ "${NPM_REGISTRY}" = "${NPM_REGISTRY_FALLBACK}" ]; then \
        echo "npm install failed on registry ${NPM_REGISTRY}"; \
        exit 1; \
      fi; \
      echo "Primary npm registry failed, retrying with fallback registry..."; \
      npm cache clean --force; \
      install_once "${NPM_REGISTRY_FALLBACK}"; \
    fi
RUN npm run build

FROM nginx:1.27-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
