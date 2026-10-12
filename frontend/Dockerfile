FROM node:20-slim AS build
WORKDIR /app
COPY package*.json .
RUN npm ci
COPY . .
# Vite inlines import.meta.env.VITE_* at build time, not runtime - so the
# deployed backend's URL has to arrive as a build arg, not a container env
# var (setting VITE_API_URL on the running nginx container would do nothing,
# the static JS is already built by then).
ARG VITE_API_URL
ENV VITE_API_URL=$VITE_API_URL
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
