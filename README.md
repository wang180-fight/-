# 低压塑壳断路器远程控制软件（源码 + 版本存档）

本仓库保存 DL/T 645 剩余电流保护断路器远程监控软件的**完整可编辑源码**，
以及历次发布版 exe 存档，便于随时回溯往届版本。

## 目录结构

| 路径 | 说明 |
| --- | --- |
| `duan_LU/` | **源码与运行目录**（在此目录执行 `python main.py`） |
| `duan_LU/dl645_breaker/` | 通信协议、设备层、仿真器、GUI 主界面 |
| `版本/` | 发布版 exe 存档（`低压塑壳断路器远程控制软件.exe`） |
| `duan_LU/低压塑壳断路器远程控制软件.spec` | PyInstaller 打包配置 |
| `doc_text.txt` / `doc_paras.txt` | 通信规约文档提取文本 |
| `_extract.py` / `_dump.py` / `_ctrl_probe.py` 等 | 从 exe 恢复源码时使用的分析脚本（保留备查） |

## 运行

```bash
cd duan_LU
pip install -r requirements.txt
python main.py          # 或双击 start_gui.bat
```

## 打包 exe

```bash
cd duan_LU
python -m PyInstaller 低压塑壳断路器远程控制软件.spec
```

产物位于 `duan_LU/dist/`（`build/`、`dist/` 已由 `.gitignore` 排除，不入库）。

## 版本回溯

```bash
git log --oneline                 # 查看所有版本提交
git show <commit>:duan_LU/dl645_breaker/gui.py   # 查看某版本的某个文件
git checkout <commit> -- <文件>    # 取回某版本的文件（旧版本 exe 同理）
git tag -a v1.0 -m "发布 v1.0"     # 为发布版本打标签，便于长期回溯
```

发布新版本时建议：

```bash
git add -A
git commit -m "feat: ..."
git tag -a v1.1 -m "发布 v1.1"
git push && git push --tags
```
