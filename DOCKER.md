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
```
LDAP_BIND_DN=
$ docker compose build
```

Run the Ububtu version:
```$ DOCKER_OS=ubuntu docker compose build```

## Open the application in a browser

Browse to https://localhost:8080/

This will generate an error as the certificate is not recognised.
You will need to override this.

TODO: explain how to trust the certificate

## Start shell in running container

docker compose exec atr bash