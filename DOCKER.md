# Using Docker

The following instructions assume the current directory is the same as this file.

## Building the image

Build the image based on Alpine OS:
```$ docker compose build```

or

Build the image based on Ubuntu OS:
```$ DOCKER_OS=ubuntu docker compose build```

## Running the image

Run the Alpine version:

```shell
LDAP_BIND_DN=xxx
export LDAP_BIND_DN
LDAP_BIND_PASSWORD=yyy
export LDAP_BIND_PASSWORD
docker compose build
```

Run the Ububtu version:

```shell
LDAP_BIND_DN=xxx
export LDAP_BIND_DN
LDAP_BIND_PASSWORD=yyy
export LDAP_BIND_PASSWORD
DOCKER_OS=ubuntu docker compose build
```

## Open the application in a browser

Browse to https://localhost:8080/

This will generate an error as the certificate is not recognised.
You will need to override this.

TODO: explain how to trust the certificate

## Start shell in running container

docker compose exec atr bash

## Start container and run shell instead of atr

docker compose run -rm atr bash
