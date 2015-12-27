.PHONY: help check token-check notebook-image volman-image images proxy pool volman tmpnb nuke dev dev-notebook

NOTEBOOK_IMAGE:=tmpnb-notebook
VOLMAN_IMAGE:=tmpnb-volman
SECRETS_VOLUME:=tmpnb-secrets
MAX_LOG_SIZE:=50m
MAX_LOG_ROLLOVER:=10

help:
	@cat README.md

###### prereqs

check:
	@which docker-machine > /dev/null || (echo "ERROR: docker-machine 0.5.0+ required"; exit 1)
	@which docker > /dev/null || (echo "ERROR: docker not found"; exit 1)
	@docker | grep volume > /dev/null || (echo "ERROR: docker 1.9.0+ required"; exit 1)

token-check:
	@test -n "$(TOKEN)" || \
		(echo "ERROR: TOKEN not defined (make help)"; exit 1)

registration-check:
	@test -n "$(REGISTRATION_KEY)" || \
		(echo "ERROR: REGISTRATION_KEY not defined (make help)"; exit 1)

###### images

notebook-image: DOCKER_ARGS?=
notebook-image: check
	@cd notebook && docker build --rm $(DOCKER_ARGS) -t $(NOTEBOOK_IMAGE) .
# Drain old idle containers after a successful notebook image build if the pool 
# is already running.
	@-cat admin/drain.py | docker exec -i tmpnb-pool python

volman-image: DOCKER_ARGS?=
volman-image: check
	@cd volman && docker build --rm $(DOCKER_ARGS) -t $(VOLMAN_IMAGE) .

images: notebook-image volman-image

###### secrets

secrets:
	@test -n "$(FQDN)" || \
		(echo "ERROR: FQDN not defined or blank"; exit 1)
	@test -n "$(EMAIL)" || \
		(echo "ERROR: EMAIL not defined or blank"; exit 1)
	@docker volume create --name $(SECRETS_VOLUME) > /dev/null
# Specifying an alternative cert path doesn't work with the --duplicate
# setting which we want to use for renewal.
	@docker run -it --rm -p 80:80 \
		-v $(SECRETS_VOLUME):/etc/letsencrypt \
		quay.io/letsencrypt/letsencrypt:latest \
		certonly \
		--standalone \
		--standalone-supported-challenges http-01 \
		--agree-tos \
		--duplicate \
		--domain '$(FQDN)' \
		--email '$(EMAIL)'
# The lets encrypt image has an entrypoint so we use the notebook image
# instead which we know uses tini as the entry and can run arbitrary commands.
# Here we need to set the permissions so nobody in the proxy container can read
# the cert and key. Plus we want to symlink the certs into the root of the 
# /etc/letsencrypt directory so that the FQDN doesn't have to be known later.
	@docker run -it --rm \
		-v $(SECRETS_VOLUME):/etc/letsencrypt \
		$(NOTEBOOK_IMAGE) \
		bash -c "ln -s /etc/letsencrypt/live/$(FQDN)/* /etc/letsencrypt/ && \
			find /etc/letsencrypt -type d -exec chmod 755 {} +"

###### components

proxy: PROXY_IMAGE?=jupyter/configurable-http-proxy@sha256:f84940db7ddf324e35f1a5935070e36832cc5c1f498efba4d69d7b962eec5d08
proxy: check token-check
	@docker run -d \
		--name=tmpnb-proxy \
		--log-driver=json-file \
		--log-opt max-size=$(MAX_LOG_SIZE) \
		--log-opt max-file=$(MAX_LOG_ROLLOVER) \
		-p 80:8000 \
		-e CONFIGPROXY_AUTH_TOKEN=$(TOKEN) \
		$(PROXY_IMAGE) \
			--default-target http://127.0.0.1:9999

secure-proxy: PROXY_IMAGE?=jupyter/configurable-http-proxy@sha256:f84940db7ddf324e35f1a5935070e36832cc5c1f498efba4d69d7b962eec5d08
secure-proxy: check token-check
	@docker run -d \
		--name=tmpnb-proxy \
		--log-driver=json-file \
		--log-opt max-size=$(MAX_LOG_SIZE) \
		--log-opt max-file=$(MAX_LOG_ROLLOVER) \
		-p 443:8000 \
		-e CONFIGPROXY_AUTH_TOKEN=$(TOKEN) \
		-v $(SECRETS_VOLUME):/etc/letsencrypt \
		$(PROXY_IMAGE) \
			--default-target http://127.0.0.1:9999 \
			--ssl-key /etc/letsencrypt/privkey.pem \
			--ssl-cert /etc/letsencrypt/fullchain.pem

