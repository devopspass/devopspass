IMAGE_NAME ?= devopspass/devopspass
IMAGE_TAG ?= latest
PLATFORMS ?= linux/amd64,linux/arm64
DOCKERFILE ?= Dockerfile.prod
BUILDER_NAME ?= devopspass-release-builder
CONTAINER_CLI ?= docker
IS_PODMAN := $(shell $(CONTAINER_CLI) --version 2>/dev/null | grep -qi podman && echo 1 || echo 0)

.PHONY: release
release:
	@if [ "$(IS_PODMAN)" = "1" ]; then \
		echo "Podman detected: prebuilding UI assets on host to avoid esbuild/qemu crash"; \
		(cd ui && npm ci && npm run build -- --configuration production); \
		$(CONTAINER_CLI) buildx inspect >/dev/null; \
		AMD64_TAG="$(IMAGE_NAME):$(IMAGE_TAG)-amd64"; \
		ARM64_TAG="$(IMAGE_NAME):$(IMAGE_TAG)-arm64"; \
		LOCAL_MANIFEST_TAG="localhost/$(IMAGE_NAME):$(IMAGE_TAG)-manifest"; \
		REMOTE_MANIFEST_TAG="$(IMAGE_NAME):$(IMAGE_TAG)"; \
		$(CONTAINER_CLI) buildx build \
			--platform linux/amd64 \
			--file $(DOCKERFILE) \
			--build-arg SKIP_UI_BUILD=1 \
			--tag $$AMD64_TAG \
			.; \
		$(CONTAINER_CLI) buildx build \
			--platform linux/arm64 \
			--file $(DOCKERFILE) \
			--build-arg SKIP_UI_BUILD=1 \
			--tag $$ARM64_TAG \
			.; \
		$(CONTAINER_CLI) manifest rm $$LOCAL_MANIFEST_TAG >/dev/null 2>&1 || true; \
		$(CONTAINER_CLI) manifest create $$LOCAL_MANIFEST_TAG; \
		$(CONTAINER_CLI) manifest add $$LOCAL_MANIFEST_TAG $$AMD64_TAG; \
		$(CONTAINER_CLI) manifest add $$LOCAL_MANIFEST_TAG $$ARM64_TAG; \
		$(CONTAINER_CLI) manifest push --all $$LOCAL_MANIFEST_TAG docker://$$REMOTE_MANIFEST_TAG; \
	else \
		$(CONTAINER_CLI) buildx inspect $(BUILDER_NAME) >/dev/null 2>&1 || $(CONTAINER_CLI) buildx create --name $(BUILDER_NAME) --use; \
		$(CONTAINER_CLI) buildx use $(BUILDER_NAME); \
		$(CONTAINER_CLI) buildx inspect --bootstrap >/dev/null; \
		$(CONTAINER_CLI) buildx build \
			--platform $(PLATFORMS) \
			--file $(DOCKERFILE) \
			--build-arg SKIP_UI_BUILD=0 \
			--tag $(IMAGE_NAME):$(IMAGE_TAG) \
			--push \
			.; \
	fi
