FROM alpine:edge

RUN apk add --no-cache npm \
  && apk add --no-cache \
       --repository=https://dl-cdn.alpinelinux.org/alpine/edge/testing \
       dart-sass

RUN adduser -D -h /home/build build

WORKDIR /opt/bootstrap

COPY .npmrc /opt/bootstrap/.npmrc
COPY build.sh /opt/bootstrap/build.sh
COPY bump.sh /opt/bootstrap/bump.sh

RUN chmod +x /opt/bootstrap/build.sh /opt/bootstrap/bump.sh
RUN chown -R build:build /opt/bootstrap

USER build

ENV HOME=/opt/bootstrap
ENV NPM_CONFIG_USERCONFIG=/opt/bootstrap/.npmrc

CMD ["/opt/bootstrap/build.sh"]
