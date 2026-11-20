.PHONY: setup doctor check docker

setup:
	./install.sh

doctor:
	./vlad doctor --profile full

check:
	./scripts/check.sh

docker:
	docker build -f docker/Dockerfile -t vlad:dev .
