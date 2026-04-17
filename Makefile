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
	cd mcp-server && pip3 install -r requirements.txt

mcp-run:
	cd mcp-server && \
	AWS_REGION=us-east-1 \
	BEDROCK_MODEL_ID=us.anthropic.claude-haiku-4-5-20251001-v1:0 \
	HITL_SNS_TOPIC=arn:aws:sns:us-east-1:805778285334:sovereign-aiops-alarms \
	HITL_TOKEN_SECRET=sovereign-aiops-demo-secret \
	PROJECT_NAME=sovereign-aiops \
	AWS_ACCOUNT_ID=805778285334 \
	python3 server.py

run_script:
	@bash _script.sh
