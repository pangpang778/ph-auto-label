# PH Auto Label 使用文档

## 1. 项目简介

`PH Auto Label` 是一个面向目标检测数据集的标注与训练工具，支持：

- 图片导入与标注管理
- SAM3 开放词汇冷启动预标注（文本提示任意类别，免训练）
- YOLO11 业务模型训练与迭代
- 训练中心任务管理与模型激活
- 视频AI对比测试（YOLO/SAM3 全帧推理，原视频与AI视频对比）
- 数据集导出（YOLO 格式）

### 1.1 目录结构

```text
ph-auto-label/
├── app.py                       # Flask 主服务（标注 / 训练 / 导出 / 视频测试 API）
├── requirements.txt
├── plugins/
│   ├── sam3_service.py          # SAM3 开放词汇检测服务（冷启动预标注）
│   ├── video_inference.py       # 视频全帧离线推理管线（YOLO / SAM3）
│   ├── sam3/models/model.pt     # SAM3 权重（约 3.3GB，不入库，需自行放置）
│   └── yolo11/                  # YOLO 业务模型安装 / 权重目录（运行时生成）
├── templates/                   # index.html 标注台 / video_test.html 视频对比页
├── static/                      # 前端脚本、样式、Font Awesome 字体
│   └── video_compare/video2.mp4 # 视频对比默认素材
├── uploads/                     # 上传图片 / 视频（运行时数据）
└── static/annotations/          # 标注 / 类别 / 训练任务 JSON（运行时数据）
```

技术栈：Flask + OpenCV + Ultralytics YOLO11 + SAM3，前端原生 JS + Font Awesome。

---

## 2. 环境与启动

### 2.1 推荐环境

- Windows 10/11
- Python 3.10（建议使用项目 `.venv`）
- NVIDIA GPU（可选，训练提速；SAM3 强烈建议 GPU）
- `ffmpeg` / `ffprobe`（视频AI对比测试需要，需在 PATH）

### 2.2 安装依赖

在项目根目录执行：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### 2.3 启动服务

```powershell
.\.venv\Scripts\python.exe app.py
```

启动后在浏览器打开：

