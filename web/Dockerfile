# The site's two images, built from this repository:
#   docker build --target site -t niadra/site .        pages, Console and the admin panel (Caddy, :8080)
#   docker build --target site-api -t niadra/site-api .   the contact form, the access counter and the
#                                                          admin panel's API (Node, :8787 TCP, :8788 UDP)
# Both run as user 10001 with nothing to write but /config (the Console's runtime addresses).

FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build && npm run build:api

FROM caddy:2.10-alpine AS site
# The binary ships with the capability to bind ports below 1024; on :8080 it needs none, and a
# container that drops every capability could not start it otherwise.
RUN apk add --no-cache libcap && setcap -r /usr/bin/caddy && apk del libcap \
  && mkdir -p /config /data && chown 10001:10001 /config /data
COPY --from=build /app/dist /srv
# The admin panel, outside /srv so the public host can never serve it (deploy/Caddyfile, admin host only).
COPY --from=build /app/dist-admin /admin
COPY --from=build /app/dist/favicon.svg /admin/favicon.svg
COPY deploy/Caddyfile /etc/caddy/Caddyfile
COPY deploy/console-config.sh deploy/entrypoint.sh /usr/local/bin/
ENV XDG_CONFIG_HOME=/config XDG_DATA_HOME=/data
USER 10001:10001
EXPOSE 8080
ENTRYPOINT ["/bin/sh", "/usr/local/bin/entrypoint.sh"]

FROM node:22-alpine AS site-api
WORKDIR /app
COPY --from=build /app/dist-server/api.mjs ./api.mjs
# The country table of the access report (scripts/geo-country.mjs), read next to api.mjs at start.
COPY --from=build /app/dist-server/geo-country.bin ./geo-country.bin
ENV NODE_ENV=production HOST=0.0.0.0 PORT=8787
USER 10001:10001
EXPOSE 8787 8788/udp
CMD ["node", "api.mjs"]
