ARG DOCKERHUB_LIBRARY_MIRROR=m.daocloud.io/docker.io/library
FROM ${DOCKERHUB_LIBRARY_MIRROR}/node:20-alpine AS build
WORKDIR /web
COPY frontend/package.json frontend/package.json
WORKDIR /web/frontend
RUN npm config set registry https://registry.npmmirror.com && npm install
COPY frontend ./
RUN npm run build

FROM ${DOCKERHUB_LIBRARY_MIRROR}/nginx:1.27-alpine
COPY --from=build /web/frontend/dist /usr/share/nginx/html
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
