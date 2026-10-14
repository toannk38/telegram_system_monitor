# Environment Instructions

## Python Environment

Project dùng **uv** để quản lý Python và dependencies (`pyproject.toml` + `uv.lock`).
Python version được ghim trong `.python-version`.

```bash
uv sync                    # tạo/cập nhật .venv (gồm nhóm dev)
uv run python main.py      # chạy bot
uv run pytest -q           # chạy tests
uv add <package>           # thêm dependency
uv add --dev <package>     # thêm dependency cho dev
```

> Không dùng `pip install` trực tiếp, và không sửa tay `uv.lock`.

## Triển khai

Xem `docs/DEPLOYMENT.md` (systemd hoặc Docker Compose).
