# Environment Instructions

## Python Environment

Project này sử dụng **Miniconda** với environment tên `sys_monitor`.

Mọi lệnh Python, pip, hoặc script đều phải chạy trong environment này:

```bash
conda run -n sys_monitor python ...
conda run -n sys_monitor pip install ...
conda run -n sys_monitor python main.py
```

Hoặc activate trước:

```bash
conda activate sys_monitor
```

### Cài dependencies

```bash
conda run -n sys_monitor pip install -r requirements.txt
```

### Chạy project

```bash
conda run -n sys_monitor python main.py
```

### Chạy tests

```bash
conda run -n sys_monitor pytest --tb=short
```

> Không dùng `python` hay `pip` trực tiếp nếu chưa activate env `sys_monitor`.
