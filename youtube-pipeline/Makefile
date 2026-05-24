.PHONY: venv install run dry-run test status logs uninstall

PROJECT_DIR := /opt/youtube-pipeline
PY := venv/bin/python

venv:
	python3 -m venv venv
	venv/bin/pip install --upgrade pip
	venv/bin/pip install -r requirements.txt

install: venv
	@if [ ! -f .env ]; then cp .env.example .env && echo "Created .env — fill in DISCORD_WEBHOOK_URL"; fi
	sudo install -m 644 youtube-pipeline.service /etc/systemd/system/
	sudo install -m 644 youtube-pipeline.timer /etc/systemd/system/
	sudo systemctl daemon-reload
	sudo systemctl enable --now youtube-pipeline.timer
	@echo "Timer installed. Run 'make status' to verify."

run:
	$(PY) main.py

dry-run:
	$(PY) main.py --dry-run

test:
	$(PY) -m pytest tests/ -v

status:
	systemctl status youtube-pipeline.timer --no-pager
	@echo "---"
	journalctl -u youtube-pipeline -n 50 --no-pager

logs:
	journalctl -u youtube-pipeline -f

uninstall:
	sudo systemctl disable --now youtube-pipeline.timer
	sudo rm -f /etc/systemd/system/youtube-pipeline.service
	sudo rm -f /etc/systemd/system/youtube-pipeline.timer
	sudo systemctl daemon-reload
