# mostly-tmpnb

tmpnb with user registration, authentication, and lightweight persistence using locally attached Docker volumes.

## Use Case

Mostly tmpnb is useful for meetups and short-courses during which:

1. select users should be able to create their own Jupyter Notebook accounts
2. user notebooks and data should persist across sessions
3. containers should recycle when their users go idle
4. dependencies such as libraries, language kernels, extensions, etc. should remain fixed (mostly)

## Prerequisites

* GNU Make 3.81+
* Docker Toolbox with Docker 1.9.1+ and Docker Machine 0.5.1+

## Local Quickstart

```
# create a docker host
make virtualbox-vm NAME=tmpnb-dev
# point docker machine to it
eval $(docker-machine env tmpnb-dev)
# build the notebook and volume manager images
make images
# start a dev solution with a hardcoded token and "test" as a registration key
make dev 
```

## Softlayer Quickstart

```
# create a docker host with FQDN myclass.mydomain.com
make softlayer-vm NAME=myclass \
    SOFTLAYER_DOMAIN=mydomain.com \
    SOFTLAYER_USER=myname \
    SOFTLAYER_API_KEY=mykey
# point docker machine to it
eval $(docker-machine env myclass)
# register fully-qualified domain name for the host
make softlayer-dns SOFTLAYER_DOMAIN=mydomain.com
# build the notebook and volume manager images on the host
make images
# get a lets encrypt certificate (accepts terms of service)
make secrets FQDN=myclass.mydomain EMAIL=myname@myemail.com
# start a tmpnb pool with 50 containers, each one hard-capped at 512 MB RAM
# require "secret" for new user registration
make tmpnb TOKEN="$(openssl rand -base64 32)" \
    REGISTRATION_KEY="secret" \
    POOL_SIZE=50 \
    MEMORY_LIMIT=512m 
```

## How does it work?

1. User visits the root of the tmpnb site in her browser.
2. tmpnb redirects the user to a notebook container in its pool.
3. User sees a custom notebook login page with Login and Register options.
4. User registers with a username, password, and pre-shared registration key (e.g., a secret communicated to select users).
5. Notebook login handler forwards user registration information to a volume manager service.
6. Volume manager service creates a new Docker volume named using the unsalted hash of the username (prefix) plus a bcrypt hash of the password (suffix).
7. User logs in with the previously chosen username / password. (Happens automatically after registration.)
8. Notebook login handler forwards user login information to a volume manager service.
9. Volume manager looks for the volume owned by the username and confirms the bcrypted password matches the volume suffix.
10. Volume manager bind mounts the user volume into the running notebook container as /home/jovyan/work (the root of the notebook directory in all docker-stacks images).

## TODOs

* Enable nbexamples from host
* Consistent log message formats across services
* Routing to a central log aggregator via docker logging driver
* Make target for rsync backup strategy
* Make target for migrating user data from volumes with lost passwords
* Instructions for scheduling renewal of cert
* Better password rules (numbers+letters+punc)
* Redirect to a tmpnb page after logout, not a new container