pool: TMPNB_IMAGE?=jupyter/tmpnb@sha256:c84dd98caffd499b40a147d5f2a2d1b9f498d6ee1b3bc5d6816f9a466ed718fc
pool: POOL_SIZE?=4
pool: MEMORY_LIMIT?=512m
pool: NOTEBOOK_IMAGE?=$(IMAGE)
pool: BRIDGE_IP=$(shell docker inspect --format='{{.NetworkSettings.Networks.bridge.Gateway}}' tmpnb-proxy)
pool: check token-check
	@docker run -d \
		--name=tmpnb-pool \
		--log-driver=json-file \
		--log-opt max-size=$(MAX_LOG_SIZE) \
		--log-opt max-file=$(MAX_LOG_ROLLOVER) \
		--net=container:tmpnb-proxy \
		-e CONFIGPROXY_AUTH_TOKEN=$(TOKEN) \
		-v /var/run/docker.sock:/docker.sock \
		$(TMPNB_IMAGE) \
		python orchestrate.py --image='$(NOTEBOOK_IMAGE)' \
			--container_ip=$(BRIDGE_IP) \
			--pool_size=$(POOL_SIZE) \
			--pool_name=tmpnb \
			--cull_period=30 \
			--cull_timeout=600 \
			--max_dock_workers=4 \
			--mem_limit=$(MEMORY_LIMIT) \
			--command='start-notebook.sh \
			"--NotebookApp.base_url={base_path} \
			--NotebookApp.login_handler_class=notebook.auth.by_volume.LoginHandler \
			--NotebookApp.logout_handler_class=notebook.auth.by_volume.LogoutHandler \
			--VolumesClient.server_url="http://$(BRIDGE_IP):9005" \
			--ip=0.0.0.0 \
			--port={port} \
			--NotebookApp.trust_xheaders=True"'

volman: HOSTMOUNT:=/host
volman: BRIDGE_IP=$(shell docker inspect --format='{{.NetworkSettings.Networks.bridge.Gateway}}' tmpnb-proxy)
volman: registration-check check
	@docker run -d \
		--name=tmpnb-volman \
		--log-driver=json-file \
		--log-opt max-size=$(MAX_LOG_SIZE) \
		--log-opt max-file=$(MAX_LOG_ROLLOVER) \
		--pid=host \
		--privileged \
		-v /:$(HOSTMOUNT) \
		-p $(BRIDGE_IP):9005:9005 \
		-v /var/run/docker.sock:/var/run/docker.sock \
		$(VOLMAN_IMAGE) --ip=0.0.0.0 \
			--host_mount='$(HOSTMOUNT)' \
			--pool_prefix='tmp.tmpnb.' \
			--registration_key='$(REGISTRATION_KEY)'

###### whole shebang 

tmpnb: secure-proxy volman pool

nuke: check
	@-docker rm -f tmpnb-proxy 2> /dev/null
	@-docker rm -f tmpnb-volman 2> /dev/null
	@-docker rm -f tmpnb-pool 2> /dev/null
	@-docker rm -f $$(docker ps -a | grep 'tmp.' | awk '{print $$1}') 2> /dev/null

###### dev targets

dev: TOKEN:=devtokenonly!
dev: REGISTRATION_KEY:=test
dev: proxy volman pool

dev-notebook: check
	@docker run -it --rm \
		-p 8888:8888 \
		--name=tmp.tmpnb.devcontainer \
		-v `pwd`/notebook/templates/login_register.html:/opt/conda/lib/python3.4/site-packages/notebook/templates/login_register.html \
		-v `pwd`/notebook/auth/by_volume.py:/opt/conda/lib/python3.4/site-packages/notebook/auth/by_volume.py \
		$(NOTEBOOK_IMAGE) start-notebook.sh \
			--NotebookApp.base_url=/user/devcontainer \
			--NotebookApp.login_handler_class=notebook.auth.by_volume.LoginHandler \
			--NotebookApp.logout_handler_class=notebook.auth.by_volume.LogoutHandler

# docker-machine drivers
include virtualbox.makefile
include softlayer.makefile
