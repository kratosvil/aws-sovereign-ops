.PHONY: validate fmt security init-example destroy-example

validate:
	@echo "--- Validating all modules ---"
	@for dir in modules/*/; do \
		echo "Validating $$dir"; \
		cd $$dir && terraform init -backend=false -input=false > /dev/null && terraform validate && cd ../..; \
	done

fmt:
	terraform fmt -recursive .

security:
	@echo "--- tfsec ---"
	tfsec .
	@echo "--- checkov ---"
	checkov -d . --quiet

init-example:
	cd examples/sovereign-aiops && terraform init

plan-example:
	cd examples/sovereign-aiops && terraform plan

apply-example:
	cd examples/sovereign-aiops && terraform apply -auto-approve

destroy-example:
	cd examples/sovereign-aiops && terraform destroy -auto-approve

mcp-install:
	cd mcp-server && pip install -r requirements.txt

mcp-run:
	cd mcp-server && python server.py
