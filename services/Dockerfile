FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock \
    && useradd --uid 10001 --create-home phoenix \
    && mkdir /data \
    && chown phoenix:phoenix /data
FROM runtime AS switch
RUN apt-get update \
    && apt-get install --no-install-recommends -y git \
    && rm -rf /var/lib/apt/lists/* \
    && install -d -o phoenix -g phoenix /revisions
# Compose mounts the developer's Git metadata here as read-only.
RUN git config --system --add safe.directory /source.git
COPY version_switch ./version_switch
USER phoenix

FROM runtime AS brain
# The agents' lab runs ShopLite revisions, so it needs git and the shop's Node version.
RUN apt-get update \
    && apt-get install --no-install-recommends -y git libstdc++6 \
    && rm -rf /var/lib/apt/lists/* \
    && install -d -o phoenix -g phoenix /revisions \
    && git config --system --add safe.directory '*'
COPY --from=node:24-bookworm-slim /usr/local/bin/node /usr/local/bin/node
COPY --from=node:24-bookworm-slim /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx
COPY brain ./brain
USER phoenix
