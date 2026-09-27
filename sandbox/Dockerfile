# Mira Scout sandbox image.
# The Playwright base image tag MUST match the playwright version in package.json.

FROM mcr.microsoft.com/playwright:v1.63.0-noble AS build
WORKDIR /build
COPY package.json package-lock.json tsconfig.json ./
RUN npm ci
COPY scout.ts ./
RUN npm run build && npm prune --omit=dev

FROM mcr.microsoft.com/playwright:v1.63.0-noble
WORKDIR /app
COPY --from=build /build/node_modules ./node_modules
COPY --from=build /build/dist ./dist
COPY --from=build /build/package.json ./

# The root filesystem is mounted read-only at runtime; Chromium needs a writable HOME.
ENV HOME=/tmp \
    NODE_ENV=production \
    SCOUT_OUT_DIR=/out

# pwuser (uid 1000) ships with the Playwright image.
USER pwuser
ENTRYPOINT ["node", "/app/dist/scout.js"]