- [http://127.0.0.1:5000](http://127.0.0.1:5000)

### 2.4 SAM3 模型权重（可选，用于开放词汇预标注 / 视频对比）

SAM3 权重约 3.3GB，**不纳入版本库**。如需使用 SAM3 引擎：

将权重放置为 `plugins/sam3/models/model.pt`（或设置环境变量 `SAM3_MODEL_PATH`）。
启动时若找不到模型，SAM3 相关功能不可用，其余功能不受影响。

### 2.5 Font Awesome 字体

页面图标依赖 `static/fonts/` 下的字体文件（`fa-solid-900` 等），仓库已包含。
若字体缺失，图标会显示成叉号/方框（参见常见问题 5.4）。

---

## 3. 推荐使用流程

当前系统推荐流程为：

1. 导入数据
2. 设置标签
3. SAM3 预标 + 人工兜底（20 张）
4. 训练 v1.0
5. 业务模型 AI 标注 + 复核
6. 增量训练与导出

> 初代训练门槛已设置为：**至少 20 张已标注图片**。

---

## 4. 主要功能说明

## 4.1 导入数据

- 点击顶部 `添加数据集`
- 支持：
  - 图片文件
  - 视频抽帧
  - LabelMe 数据导入

导入后可在左侧列表查看图片与标注状态。

## 4.2 标签管理

右侧 `标签管理` 区域可新增类别（如 `base / frame / mirror / screw`）。

建议先稳定类别命名，再进入大规模预标注，避免后续训练标签漂移。

## 4.3 冷启动（SAM3 开放词汇）

- 在流程第 3 步点击 `去SAM3预标`
- 在 AI 标注弹窗选择：
  - 引擎：`SAM3`
  - 目标类：任意类别（英文，逗号分隔，如 `base,frame,mirror,screw`），SAM3 不需要预先训练
  - 置信度：可从 `0.2~0.5` 试起
- 执行区间批量预标注后，人工逐张复核并保存

目标：把高质量标注样本提升到至少 20 张。

## 4.4 训练中心

- 点击 `训练中心`
- 关注：
  - 已标注图片数
  - 初代模型门槛（20）
  - CUDA 状态
- 点击 `训练初代 v1.0` 启动训练

训练完成后模型会进入模型仓库，并可设为生产模型。

## 4.5 AI 标注（业务模型）

- 在流程第 5 步点击 `去AI标注`
- 选择引擎 `YOLO11`
- 选择业务模型（如 `v1.0.pt / v1.1.pt`）
- 设置置信度后执行标注，再人工复核

## 4.6 导出数据集

- 点击 `导出数据集`
- 配置训练/验证/测试比例与类别
- 导出 YOLO 格式数据集

## 4.7 视频AI对比测试

点击顶部 `视频AI测试` 进入独立页面 [http://127.0.0.1:5000/video-test](http://127.0.0.1:5000/video-test)：

1. 选择/上传视频（默认提供 `video2.mp4`）
2. 选择引擎：
   - `YOLO`：下拉选预训练 `yolo11n.pt`（COCO 80 类）或已训练模型
   - `SAM3`：填写目标类别（英文，开放词汇）
3. 设置置信度，点击 `开始全帧推理`
4. 实时进度（SSE）+ ETA，处理完成后产出带标注的 AI 视频
5. 原视频与 AI 视频**左右并排**，可勾选「同步联动」一起播放/拖动对比

说明：

- 全帧推理：视频每一帧都跑模型，AI 视频帧率/时长与原片一致
- 任务在后台运行，**刷新页面会自动恢复进度**（不会归零丢失）
- YOLO 全帧较快；SAM3 每帧约 1.6s（GPU），长视频较慢

---

## 5. 常见问题

## 5.1 “无可用模型，请去设置里安装模型”

原因：当前没有可用 YOLO11 模型。

处理：

1. 打开 `设置 -> YOLO11 模型管理`
2. 下载预训练模型，或上传你自己的 `.pt` 模型
3. 回到 AI 标注界面重新选择模型

## 5.2 CUDA 显示不可用

先确认两点：

- `nvidia-smi` 能看到 GPU
- 当前项目 `.venv` 中 `torch` 为 CUDA 版本（如 `+cu124` / `+cu126`）

如是 CPU 版 torch（`+cpu`），训练/SAM3 会退回 CPU，速度极慢。

## 5.3 训练后 AI 一个框都没出

常见原因：

- 冷启动样本太少（刚过门槛）
- 置信度阈值过高
- 类别覆盖不均

建议：

- 先继续 SAM3 预标 + 人工兜底，扩充高质量样本
- 降低置信度进行验证（例如 0.2/0.1）
- 再做增量训练

## 5.4 图标显示成叉号或方框

通常是字体文件加载异常。确认 `static/fonts` 下的 Font Awesome 字体（`fa-solid-900.woff2` 等）存在，且 `static/all.min.css` 路径正确。

## 5.5 SAM3 模型未加载 / 视频对比页 SAM3 不可用

确认 `plugins/sam3/models/model.pt` 存在（约 3.3GB，不入库需自行放置），或设置环境变量 `SAM3_MODEL_PATH` 指向权重文件。

---

## 6. 数据文件位置（默认）

- 图片上传目录：`uploads`
- 标注数据：`static/annotations/annotations.json`
- 类别配置：`static/annotations/classes.json`
- 训练任务：`static/annotations/train_jobs.json`
- 模型注册：`static/annotations/model_registry.json`
- 当前激活模型：`static/annotations/active_model.json`
- 视频对比默认素材 / 生成的 AI 视频：`static/video_compare/`

> `uploads/`、`static/annotations/`、`static/video_compare/ai_*.mp4` 为运行时数据，已加入 `.gitignore`。

---

## 7. 维护建议

- 每次改动训练/标注核心逻辑后，先用少量样本做回归验证
- 保持类别命名稳定，避免中途频繁改标签语义
- 模型版本建议按 `v1.0 -> v1.1 -> v1.2` 迭代管理
- 模型权重（`*.pt`）不入库，通过模型管理页或训练产出管理
