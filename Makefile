# Code generation for the node control plane.
#
# The generated stubs are committed, so this only needs to run when
# proto/athena/node/v1/nodehub.proto changes. Requires protoc, protoc-gen-go,
# protoc-gen-go-grpc and the panel venv's grpcio-tools.

PROTO      := proto/athena/node/v1/nodehub.proto
PY_OUT     := backend/app/pb
GO_OUT     := agent/pb
VENV_PY    := /opt/vpn-panel/backend/venv/bin/python
GOBIN      := $(HOME)/go/bin

.PHONY: proto proto-py proto-go agent

proto: proto-py proto-go

proto-py:
	@mkdir -p $(PY_OUT)
	$(VENV_PY) -m grpc_tools.protoc -Iproto \
	    --python_out=$(PY_OUT) --grpc_python_out=$(PY_OUT) \
	    --pyi_out=$(PY_OUT) $(PROTO)
	@# grpc_tools emits package-absolute imports; make them relative so the
	@# stubs work as a normal sub-package of app/.
	@sed -i 's/^from athena\.node\.v1 import /from . import /' $(PY_OUT)/*_pb2_grpc.py
	@sed -i 's/^from athena\.node\.v1 import /from . import /' $(PY_OUT)/*_pb2.py 2>/dev/null || true
	@mv -f $(PY_OUT)/athena/node/v1/*.py  $(PY_OUT)/ 2>/dev/null || true
	@mv -f $(PY_OUT)/athena/node/v1/*.pyi $(PY_OUT)/ 2>/dev/null || true
	@rm -rf $(PY_OUT)/athena
	@echo "python stubs -> $(PY_OUT)"

proto-go:
	@mkdir -p $(GO_OUT)
	PATH="$(GOBIN):/usr/local/go/bin:$$PATH" protoc -Iproto \
	    --go_out=$(GO_OUT) --go_opt=paths=source_relative \
	    --go-grpc_out=$(GO_OUT) --go-grpc_opt=paths=source_relative \
	    $(PROTO)
	@mv -f $(GO_OUT)/athena/node/v1/*.go $(GO_OUT)/ 2>/dev/null || true
	@rm -rf $(GO_OUT)/athena
	@echo "go stubs -> $(GO_OUT)"

agent:
	cd agent && PATH=/usr/local/go/bin:$$PATH CGO_ENABLED=0 \
	    go build -trimpath -ldflags "-s -w -X main.Version=$(VERSION)" \
	    -o ../dist/athena-agent .
	@ls -lh dist/athena-agent
